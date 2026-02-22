#!/usr/bin/env python3
"""
LEELOO Brain — Asyncio orchestrator for the LEELOO music sharing gadget

This is the main entry point. Replaces gadget_main.py with an event-driven
architecture that handles taps, voice, WebSocket messages, LED animations,
and display rendering concurrently.

Usage:
    sudo python3 leeloo_brain.py
"""

import os
import sys
import json
import time
import asyncio
import struct
from enum import Enum, auto
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# Display
from gadget_display import LeelooDisplay, COLORS
from display.frame_animator import FrameAnimator, FrameType
from display.frame_animator import rgb_to_rgb565_fast, write_region_to_framebuffer_rowbyrow

# Data / utilities
from gadget_weather import get_weather
from gadget_data import format_countdown_display, set_next_hang
from text_scroller import truncate_text, center_text_in_box

# Subsystems
from leeloo_led import LEDManager
from leeloo_tap import TapManager
from leeloo_voice import VoiceManager
from leeloo_intent import IntentRouter, build_context
from leeloo_messages import MessageManager
from leeloo_client import LeelooClient

# PIL
from PIL import Image, ImageDraw, ImageFont

# Config
LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, "crew_config.json")
FB_PATH = "/dev/fb1"
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320

# Timing
WEATHER_REFRESH_INTERVAL = 600  # 10 minutes
MUSIC_REFRESH_INTERVAL = 20     # 20 seconds (4,320 API calls/day)
DISPLAY_REFRESH_INTERVAL = 1.0  # 1 second
EXPANDED_HOLD_DURATION = 20.0   # How long expanded frames stay open
RELAY_URL = "wss://leeloobot.xyz/ws"


class UIState(Enum):
    """UI display states"""
    NORMAL = auto()
    EXPANDING = auto()
    EXPANDED = auto()
    COLLAPSING = auto()
    LISTENING = auto()
    PROCESSING = auto()


# =============================================================================
# Helper functions (extracted from gadget_main.py)
# =============================================================================

def load_json(path):
    """Load JSON file safely"""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def rgb_to_rgb565(r, g, b):
    """Convert RGB888 to RGB565"""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def write_to_framebuffer(img, fb_path=FB_PATH):
    """Write PIL image to LCD framebuffer (fast numpy path)"""
    try:
        import numpy as np
        arr = np.array(img)  # (320, 480, 3) uint8
        r = arr[:, :, 0].astype(np.uint16)
        g = arr[:, :, 1].astype(np.uint16)
        b = arr[:, :, 2].astype(np.uint16)
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        with open(fb_path, 'wb') as fb:
            fb.write(rgb565.astype(np.uint16).tobytes())
    except ImportError:
        # Fallback to slow pixel-by-pixel
        with open(fb_path, 'wb') as fb:
            for y in range(320):
                for x in range(480):
                    r, g, b = img.getpixel((x, y))
                    pixel = rgb_to_rgb565(r, g, b)
                    fb.write(struct.pack('H', pixel))


def get_album_art_path(music_data):
    """Get path to album art"""
    if not music_data:
        return None
    if 'album_art_cached' in music_data:
        cached_path = music_data['album_art_cached']
        if os.path.exists(cached_path):
            return cached_path
    return None


# =============================================================================
# LEELOO Brain
# =============================================================================

class LeelooBrain:
    """Main orchestrator — coordinates all LEELOO subsystems"""

    def __init__(self):
        print("[BRAIN] Initializing LEELOO Brain...")

        # UI State
        self.ui_state = UIState.NORMAL
        self.expanded_frame = None       # which FrameType is expanded
        self.message_view_active = False  # for double-tap routing

        # Display
        self.display = LeelooDisplay(preview_mode=False)
        self.box_right = 153  # Will be recalculated after first album art load

        # Subsystems
        _device_cfg = load_json(DEVICE_CONFIG_PATH)
        self.led = LEDManager(num_leds=_device_cfg.get('num_leds', 3))
        self.tap = TapManager(callback=self._on_tap)
        self._led_ambient_state = "idle"  # tracks current ambient to avoid redundant set_ambient calls

        # Load API keys from .env
        self._load_env()
        deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

        # Voice (Phase 2)
        if deepgram_key:
            self.voice = VoiceManager(deepgram_key)
            print(f"[BRAIN] Voice: enabled (Deepgram)")
        else:
            self.voice = None
            print("[BRAIN] Voice: disabled (no DEEPGRAM_API_KEY)")

        # Intent router (Phase 3)
        if anthropic_key:
            self.intent_router = IntentRouter(
                anthropic_key,
                get_context_fn=self._get_intent_context
            )
            print(f"[BRAIN] Intent: enabled (Claude Haiku)")
        else:
            self.intent_router = None
            print("[BRAIN] Intent: disabled (no ANTHROPIC_API_KEY)")

        # Messages (Phase 4)
        self.message_mgr = MessageManager()
        print(f"[BRAIN] Messages: {self.message_mgr.get_total_unread()} unread")

        # WebSocket client (Phase 5)
        self.ws_client = LeelooClient(
            relay_url=RELAY_URL,
            config_path=CREW_CONFIG_PATH
        )
        self._setup_ws_callbacks()
        print(f"[BRAIN] WebSocket: {'configured' if self.ws_client.is_configured() else 'no crew'}")

        # Data caches
        self.device_config = load_json(DEVICE_CONFIG_PATH)
        self.crew_config = load_json(CREW_CONFIG_PATH)
        self.weather_data = None
        self.music_data = None
        self.album_art_path = None
        self.time_data = {}
        self.contacts = self.crew_config.get('members', [])

        # Timing
        self.last_weather_fetch = 0
        self.last_music_fetch = 0

        # Timezone
        tz_name = self.device_config.get('timezone')
        try:
            self.local_tz = ZoneInfo(tz_name) if tz_name else None
        except (KeyError, Exception):
            self.local_tz = None

        # Location
        self.latitude = self.device_config.get('latitude')
        self.longitude = self.device_config.get('longitude')
        self.location_configured = self.latitude is not None and self.longitude is not None

        # Animation
        self._expand_task = None  # Current expand/hold/collapse task
        self._welcome_qr_active = False  # Prevents display loop from overwriting QR

        # Fonts for typewriter
        try:
            self.font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14
            )
            self.font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 18
            )
            self.font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12
            )
        except OSError:
            self.font = ImageFont.load_default()
            self.font_large = self.font
            self.font_small = self.font

        print("[BRAIN] Initialization complete")

    # =========================================================================
    # ENVIRONMENT / CONTEXT
    # =========================================================================

    def _load_env(self):
        """Load API keys from .env file"""
        env_path = os.path.join(LEELOO_HOME, ".env")
        if os.path.exists(env_path):
            print(f"[BRAIN] Loading .env from {env_path}")
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip().strip('"').strip("'")
                            if key and value:
                                os.environ[key] = value
            except Exception as e:
                print(f"[BRAIN] Error loading .env: {e}")
        else:
            print(f"[BRAIN] No .env file at {env_path}")

    def _get_intent_context(self):
        """Build context string for intent router"""
        # Get recent messages if available
        messages = None
        if self.message_mgr:
            try:
                messages = self.message_mgr.get_history_24h()
            except Exception:
                pass

        return build_context(
            weather_data=self.weather_data,
            music_data=self.music_data,
            contacts=self.contacts,
            messages=messages
        )

    # =========================================================================
    # WEBSOCKET CALLBACKS
    # =========================================================================

    def _setup_ws_callbacks(self):
        """Wire up WebSocket event callbacks"""
        self.ws_client.on_message = self._on_ws_message
        self.ws_client.on_reaction = self._on_ws_reaction
        self.ws_client.on_song_push = self._on_ws_song_push
        self.ws_client.on_nudge = self._on_ws_nudge
        self.ws_client.on_hang_propose = self._on_ws_hang_propose
        self.ws_client.on_hang_confirm = self._on_ws_hang_confirm
        self.ws_client.on_member_joined = lambda name: print(f"[BRAIN] {name} came online")
        self.ws_client.on_member_offline = lambda name: print(f"[BRAIN] {name} went offline")
        self.ws_client.on_spotify_auth = self._on_spotify_auth

    def _on_ws_message(self, sender, text):
        """Handle incoming crew message — detect music mentions and show album art"""
        print(f"[BRAIN] Message from {sender}: {text}")
        self.message_mgr.add_message(sender, text)

        # LED notification
        asyncio.ensure_future(self.led.message_received())

        # Show message on display AND check for music mentions
        asyncio.ensure_future(self._handle_message_with_music(sender, text))

    async def _handle_message_with_music(self, sender, text):
        """Show crew message and detect music mentions for album art display"""
        # First, show the message immediately
        lines = [
            (f"from {sender.lower()}", "large", COLORS['lavender']),
            ("", None, None),
            *self._format_display_text(text, COLORS['white']),
        ]
        self._expand_task = asyncio.ensure_future(
            self.expand_frame(FrameType.MESSAGES, lines, duration=EXPANDED_HOLD_DURATION)
        )

        # While message is showing, check for music mentions in background
        try:
            query = await self._detect_music_in_message(text)
            if query:
                print(f"[BRAIN] Music detected in message: '{query}'")
                result = await self._search_spotify_display_only(query, pushed_by=sender)
                if result:
                    print(f"[BRAIN] Album art updated: {result['artist']} - {result['track']}")
                    # Force display refresh to show new album art
                    self.last_music_fetch = 0
                    self._update_music()
                    self._render_normal()
                else:
                    print(f"[BRAIN] No Spotify result for: {query}")
            else:
                print(f"[BRAIN] No music detected in message")
        except Exception as e:
            print(f"[BRAIN] Music detection error: {e}")

    def _on_ws_reaction(self, sender, reaction_type):
        """Handle incoming reaction"""
        print(f"[BRAIN] Reaction from {sender}: {reaction_type}")
        asyncio.ensure_future(self.led.ack())
        # TODO: play reaction animation (love/fire ASCII art)

    def _on_ws_song_push(self, sender, payload):
        """Handle incoming song push — download album art + scancode"""
        artist = payload.get('artist', '')
        track = payload.get('track', '')
        note = payload.get('note', '')
        spotify_uri = payload.get('spotify_uri', '')
        print(f"[BRAIN] Song from {sender}: {artist} - {track}")

        # LED notification
        asyncio.ensure_future(self.led.music_received())

        # Download album art + scancode in background, then refresh display
        async def _fetch_and_display():
            # Use spotify_uri for direct lookup, fall back to text search
            query = spotify_uri if spotify_uri else f"{artist} {track}"
            result = await self._search_spotify_display_only(query, pushed_by=sender)
            if result:
                print(f"[BRAIN] Shared song art updated: {result['artist']} - {result['track']}")
                self.last_music_fetch = 0
                self._update_music()

        asyncio.ensure_future(_fetch_and_display())

        # Show note or album info overlay
        if note:
            async def _show_note():
                await asyncio.sleep(3)
                lines = [
                    (f"from {sender.lower()}", "large", COLORS['lavender']),
                    ("", None, None),
                    *self._format_display_text(note, COLORS['white']),
                ]
                self._expand_task = asyncio.ensure_future(
                    self.expand_frame(FrameType.MESSAGES, lines, duration=EXPANDED_HOLD_DURATION)
                )
            asyncio.ensure_future(_show_note())
        else:
            lines = [
                (f"{sender.lower()} shared", "large", COLORS['green']),
                ("", None, None),
                *self._format_display_text(f"{artist} - {track}", COLORS['white']),
            ]
            self._expand_task = asyncio.ensure_future(
                self.expand_frame(FrameType.ALBUM, lines, duration=15.0)
            )

    def _on_ws_nudge(self, sender):
        """Handle incoming nudge"""
        print(f"[BRAIN] Nudge from {sender}!")
        asyncio.ensure_future(self.led.nudge(duration=30))

    def _on_ws_hang_propose(self, sender, datetime_str, description):
        """Handle incoming hang proposal"""
        print(f"[BRAIN] Hang proposal from {sender}: {datetime_str}")
        lines = [
            (f"{sender.lower()} wants to", "large", COLORS['lavender']),
            ("hang!", "large", COLORS['lavender']),
            ("", None, None),
            *self._format_display_text(f"{datetime_str} {description}".strip(), COLORS['white']),
        ]
        self._expand_task = asyncio.ensure_future(
            self.expand_frame(FrameType.MESSAGES, lines, duration=EXPANDED_HOLD_DURATION)
        )

    def _on_ws_hang_confirm(self, sender):
        """Handle incoming hang confirmation"""
        print(f"[BRAIN] Hang confirmed by {sender}!")
        asyncio.ensure_future(self.led.ack())
        lines = [
            ("hang confirmed!", "large", COLORS['lavender']),
            ("", None, None),
            (f"{sender.lower()} is in", "normal", COLORS['white']),
        ]
        self._expand_task = asyncio.ensure_future(
            self.expand_frame(FrameType.MESSAGES, lines, duration=10.0)
        )

    def _on_spotify_auth(self, tokens):
        """Handle Spotify OAuth tokens received via WebSocket"""
        print(f"[BRAIN] Spotify connected! Triggering music refresh...")
        # Force immediate music update to show currently playing
        self.last_music_fetch = 0
        # Dismiss welcome screen if showing (user just authed)
        if self._expand_task and not self._expand_task.done():
            self._expand_task.cancel()
        # Show confirmation
        asyncio.ensure_future(self._show_spotify_connected())

    async def _show_spotify_connected(self):
        """Brief confirmation that Spotify connected successfully"""
        await asyncio.sleep(1.0)  # Let cancel settle
        lines = [
            ("spotify connected!", "large", COLORS['green']),
            ("", None, None),
            ("your music will now", "normal", COLORS['white']),
            ("show on your LEELOO", "normal", COLORS['white']),
        ]
        self._expand_task = asyncio.create_task(
            self.expand_frame(FrameType.ALBUM, lines, duration=10.0)
        )

    # =========================================================================
    # DATA REFRESH
    # =========================================================================

    def _update_time(self):
        """Update time data"""
        now = datetime.now(tz=self.local_tz) if self.local_tz else datetime.now()
        self.time_data = {
            'time_str': now.strftime('%-I:%M %p'),
            'date_str': now.strftime('%b %-d'),
            'seconds': now.second,
        }

    def _update_weather(self):
        """Refresh weather data if needed"""
        if not self.location_configured:
            return

        now = time.time()
        if self.weather_data is None or (now - self.last_weather_fetch) > WEATHER_REFRESH_INTERVAL:
            try:
                tz_name = self.device_config.get('timezone')
                self.weather_data = get_weather(self.latitude, self.longitude, timezone=tz_name)
                self.last_weather_fetch = now
                print(f"[BRAIN] Weather: {self.weather_data.get('temp_f', '?')}°F")
            except Exception as e:
                print(f"[BRAIN] Weather fetch failed: {e}")

    def _update_music(self):
        """Refresh music data — uses Spotify OAuth if tokens exist, else reads cache file.
        Uses tiered polling: faster when actively playing, slower when idle."""
        # Don't overwrite album art while welcome QR is being displayed
        if self._welcome_qr_active:
            return

        now = time.time()

        # Tiered polling intervals based on playback state
        is_playing = self.music_data and self.music_data.get('is_playing', True)  # Assume playing if unknown
        has_recent_music = self.music_data and (now - self.music_data.get('last_updated', 0)) < 300  # 5 min

        if is_playing:
            interval = MUSIC_REFRESH_INTERVAL  # 20s - actively listening
        elif has_recent_music:
            interval = 60  # 1 min - recently played, might resume
        else:
            interval = 120  # 2 min - idle, check occasionally

        if now - self.last_music_fetch > interval:
            # Check if user has Spotify OAuth tokens for "currently playing"
            spotify_tokens_path = os.path.join(LEELOO_HOME, "spotify_tokens.json")
            if os.path.exists(spotify_tokens_path):
                try:
                    from leeloo_music_manager import update_music_display
                    result = update_music_display()
                    if result:
                        self.music_data = {
                            'artist': result.get('artist', ''),
                            'album': result.get('album', ''),
                            'track': result.get('track', ''),
                            'bpm': result.get('bpm'),
                            'listeners': result.get('listeners'),
                            'pushed_by': result.get('pushed_by'),
                            'spotify_uri': result.get('spotify_uri'),
                            'is_playing': result.get('is_playing', False),
                            'last_updated': now,
                        }
                        self.album_art_path = get_album_art_path(result)
                        self.last_music_fetch = now
                        return
                except Exception as e:
                    print(f"[BRAIN] Spotify currently-playing error: {e}")

            # Fallback: read from current_music.json (shared music, voice-pushed, etc.)
            music_path = os.path.join(LEELOO_HOME, "current_music.json")
            music = load_json(music_path)
            if music:
                self.music_data = {
                    'artist': music.get('artist', ''),
                    'album': music.get('album', ''),
                    'track': music.get('track', ''),
                    'bpm': music.get('bpm'),
                    'listeners': music.get('listeners'),
                    'pushed_by': music.get('pushed_by'),
                    'spotify_uri': music.get('spotify_uri'),
                    'is_playing': music.get('is_playing', False),
                    'last_updated': music.get('timestamp', now),
                }
                self.album_art_path = get_album_art_path(music)
            else:
                self.music_data = None
                self.album_art_path = None
            self.last_music_fetch = now

        # Update ambient LED state based on current playback
        self._sync_ambient_led()

    def _sync_ambient_led(self):
        """Switch ambient LED breathe to match current playback state.
        Only trust is_playing if Spotify OAuth is connected — the JSON cache
        reflects stale state from whenever the file was last written."""
        spotify_tokens_path = os.path.join(LEELOO_HOME, "spotify_tokens.json")
        has_live_spotify = os.path.exists(spotify_tokens_path)
        is_playing = bool(
            has_live_spotify and
            self.music_data and
            self.music_data.get('is_playing', False)
        )
        new_state = "playing" if is_playing else "idle"
        if new_state != self._led_ambient_state:
            self._led_ambient_state = new_state
            asyncio.ensure_future(self.led.set_ambient(new_state))

    # =========================================================================
    # DISPLAY RENDERING
    # =========================================================================

    def _render_normal(self):
        """Render the normal UI to framebuffer"""
        self._update_time()

        img = self.display.render(
            self.weather_data,
            self.time_data,
            self.contacts,
            self.music_data,
            album_art_path=self.album_art_path
        )

        # Calculate box_right from album art position
        # (The display.render already computed this internally)
        write_to_framebuffer(img)

    def _calculate_box_right(self):
        """Calculate box_right from album art position"""
        temp_img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
        temp_draw = ImageDraw.Draw(temp_img)
        self.display.image = temp_img
        self.display.draw = temp_draw
        album_x = self.display.draw_album_art(album_art_path=self.album_art_path)
        return album_x - 10  # 5px gap + 5px padding

    # =========================================================================
    # FRAME EXPANSION (async)
    # =========================================================================

    async def expand_frame(self, frame_type: FrameType, content_lines, duration=EXPANDED_HOLD_DURATION):
        """
        Expand a frame, show content with typewriter, hold, then collapse.

        Args:
            frame_type: Which frame to expand (WEATHER, MESSAGES, ALBUM, etc.)
            content_lines: List of (text, size, color) tuples for typewriter
            duration: How long to hold expanded view (seconds)
        """
        if self.ui_state != UIState.NORMAL:
            # Cancel existing expansion
            if self._expand_task and not self._expand_task.done():
                self._expand_task.cancel()
                try:
                    await self._expand_task
                except asyncio.CancelledError:
                    pass

        self.ui_state = UIState.EXPANDING
        self.expanded_frame = frame_type
        self.message_view_active = (frame_type == FrameType.MESSAGES)

        try:
            # Calculate box_right for animator
            box_right = self._calculate_box_right()
            animator = FrameAnimator(self.display, box_right=box_right, fb_path=FB_PATH)

            # Expand animation
            print(f"[BRAIN] Expanding {frame_type.value}...")
            await animator.async_expand(frame_type)

            # Typewriter content
            self.ui_state = UIState.EXPANDED
            overflow = await self._typewriter(content_lines, box_right)

            # Hold (with scrolling if content overflows)
            if overflow:
                print(f"[BRAIN] Content overflows — scrolling ({overflow['total_height']}px > {overflow['visible_height']}px)")
                # Start scrolling immediately after typewriter finishes
                await self._scroll_content(overflow, duration)
            else:
                print(f"[BRAIN] Holding for {duration}s...")
                await asyncio.sleep(duration)

            # Collapse
            self.ui_state = UIState.COLLAPSING
            print(f"[BRAIN] Collapsing {frame_type.value}...")
            await animator.async_collapse(frame_type)

        except asyncio.CancelledError:
            print(f"[BRAIN] Expansion cancelled")
            raise
        finally:
            # Return to normal
            self.ui_state = UIState.NORMAL
            self.expanded_frame = None
            self.message_view_active = False
            # Force data refresh before re-rendering (picks up new album art, etc.)
            self.last_music_fetch = 0
            self._update_music()
            self._render_normal()

    async def _typewriter(self, content_lines, box_right):
        """Typewriter effect — writes text character by character to framebuffer.
        If text overflows the frame, stores the full rendered content for scrolling.
        Returns the full content image and total height if overflow occurred."""
        content_x = 7 + 10
        content_y = 16 + 25
        line_height = 22
        text_region_width = box_right - 7 - 25
        char_delay = 0.025
        line_delay = 0.08

        # Max Y before we'd write outside the frame (bottom border ~row 304)
        max_y = SCREEN_HEIGHT - 20
        visible_height = max_y - content_y

        # Pre-render ALL lines to an off-screen image to know total height
        total_content_height = 0
        for line_text, font_size, line_color in content_lines:
            if line_text == "":
                total_content_height += line_height // 2
            else:
                total_content_height += line_height

        has_overflow = total_content_height > visible_height

        # Create full content image (may be taller than visible area)
        full_content_img = Image.new('RGB', (text_region_width, max(total_content_height, visible_height)), COLORS['bg'])
        full_draw = ImageDraw.Draw(full_content_img)

        y_pos = content_y

        try:
            fb = open(FB_PATH, 'r+b')
        except (PermissionError, FileNotFoundError):
            print("[BRAIN] Cannot open framebuffer for typewriter")
            return None

        try:
            for line_text, font_size, line_color in content_lines:
                if line_text == "":
                    y_pos += line_height // 2
                    continue

                # Select font
                if font_size == "large":
                    line_font = self.font_large
                elif font_size == "small":
                    line_font = self.font_small
                else:
                    line_font = self.font

                current_text = ""
                line_is_visible = y_pos + line_height <= max_y
                for char in line_text:
                    current_text += char

                    if line_is_visible:
                        # Create line image and write to framebuffer
                        line_img = Image.new('RGB', (text_region_width, line_height), COLORS['bg'])
                        line_draw = ImageDraw.Draw(line_img)
                        line_draw.text((0, 2), current_text, font=line_font, fill=line_color)

                        rgb565 = rgb_to_rgb565_fast(line_img)
                        height, width = rgb565.shape
                        for row in range(height):
                            if y_pos + row < SCREEN_HEIGHT:
                                offset = ((y_pos + row) * SCREEN_WIDTH + content_x) * 2
                                fb.seek(offset)
                                fb.write(rgb565[row, :].tobytes())

                        await asyncio.sleep(char_delay)
                    # Off-screen lines: no delay, just accumulate text for the content image

                # Also render this line into the full content image
                render_y = y_pos - content_y  # Position in content image
                if render_y >= 0 and render_y < total_content_height:
                    full_draw.text((0, render_y + 2), line_text, font=line_font, fill=line_color)

                y_pos += line_height
                if line_is_visible:
                    await asyncio.sleep(line_delay)
                else:
                    await asyncio.sleep(0)  # yield to event loop without visible delay
        finally:
            fb.close()

        if has_overflow:
            return {
                'image': full_content_img,
                'total_height': total_content_height,
                'visible_height': visible_height,
                'content_x': content_x,
                'content_y': content_y,
            }
        return None

    async def _scroll_content(self, overflow, remaining_duration):
        """Scroll overflowed content upward through the visible area."""
        import numpy as np

        content_img = overflow['image']
        total_h = overflow['total_height']
        visible_h = overflow['visible_height']
        content_x = overflow['content_x']
        content_y = overflow['content_y']

        max_scroll = total_h - visible_h
        if max_scroll <= 0:
            await asyncio.sleep(remaining_duration)
            return

        # Scroll speed: ~1 pixel per frame at ~15fps = ~15px/sec
        scroll_speed = 1  # pixels per frame
        frame_delay = 0.07  # ~14fps

        try:
            fb = open(FB_PATH, 'r+b')
        except (PermissionError, FileNotFoundError):
            await asyncio.sleep(remaining_duration)
            return

        try:
            scroll_offset = 0
            while scroll_offset < max_scroll:
                # Crop visible region from full content
                crop = content_img.crop((0, scroll_offset, content_img.width, scroll_offset + visible_h))

                # Convert to RGB565 and write to framebuffer
                arr = np.array(crop)
                r = arr[:, :, 0].astype(np.uint16)
                g = arr[:, :, 1].astype(np.uint16)
                b = arr[:, :, 2].astype(np.uint16)
                rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                rgb565_bytes = rgb565.astype(np.uint16)

                rows = rgb565_bytes.shape[0]
                for row in range(rows):
                    screen_y = content_y + row
                    if screen_y < SCREEN_HEIGHT:
                        offset = (screen_y * SCREEN_WIDTH + content_x) * 2
                        fb.seek(offset)
                        fb.write(rgb565_bytes[row, :].tobytes())

                scroll_offset += scroll_speed
                await asyncio.sleep(frame_delay)

            # Hold at the bottom for remaining time
            elapsed_scroll = (max_scroll / scroll_speed) * frame_delay
            hold_time = max(remaining_duration - elapsed_scroll - 2.0, 3.0)
            await asyncio.sleep(hold_time)

        finally:
            fb.close()

    # =========================================================================
    # TAP HANDLING
    # =========================================================================

    async def _on_tap(self, tap_type):
        """Handle tap events from TapManager"""
        print(f"[BRAIN] Tap: {tap_type}")

        if tap_type == 'single_tap':
            await self._handle_single_tap()
        elif tap_type == 'double_tap':
            await self._handle_double_tap()
        elif tap_type == 'triple_tap':
            await self._handle_triple_tap()

    async def _handle_single_tap(self):
        """Single tap — activate voice (or cancel current expansion)"""
        if self.ui_state in (UIState.EXPANDED, UIState.EXPANDING):
            # Cancel current expansion — tap to dismiss
            if self._expand_task and not self._expand_task.done():
                self._expand_task.cancel()
            return

        # Ignore taps while already listening or processing — concurrent
        # voice sessions spawn competing arecord processes that corrupt
        # the I2S state and kill all subsequent recordings until restart.
        if self.ui_state in (UIState.LISTENING, UIState.PROCESSING):
            return

        # LED acknowledgment
        await self.led.ack()

        # Voice interaction
        if self.voice and self.intent_router:
            await self._voice_interaction()
        elif self.voice and not self.intent_router:
            # Voice only mode — just transcribe, no intent routing
            print("[BRAIN] Voice-only mode (no intent router)")
            self.ui_state = UIState.LISTENING
            await self.led.listening()
            transcript = await self.voice.record_and_transcribe()
            await self.led.off()
            self.ui_state = UIState.NORMAL
            if transcript:
                self._expand_task = asyncio.create_task(
                    self.expand_frame(
                        FrameType.WEATHER,
                        [
                            ("you said:", "large", COLORS['tan']),
                            ("", None, None),
                            *self._format_display_text(transcript, COLORS['white']),
                        ],
                        duration=10.0
                    )
                )
        else:
            # No voice — demo expand weather
            print("[BRAIN] Voice not available — demo weather expand")
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.WEATHER,
                    [
                        ("weather report", "large", COLORS['tan']),
                        ("", None, None),
                        (f"{self.weather_data.get('temp_f', '??')}F" if self.weather_data else "no data", "large", COLORS['white']),
                        ("", None, None),
                        ("tap to talk coming soon", "normal", COLORS['tan']),
                    ],
                    duration=10.0
                )
            )

    async def _handle_double_tap(self):
        """Double tap — nudge (normal) or heart reaction (message view)"""
        if self.message_view_active:
            # Heart reaction
            print("[BRAIN] Double tap → heart reaction")
            await self.led.ack()
            # Phase 5: send reaction via WebSocket
            if self.ws_client:
                await self.ws_client.send_reaction('love')
        else:
            # Nudge — cyan LED for 30s
            print("[BRAIN] Double tap → nudge")
            asyncio.create_task(self.led.nudge(duration=30))
            # Phase 5: send nudge via WebSocket
            if self.ws_client:
                await self.ws_client.send_nudge()

    async def _handle_triple_tap(self):
        """Triple tap — fire reaction (only during message view)"""
        if self.message_view_active:
            print("[BRAIN] Triple tap → fire reaction")
            await self.led.ack()
            if self.ws_client:
                await self.ws_client.send_reaction('fire')
        else:
            print("[BRAIN] Triple tap — no action outside message view")

    # =========================================================================
    # VOICE INTERACTION
    # =========================================================================

    async def _voice_interaction(self):
        """Full voice interaction: listen → transcribe → route → act"""
        try:
            # Ensure ambient LED is fully off before any audio — WS2812B DMA and
            # INMP441 I2S DMA conflict on Pi Zero if both run simultaneously.
            await self.led._cancel_ambient()

            # Listen
            self.ui_state = UIState.LISTENING
            await self.led.listening()
            print("[BRAIN] Listening...")

            transcript = await self.voice.record_and_transcribe()
            print(f"[BRAIN] Transcript: '{transcript}'")

            if not transcript:
                await self.led.error()
                self.ui_state = UIState.NORMAL
                return

            # Process
            self.ui_state = UIState.PROCESSING
            asyncio.create_task(self.led.processing())
            print("[BRAIN] Processing intent...")

            # Force-refresh music data so Claude has the latest context
            self.last_music_fetch = 0
            self._update_music()

            intent = await self.intent_router.route(transcript)
            print(f"[BRAIN] Intent: {intent.action}")

            await self.led.off()

            # Execute
            await self._execute_intent(intent)

        except Exception as e:
            print(f"[BRAIN] Voice error: {e}")
            import traceback
            traceback.print_exc()
            await self.led.error()
            self.ui_state = UIState.NORMAL

    async def _execute_intent(self, intent):
        """Execute a classified intent"""
        # Phase 3/4 implementation
        action = intent.action

        if action == "WEATHER_EXPAND":
            # Build weather text + rain visualization
            text_lines = self._format_display_text(intent.response_text, COLORS['tan'])
            rain_viz = self._build_rain_viz(self.weather_data)

            # Combine: text + blank line + rain viz
            content = text_lines + [("", None, None)] + rain_viz

            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.WEATHER,
                    content,
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "ALBUM_INFO":
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.ALBUM,
                    self._format_display_text(intent.response_text, COLORS['green']),
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "MESSAGE_SEND":
            msg = intent.params.get('message', '')
            if self.ws_client and self.ws_client.connected and msg:
                await self.ws_client.send_message(msg)
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.MESSAGES,
                    [
                        ("message sent", "large", COLORS['lavender']),
                        ("", None, None),
                        (msg, "normal", COLORS['white']),
                    ],
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "MESSAGE_READOUT":
            if self.message_mgr:
                messages = self.message_mgr.get_history_24h()
                lines = [("messages", "large", COLORS['lavender']), ("", None, None)]
                for m in messages:
                    lines.append((f"{m['text']} - {m['sender']}", "normal", COLORS['white']))
                    lines.append(("", None, None))
                if not messages:
                    lines.append(("no messages", "normal", COLORS['white']))
                self._expand_task = asyncio.create_task(
                    self.expand_frame(FrameType.MESSAGES, lines, duration=EXPANDED_HOLD_DURATION)
                )
                self.message_mgr.mark_all_read()
        elif action == "NUDGE":
            # Send nudge — cyan LED
            print("[BRAIN] Intent: nudge")
            asyncio.create_task(self.led.nudge(duration=30))
            if self.ws_client and self.ws_client.connected:
                await self.ws_client.send_nudge()
        elif action == "SONG_PUSH":
            if intent.params.get('current') and self.music_data:
                # Push currently playing song
                print(f"[BRAIN] Push current song: {self.music_data.get('track')}")
                if self.ws_client and self.ws_client.connected and self.music_data.get('spotify_uri'):
                    await self.ws_client.push_song(
                        self.music_data['spotify_uri'],
                        self.music_data.get('artist', ''),
                        self.music_data.get('track', ''),
                        self.music_data.get('album', '')
                    )
                self._expand_task = asyncio.create_task(
                    self.expand_frame(
                        FrameType.ALBUM,
                        [
                            ("song shared!", "large", COLORS['green']),
                            ("", None, None),
                            *self._format_display_text(
                                f"{self.music_data.get('artist', '')} - {self.music_data.get('track', '')}",
                                COLORS['white']
                            ),
                        ],
                        duration=10.0
                    )
                )
            else:
                # Search Spotify and push
                query = intent.params.get('query', '')
                print(f"[BRAIN] Song push search: '{query}'")
                result = await self._search_and_push_song(query)
                if result:
                    self._expand_task = asyncio.create_task(
                        self.expand_frame(
                            FrameType.ALBUM,
                            [
                                ("song shared!", "large", COLORS['green']),
                                ("", None, None),
                                *self._format_display_text(
                                    f"{result.get('artist', '')} - {result.get('track', '')}",
                                    COLORS['white']
                                ),
                            ],
                            duration=10.0
                        )
                    )
                else:
                    self._expand_task = asyncio.create_task(
                        self.expand_frame(
                            FrameType.ALBUM,
                            [
                                ("couldn't find it", "large", COLORS['green']),
                                ("", None, None),
                                *self._format_display_text(f"searched for: {query}", COLORS['white']),
                                ("", None, None),
                                *self._format_display_text("wanna try a different song?", COLORS['white']),
                            ],
                            duration=10.0
                        )
                    )
        elif action == "HANG_PROPOSE":
            dt_str = intent.params.get('datetime', '')
            desc = intent.params.get('description', '')
            print(f"[BRAIN] Hang propose: {dt_str} {desc}")
            # Phase 5: send via WebSocket
            if self.ws_client and dt_str:
                pass  # await self.ws_client.send_hang_propose(dt_str)
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.MESSAGES,
                    [
                        ("hang proposed", "large", COLORS['lavender']),
                        ("", None, None),
                        *self._format_display_text(intent.response_text, COLORS['white']),
                    ],
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "HANG_CONFIRM":
            print("[BRAIN] Hang confirmed")
            await self.led.ack()
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.MESSAGES,
                    [
                        ("hang confirmed!", "large", COLORS['lavender']),
                        ("", None, None),
                        *self._format_display_text(intent.response_text, COLORS['white']),
                    ],
                    duration=10.0
                )
            )
        elif action == "TELEGRAM_SETUP":
            # Show Telegram setup instructions
            print("[BRAIN] Telegram setup requested")
            crew_code = self.crew_config.get('invite_code',
                        self.crew_config.get('crew_code', ''))
            lines = [
                ("telegram setup", "large", COLORS['lavender']),
                ("", None, None),
                ("message @Leeloo2259_bot", "normal", COLORS['white']),
                ("on Telegram and send /start", "normal", COLORS['white']),
                ("", None, None),
            ]
            if crew_code:
                lines.append((f"your crew code: {crew_code}", "normal", COLORS['green']))
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.MESSAGES,
                    lines,
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "TIME_QUERY":
            # Generate Tolkien-style cosmic time description
            print("[BRAIN] Time query — generating cosmic description")
            time_text = await self._generate_cosmic_time()
            self._expand_task = asyncio.create_task(
                self.expand_frame(
                    FrameType.TIME,
                    self._format_display_text(time_text, COLORS['purple']),
                    duration=EXPANDED_HOLD_DURATION
                )
            )
        elif action == "UNKNOWN":
            # Show the response text if any
            if intent.response_text:
                self._expand_task = asyncio.create_task(
                    self.expand_frame(
                        FrameType.WEATHER,
                        self._format_display_text(intent.response_text, COLORS['tan']),
                        duration=10.0
                    )
                )
        else:
            print(f"[BRAIN] Unknown intent: {action}")

    # =========================================================================
    # MUSIC DETECTION IN MESSAGES
    # =========================================================================

    async def _detect_music_in_message(self, text):
        """Detect if a crew message mentions a song/artist/album.
        Returns a Spotify search query string, or None if no music detected.

        Handles three cases:
        1. Spotify links (open.spotify.com/track/...) — extract URI directly
        2. Spotify URIs (spotify:track:...) — use directly
        3. Natural language mentions — use Claude Haiku to extract search query
        """
        import re

        # Case 1: Spotify track link  (e.g. https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC)
        link_match = re.search(r'open\.spotify\.com/track/([a-zA-Z0-9]+)', text)
        if link_match:
            track_id = link_match.group(1)
            print(f"[BRAIN] Spotify link detected, track ID: {track_id}")
            return f"spotify:track:{track_id}"

        # Case 2: Spotify URI (e.g. spotify:track:4uLU6hMCjMI75M1A2tKUQC)
        uri_match = re.search(r'(spotify:track:[a-zA-Z0-9]+)', text)
        if uri_match:
            print(f"[BRAIN] Spotify URI detected: {uri_match.group(1)}")
            return uri_match.group(1)

        # Case 3: Natural language — use Claude Haiku to detect music mentions
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            print("[BRAIN] No Anthropic key for music detection")
            return None

        try:
            loop = asyncio.get_event_loop()

            def _classify():
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_key)
                resp = client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": text}],
                    system="""You detect music mentions in casual messages between friends.
If the message mentions a specific song, artist, album, or track — extract the best Spotify search query.

Respond with ONLY a JSON object (no markdown, no code fences):
{"music": true, "query": "artist name song title"}
OR
{"music": false}

Examples:
- "Yall have to listen to that new Fred again song Feisty" → {"music": true, "query": "Fred again Feisty"}
- "check out Blonde by Frank Ocean" → {"music": true, "query": "Frank Ocean Blonde"}
- "hey whats up tonight" → {"music": false}
- "that Kendrick album is fire" → {"music": true, "query": "Kendrick Lamar"}
- "been bumping Tame Impala all day" → {"music": true, "query": "Tame Impala"}"""
                )
                return resp.content[0].text.strip()

            raw = await loop.run_in_executor(None, _classify)
            print(f"[BRAIN] Music detection result: {raw}")

            # Parse JSON response
            result = json.loads(raw)
            if result.get('music') and result.get('query'):
                return result['query']
            return None

        except Exception as e:
            print(f"[BRAIN] Music detection classify error: {e}")
            return None

    async def _search_spotify_display_only(self, query, pushed_by=None):
        """Search Spotify and update album art display WITHOUT pushing to crew.
        Used for incoming messages that mention music.

        If query is a spotify:track: URI, fetches track directly by ID.
        Otherwise searches by text query.
        Returns the music result dict, or None on failure."""
        if not query:
            return None

        try:
            import requests
            import base64
            from leeloo_music_manager import (
                SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
                SPOTIFY_API_BASE, SPOTIFY_TOKEN_URL,
                ALBUM_ART_DIR, CURRENT_MUSIC_FILE
            )
            from leeloo_album_art import download_and_create_album_art

            loop = asyncio.get_event_loop()
            is_uri = query.startswith('spotify:track:')

            def _do_search():
                auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
                auth_b64 = base64.b64encode(auth_str.encode()).decode()

                token_resp = requests.post(
                    SPOTIFY_TOKEN_URL,
                    data={'grant_type': 'client_credentials'},
                    headers={
                        'Authorization': f'Basic {auth_b64}',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    timeout=5
                )
                if token_resp.status_code != 200:
                    print(f"[BRAIN] Spotify token error: {token_resp.status_code}")
                    return None

                cc_token = token_resp.json()['access_token']
                headers = {"Authorization": f"Bearer {cc_token}"}

                if is_uri:
                    # Direct track lookup by ID
                    track_id = query.split(':')[-1]
                    track_resp = requests.get(
                        f"{SPOTIFY_API_BASE}/tracks/{track_id}",
                        headers=headers,
                        timeout=5
                    )
                    if track_resp.status_code != 200:
                        print(f"[BRAIN] Spotify track lookup error: {track_resp.status_code}")
                        return None
                    track = track_resp.json()
                else:
                    # Text search
                    search_resp = requests.get(
                        f"{SPOTIFY_API_BASE}/search",
                        params={"q": query, "type": "track", "limit": 5},
                        headers=headers,
                        timeout=5
                    )
                    if search_resp.status_code != 200:
                        print(f"[BRAIN] Spotify search error: {search_resp.status_code}")
                        return None

                    tracks = search_resp.json().get('tracks', {}).get('items', [])
                    if not tracks:
                        print(f"[BRAIN] No tracks found for: {query}")
                        return None

                    # Bigram validation (same as _search_and_push_song)
                    import re as _re
                    _ignore = {'the', 'a', 'an', 'by', 'and', 'or', 'with', 'to', 'from'}
                    _qwords = [w for w in query.lower().split() if w not in _ignore and len(w) > 2]

                    best_track = None
                    best_score = (0, 0)
                    for t in tracks:
                        a = t['artists'][0]['name'].lower() if t.get('artists') else ''
                        n = t['name'].lower()
                        combined = f"{a} {n}"
                        rwords = set(_re.findall(r'\w+', combined))
                        whits = sum(1 for w in _qwords if w in rwords)
                        bhits = 0
                        if len(_qwords) >= 2:
                            for i in range(len(_qwords) - 1):
                                if f"{_qwords[i]} {_qwords[i+1]}" in combined:
                                    bhits += 1
                        s = (bhits, whits)
                        if s > best_score:
                            best_score = s
                            best_track = t

                    if _qwords:
                        if len(_qwords) >= 2 and best_score[0] == 0:
                            print(f"[BRAIN] Poor match for '{query}': no bigram match in top 5")
                            return None
                        elif len(_qwords) == 1 and best_score[1] == 0:
                            print(f"[BRAIN] Poor match for '{query}': word not found in top 5")
                            return None

                    track = best_track

                artist = track['artists'][0]['name'] if track.get('artists') else 'Unknown'
                track_name = track['name']
                album = track.get('album', {}).get('name', '')
                spotify_uri = track['uri']
                album_art_url = track.get('album', {}).get('images', [{}])[0].get('url', '')

                print(f"[BRAIN] Found: {artist} - {track_name} ({album})")

                # Build scancode URL
                from urllib.parse import quote
                encoded_uri = quote(spotify_uri, safe=':')
                scancode_url = f"https://scannables.scdn.co/uri/plain/png/1A1D2E/white/280/{encoded_uri}"

                # Clear stale cache
                from leeloo_album_art import get_album_art_path as _get_art_path
                stale_path = _get_art_path(spotify_uri, ALBUM_ART_DIR)
                if os.path.exists(stale_path):
                    os.remove(stale_path)

                # Download album art + scancode
                album_art_cached = download_and_create_album_art(
                    album_art_url,
                    spotify_uri,
                    ALBUM_ART_DIR,
                    source='shared',
                    scancode_url=scancode_url
                )

                # Write to current_music.json
                import time as _time
                result = {
                    'artist': artist,
                    'track': track_name,
                    'album': album,
                    'spotify_uri': spotify_uri,
                    'album_art_url': album_art_url,
                    'album_art_cached': album_art_cached,
                    'source': 'shared',
                    'pushed_by': pushed_by or 'crew',
                    'timestamp': _time.time(),
                }

                with open(CURRENT_MUSIC_FILE, 'w') as f:
                    json.dump(result, f, indent=2)

                return result

            result = await loop.run_in_executor(None, _do_search)

            if result:
                self.last_music_fetch = 0
                return result

        except Exception as e:
            print(f"[BRAIN] Spotify display search error: {e}")
            import traceback
            traceback.print_exc()

        return None

    async def _generate_cosmic_time(self):
        """Generate a Tolkien-style cosmic time description using Claude Haiku"""
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            # Get current date/time in user's timezone
            tz_name = self.device_config.get('timezone', 'America/New_York')
            now = datetime.now(ZoneInfo(tz_name))

            # Get location for plant/animal specificity
            lat = self.latitude or 35.91
            lon = self.longitude or -79.08
            zip_code = self.device_config.get('zip_code', '27510')

            # Build prompt for Haiku
            prompt = f"""Write a 65-word, Tolkien-style paragraph describing the current date as a day in its present season, naming the Sun's zodiac sign and the current Moon phase, and setting it between the fading Age of Pisces and the coming Age of Aquarius. Include one vivid, locally accurate example of animal behavior and one of plant life (bud, flower, or root) that are actively changing at this place and time, with a hint of scientific wit.

Current date: {now.strftime('%B %d, %Y')}
Current time: {now.strftime('%I:%M %p')}
Day of week: {now.strftime('%A')}
Location: ZIP {zip_code} (lat {lat:.2f}, lon {lon:.2f})

Write ONLY the 65-word paragraph, no preamble or explanation."""

            # Call Claude Haiku
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.intent_router.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=200,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
            )

            result = response.content[0].text.strip()
            print(f"[BRAIN] Cosmic time: {result[:80]}...")
            return result

        except Exception as e:
            print(f"[BRAIN] Cosmic time error: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to simple time
            now = datetime.now(ZoneInfo(tz_name))
            return f"it is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}"

    async def _search_and_push_song(self, query):
        """Search Spotify for a song, update display, and push to crew.
        Returns the music result dict, or None on failure."""
        if not query:
            return None

        try:
            import requests
            import base64
            from leeloo_music_manager import (
                SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
                SPOTIFY_API_BASE, SPOTIFY_TOKEN_URL,
                ALBUM_ART_DIR, CURRENT_MUSIC_FILE
            )
            from leeloo_album_art import download_and_create_album_art

            # Get client credentials token
            loop = asyncio.get_event_loop()

            def _do_search():
                auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
                auth_b64 = base64.b64encode(auth_str.encode()).decode()

                token_resp = requests.post(
                    SPOTIFY_TOKEN_URL,
                    data={'grant_type': 'client_credentials'},
                    headers={
                        'Authorization': f'Basic {auth_b64}',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    timeout=5
                )
                if token_resp.status_code != 200:
                    print(f"[BRAIN] Spotify token error: {token_resp.status_code}")
                    return None

                cc_token = token_resp.json()['access_token']
                headers = {"Authorization": f"Bearer {cc_token}"}

                # Search for track
                search_resp = requests.get(
                    f"{SPOTIFY_API_BASE}/search",
                    params={"q": query, "type": "track", "limit": 5},
                    headers=headers,
                    timeout=5
                )

                if search_resp.status_code != 200:
                    print(f"[BRAIN] Spotify search error: {search_resp.status_code}")
                    return None

                tracks = search_resp.json().get('tracks', {}).get('items', [])
                if not tracks:
                    print(f"[BRAIN] No tracks found for: {query}")
                    return None

                # Validate results — require contiguous word pairs (bigrams)
                # from the query to appear in artist+track. This prevents
                # "bed michael jackson" matching "Michael Perry...Long Beds...Jackson"
                import re
                ignore_words = {'the', 'a', 'an', 'by', 'and', 'or', 'with', 'to', 'from'}
                query_words = [w for w in query.lower().split() if w not in ignore_words and len(w) > 2]

                def _score_result(t):
                    """Score a Spotify result against query words.
                    Returns (bigram_matches, word_matches) tuple."""
                    a = t['artists'][0]['name'].lower() if t.get('artists') else ''
                    n = t['name'].lower()
                    combined = f"{a} {n}"
                    result_words = set(re.findall(r'\w+', combined))

                    # Count whole-word matches
                    word_hits = sum(1 for w in query_words if w in result_words)

                    # Count bigram (contiguous pair) matches
                    bigram_hits = 0
                    if len(query_words) >= 2:
                        for i in range(len(query_words) - 1):
                            bigram = f"{query_words[i]} {query_words[i+1]}"
                            if bigram in combined:
                                bigram_hits += 1

                    return (bigram_hits, word_hits)

                # Score all results and pick the best
                best_track = None
                best_score = (0, 0)
                for t in tracks:
                    score = _score_result(t)
                    if score > best_score:
                        best_score = score
                        best_track = t

                # Guard: no tracks returned at all
                if best_track is None:
                    print(f"[BRAIN] No tracks found for '{query}'")
                    return None

                # Validation: for 2+ word queries, require at least one bigram match.
                # For single-word queries, require the word to match.
                if query_words:
                    if len(query_words) >= 2 and best_score[0] == 0:
                        a = best_track['artists'][0]['name'] if best_track.get('artists') else '?'
                        n = best_track['name']
                        print(f"[BRAIN] Poor match for '{query}': got '{a} - {n}'")
                        print(f"[BRAIN] No contiguous word pairs matched (bigram check failed)")
                        return None
                    elif len(query_words) == 1 and best_score[1] == 0:
                        a = best_track['artists'][0]['name'] if best_track.get('artists') else '?'
                        n = best_track['name']
                        print(f"[BRAIN] Poor match for '{query}': got '{a} - {n}'")
                        return None

                track = best_track
                artist = track['artists'][0]['name'] if track.get('artists') else 'Unknown'
                track_name = track['name']
                album = track.get('album', {}).get('name', '')
                spotify_uri = track['uri']
                album_art_url = track.get('album', {}).get('images', [{}])[0].get('url', '')

                print(f"[BRAIN] Found: {artist} - {track_name} ({album})")

                # Build scancode URL from Spotify URI
                from urllib.parse import quote
                encoded_uri = quote(spotify_uri, safe=':')
                scancode_url = f"https://scannables.scdn.co/uri/plain/png/1A1D2E/white/280/{encoded_uri}"

                # Clear stale cache (may have been cached without scancode)
                from leeloo_album_art import get_album_art_path as _get_art_path
                stale_path = _get_art_path(spotify_uri, ALBUM_ART_DIR)
                if os.path.exists(stale_path):
                    os.remove(stale_path)
                    print(f"[BRAIN] Cleared stale album art cache: {stale_path}")

                # Download album art + scancode
                album_art_cached = download_and_create_album_art(
                    album_art_url,
                    spotify_uri,
                    ALBUM_ART_DIR,
                    source='shared',
                    scancode_url=scancode_url
                )

                # Write to current_music.json
                import time as _time
                result = {
                    'artist': artist,
                    'track': track_name,
                    'album': album,
                    'spotify_uri': spotify_uri,
                    'album_art_url': album_art_url,
                    'album_art_cached': album_art_cached,
                    'source': 'shared',
                    'pushed_by': 'You',
                    'timestamp': _time.time(),
                }

                with open(CURRENT_MUSIC_FILE, 'w') as f:
                    json.dump(result, f, indent=2)

                return result

            result = await loop.run_in_executor(None, _do_search)

            if result:
                # Force music refresh on next display cycle
                self.last_music_fetch = 0

                # Push to crew via WebSocket
                if self.ws_client and self.ws_client.connected:
                    await self.ws_client.push_song(
                        result['spotify_uri'],
                        result['artist'],
                        result['track'],
                        result['album'],
                        album_art_url=result.get('album_art_url', '')
                    )
                    print(f"[BRAIN] Song pushed to crew: {result['artist']} - {result['track']}")

                return result

        except Exception as e:
            print(f"[BRAIN] Song search error: {e}")
            import traceback
            traceback.print_exc()

        return None

    def _format_display_text(self, text, color):
        """Format a block of text into typewriter content lines"""
        if not text:
            return [("no response", "normal", color)]

        lines = []
        # Word wrap at ~20 chars for the small display
        words = text.split()
        current_line = ""
        for word in words:
            test = current_line + (" " if current_line else "") + word
            if len(test) > 22:
                if current_line:
                    lines.append((current_line, "normal", COLORS['white']))
                current_line = word
            else:
                current_line = test
        if current_line:
            lines.append((current_line, "normal", COLORS['white']))

        return lines

    def _build_rain_viz(self, weather_data):
        """
        Build ASCII rain visualization similar to iOS weather
        Returns list of text lines showing rain intensity over next hour

        Format:
        ▃▅█▆▃▂  ▂▃▅▃▂   ▂▃▂
        Now 10m 20m 30m 40m
        """
        import random

        # Extract rain data
        rain_24h = weather_data.get('rain_24h_inches', 0)
        current_precip = weather_data.get('current_precip_inches', 0)
        is_raining = weather_data.get('is_raining', False)

        # Unicode block chars from low to high
        blocks = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']

        # Generate 24 bars (representing ~2.5 min intervals over 60 min)
        bars = []
        for i in range(24):
            # If it's raining now, high intensity for first few bars, then random taper
            if is_raining and i < 8:
                intensity = random.randint(5, 7)  # High rain
            elif is_raining and i < 16:
                intensity = random.randint(2, 5)  # Tapering off
            elif rain_24h > 0.5:  # Forecast shows significant rain
                intensity = random.randint(3, 6)  # Variable rain
            elif rain_24h > 0.1:  # Light rain forecasted
                intensity = random.randint(1, 3)  # Occasional drops
            else:
                intensity = 0  # No rain

            bars.append(blocks[intensity])

        # Build the visualization
        bar_line = ''.join(bars)
        timeline = "Now 10m 20m 30m 40m"

        # Return as formatted lines
        viz_lines = [
            (bar_line, "normal", COLORS['lavender']),
            (timeline, "normal", COLORS['white'])
        ]

        return viz_lines

    # =========================================================================
    # MAIN LOOPS
    # =========================================================================

    async def _display_loop(self):
        """Main display refresh loop — 1fps when in NORMAL state"""
        print("[BRAIN] Display loop started")
        loop = asyncio.get_event_loop()
        while True:
            try:
                if self.ui_state == UIState.NORMAL:
                    # Run blocking display work in executor so tap polling isn't blocked
                    await loop.run_in_executor(None, self._display_tick)
            except Exception as e:
                print(f"[BRAIN] Display error: {e}")

            await asyncio.sleep(DISPLAY_REFRESH_INTERVAL)

    def _display_tick(self):
        """Synchronous display update — runs in thread executor"""
        if self._welcome_qr_active:
            return  # Don't overwrite QR during welcome screen
        self._update_weather()
        self._update_music()
        self._render_normal()

    # =========================================================================
    # FIRST BOOT WELCOME
    # =========================================================================

    def _send_first_boot_welcome(self):
        """Send welcome message on first boot after setup.
        Returns True if this is the first boot (welcome not yet sent)."""
        welcome_flag = os.path.join(LEELOO_HOME, '.welcome_sent')
        if os.path.exists(welcome_flag):
            return False

        user_name = self.device_config.get('user_name', '')
        crew_code = self.crew_config.get('invite_code',
                    self.crew_config.get('crew_code', ''))
        telegram_opted = self.device_config.get('telegram_opted_in', False)

        if not user_name:
            return False

        # Build welcome text
        welcome = f"welcome {user_name}!"
        if crew_code:
            welcome += f" your crew code is {crew_code}."
        if telegram_opted:
            welcome += " say 'telegram setup' for help anytime"

        # Add as a message from LEELOO
        self.message_mgr.add_message("leeloo", welcome)
        print(f"[BRAIN] Welcome message sent: {welcome[:60]}...")

        # Mark welcome as sent
        try:
            with open(welcome_flag, 'w') as f:
                f.write('1')
        except Exception:
            pass

        return True

    def _should_show_spotify_qr(self):
        """Check if we should show Spotify QR on welcome screen.
        Only if: Telegram setup happened AND Spotify NOT already connected."""
        telegram_opted = self.device_config.get('telegram_opted_in', False)
        spotify_tokens_path = os.path.join(LEELOO_HOME, "spotify_tokens.json")
        spotify_connected = os.path.exists(spotify_tokens_path)

        return telegram_opted and not spotify_connected

    def _generate_spotify_oauth_url(self):
        """Generate the Spotify OAuth URL for QR code."""
        from urllib.parse import quote
        # Use the actual WebSocket device_id that the server knows us by.
        # This is critical — the server uses state param to route tokens back.
        device_id = self.ws_client.config.device_id
        if not device_id:
            print("[BRAIN] WARNING: No device_id yet for Spotify OAuth URL")
            device_id = 'leeloo_unknown'
        client_id = "f8c3c0120e694af283d7d7f7c2f67d4c"
        redirect_uri = "https://leeloobot.xyz/spotify/callback"
        scopes = "user-read-currently-playing user-read-playback-state"
        return (
            f"https://accounts.spotify.com/authorize?"
            f"client_id={client_id}&"
            f"response_type=code&"
            f"redirect_uri={quote(redirect_uri)}&"
            f"scope={quote(scopes)}&"
            f"state={device_id}&"
            f"show_dialog=true"
        )

    def _generate_qr_image(self, qr_url, label_text, sublabel_text, label_color, hint_text=None):
        """Generate a 243x304 image with QR code for the album art box.
        Returns path to temp image, or None on failure."""
        try:
            import qrcode

            # Match album art dimensions: 243x304
            W, H = 243, 304
            bg = COLORS['bg']
            img = Image.new('RGB', (W, H), bg)
            draw = ImageDraw.Draw(img)

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=8,
                border=2,
            )
            qr.add_data(qr_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="white", back_color=bg)
            # Center QR in the top portion
            qr_size = min(W - 20, 200)
            qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST).convert('RGB')
            qr_x = (W - qr_size) // 2
            qr_y = 15
            img.paste(qr_img, (qr_x, qr_y))

            # Label below QR
            y = qr_y + qr_size + 10
            try:
                tw = self.font_small.getlength(label_text)
            except:
                tw = len(label_text) * 7
            draw.text(((W - tw) // 2, y), label_text, font=self.font_small, fill=label_color)

            # Sublabel
            y += 22
            try:
                tw = self.font_small.getlength(sublabel_text)
            except:
                tw = len(sublabel_text) * 7
            draw.text(((W - tw) // 2, y), sublabel_text, font=self.font_small, fill=label_color)

            if hint_text:
                y += 22
                try:
                    tw = self.font_small.getlength(hint_text)
                except:
                    tw = len(hint_text) * 7
                draw.text(((W - tw) // 2, y), hint_text, font=self.font_small, fill=(74, 74, 106))

            # Save to temp file
            qr_path = os.path.join(LEELOO_HOME, '.welcome_qr.png')
            img.save(qr_path, 'PNG')
            print(f"[BRAIN] QR image saved: {qr_path}")
            return qr_path

        except Exception as e:
            print(f"[BRAIN] QR image generation failed: {e}")
            return None

    async def _show_welcome_with_qr(self):
        """Show first-boot welcome in two phases:
        Phase 1: Telegram QR (always, if opted in) — 60s
        Phase 2: Spotify OAuth QR (if conditions met) — 60s
        """
        user_name = self.device_config.get('user_name', '')
        crew_code = self.crew_config.get('invite_code',
                    self.crew_config.get('crew_code', ''))
        telegram_opted = self.device_config.get('telegram_opted_in', False)
        show_spotify = self._should_show_spotify_qr()

        # Save original album art path to restore later
        original_art_path = self.album_art_path

        try:
            # _welcome_qr_active already set True before task creation

            # --- PHASE 1: Telegram QR (always if opted) ---
            if telegram_opted and crew_code:
                qr_path = self._generate_qr_image(
                    qr_url=f"https://t.me/Leeloo2259_bot?start={crew_code.replace('-', '')}",
                    label_text="scan for Telegram",
                    sublabel_text=crew_code,
                    label_color=COLORS['lavender']
                )
                if qr_path:
                    self.album_art_path = qr_path
                    self._render_normal()
                    print("[BRAIN] Telegram QR shown in album art box")

                lines = [
                    (f"welcome {user_name}!", "large", COLORS['lavender']),
                    ("", None, None),
                    (f"crew: {crew_code}", "normal", COLORS['green']),
                    ("share with friends to join", "small", COLORS['white']),
                    ("", None, None),
                    ("scan QR to connect", "normal", COLORS['tan']),
                    ("Telegram", "large", COLORS['tan']),
                    ("", None, None),
                    ('ask leeloo "setup help"', "small", COLORS['lavender']),
                    ("anytime you forget", "small", COLORS['lavender']),
                ]
                await self.expand_frame(FrameType.MESSAGES, lines, duration=60.0)

            # --- PHASE 2: Spotify OAuth QR (conditional) ---
            if show_spotify:
                qr_path = self._generate_qr_image(
                    qr_url=self._generate_spotify_oauth_url(),
                    label_text="scan to sync",
                    sublabel_text="your Spotify",
                    label_color=COLORS['green'],
                    hint_text="(optional)"
                )
                if qr_path:
                    self.album_art_path = qr_path
                    self._render_normal()
                    print("[BRAIN] Spotify OAuth QR shown in album art box")

                lines = [
                    ("one more thing...", "large", COLORS['green']),
                    ("", None, None),
                    ("scan QR to sync", "normal", COLORS['green']),
                    ("your Spotify", "large", COLORS['green']),
                    ("", None, None),
                    ("shows what you're", "small", COLORS['white']),
                    ("currently listening to", "small", COLORS['white']),
                    ("", None, None),
                    ("tap device to skip", "small", (74, 74, 106)),
                ]
                await self.expand_frame(FrameType.MESSAGES, lines, duration=60.0)

        except asyncio.CancelledError:
            print("[BRAIN] Welcome screen cancelled")
            raise
        finally:
            self._welcome_qr_active = False
            # Restore original album art — fall back to leeloo_for_sharing.png
            # when no music is loaded yet (avoids the gradient placeholder)
            sharing_img = os.path.join(LEELOO_HOME, 'leeloo_for_sharing.png')
            if original_art_path and os.path.exists(original_art_path):
                self.album_art_path = original_art_path
            elif os.path.exists(sharing_img):
                self.album_art_path = sharing_img
            else:
                self.album_art_path = original_art_path
            # Clean up temp QR file
            qr_path = os.path.join(LEELOO_HOME, '.welcome_qr.png')
            try:
                os.remove(qr_path)
            except:
                pass

    async def run(self):
        """Main entry point — start all subsystems"""
        print("[BRAIN] Starting LEELOO Brain...")
        print(f"[BRAIN] Device config: {self.device_config}")
        print(f"[BRAIN] Crew: {self.contacts}")
        print(f"[BRAIN] Location: {'configured' if self.location_configured else 'not set'}")

        # Move console off LCD
        os.system("sudo con2fbmap 1 0 2>/dev/null")

        # Initial render
        self._update_weather()
        self._update_music()
        self._render_normal()

        # Start ambient LED breathe (uses same logic as _sync_ambient_led)
        spotify_tokens_path = os.path.join(LEELOO_HOME, "spotify_tokens.json")
        initial_playing = (
            os.path.exists(spotify_tokens_path) and
            bool(self.music_data and self.music_data.get('is_playing', False))
        )
        initial_ambient = "playing" if initial_playing else "idle"
        self._led_ambient_state = initial_ambient
        await self.led.set_ambient(initial_ambient)

        # Send welcome message on first boot after setup
        is_first_boot = self._send_first_boot_welcome()

        # Start concurrent tasks
        tasks = [
            asyncio.create_task(self._display_loop()),
            asyncio.create_task(self.tap.start()),
        ]

        # WebSocket connection (with auto-reconnect)
        if self.ws_client.is_configured():
            tasks.append(asyncio.create_task(self._ws_connection_loop()))

        print("[BRAIN] All subsystems started. LEELOO is alive!")
        print("[BRAIN] Tap the device to interact.")

        # Auto-expand welcome screen with QR code on first boot
        if is_first_boot:
            self._welcome_qr_active = True  # Block music updates BEFORE task starts
            self._expand_task = asyncio.create_task(
                self._show_welcome_with_qr()
            )
            print("[BRAIN] First boot welcome screen launched")

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("\n[BRAIN] Shutting down...")
        finally:
            self.tap.stop()
            self.led.off_sync()
            if self.ws_client.connected:
                await self.ws_client.disconnect()
            print("[BRAIN] Goodbye!")

    async def _ws_connection_loop(self):
        """WebSocket connection with auto-reconnect"""
        retry_delay = 5
        max_delay = 60

        while True:
            try:
                print(f"[BRAIN] Connecting to relay ({RELAY_URL})...")
                if await self.ws_client.connect():
                    # Try to join crew — if it doesn't exist yet, create it
                    joined = await self.ws_client.rejoin_crew()
                    if not joined and self.crew_config.get('is_creator', False):
                        # We created this crew locally but it doesn't exist on server yet
                        display_name = self.device_config.get('user_name', 'LEELOO')
                        print(f"[BRAIN] Crew not found, creating on server...")
                        crew_code = await self.ws_client.create_crew(display_name)
                        if crew_code:
                            print(f"[BRAIN] Crew created on server: {crew_code}")
                            joined = True
                    if joined:
                        print("[BRAIN] WebSocket connected and crew joined!")
                        retry_delay = 5  # Reset on success

                        # Run listen and keepalive concurrently
                        listen_task = asyncio.create_task(self.ws_client.listen())
                        keepalive_task = asyncio.create_task(self.ws_client.keepalive())

                        # Wait for either to finish (means disconnected)
                        done, pending = await asyncio.wait(
                            [listen_task, keepalive_task],
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        # Cancel the other
                        for task in pending:
                            task.cancel()

                        print("[BRAIN] WebSocket disconnected")
                    else:
                        print("[BRAIN] Failed to rejoin crew")
                else:
                    print("[BRAIN] WebSocket connection failed")

            except Exception as e:
                print(f"[BRAIN] WebSocket error: {e}")

            # Exponential backoff
            print(f"[BRAIN] Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Entry point"""
    brain = LeelooBrain()
    try:
        asyncio.run(brain.run())
    except KeyboardInterrupt:
        print("\n[BRAIN] Interrupted")
    except Exception as e:
        print(f"[BRAIN] Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
