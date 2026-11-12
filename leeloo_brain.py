#!/usr/bin/env python3
"""
LEELOO Brain — Asyncio orchestrator for the LEELOO music sharing gadget

This is the main entry point. It handles taps, voice, WebSocket messages,
LED animations, and display rendering concurrently.

Rules that keep the device responsive (see CLAUDE.md):
- Nothing slow runs on the event loop. Network calls and PIL rendering run
  in worker threads (run_in_executor).
- Only ONE expanded frame exists at a time. Always use show_frame().
- The LEDs never animate while the microphone records (DMA conflict).

Usage:
    sudo python3 leeloo_brain.py
"""

import os
import re
import sys
import json
import time
import asyncio
import threading
import traceback
from enum import Enum, auto
from datetime import datetime
from zoneinfo import ZoneInfo

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")


def load_env_file(path=None):
    """Load KEY=VALUE lines from .env into os.environ.
    Runs before the subsystem imports because some of them read the
    environment at import time."""
    path = path or os.path.join(LEELOO_HOME, ".env")
    if not os.path.exists(path):
        print(f"[BRAIN] No .env file at {path}")
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        os.environ[key] = value
        print(f"[BRAIN] Loaded .env from {path}")
    except Exception as e:
        print(f"[BRAIN] Error loading .env: {e}")


load_env_file()

# PIL
from PIL import Image, ImageDraw, ImageFont

# Display
from gadget_display import LeelooDisplay, COLORS
from display.frame_animator import FrameAnimator, FrameType, rgb_to_rgb565_fast

# Data / utilities
import gadget_data
from gadget_weather import get_weather
from gadget_data import format_countdown_display, set_next_hang
import leeloo_music_manager as music_manager

# Subsystems
from leeloo_led import LEDManager
from leeloo_tap import TapManager
from leeloo_voice import VoiceManager
from leeloo_intent import IntentRouter, build_context, ask_claude, extract_json
from leeloo_messages import MessageManager
from leeloo_client import LeelooClient

# Config
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, "crew_config.json")
SPOTIFY_TOKENS_PATH = os.path.join(LEELOO_HOME, "spotify_tokens.json")
SPOTIFY_OFFERED_FLAG = os.path.join(LEELOO_HOME, ".spotify_offered")
FB_PATH = "/dev/fb1"
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320

# Timing
WEATHER_REFRESH_INTERVAL = 600  # 10 minutes
WEATHER_RETRY_INTERVAL = 60     # after a failed fetch
WEATHER_MAX_AGE = 2 * 3600      # older data is shown as "weather offline"
MUSIC_REFRESH_INTERVAL = 20     # when actively playing
DISPLAY_REFRESH_INTERVAL = 1.0  # display loop tick (renders only on change)
EXPANDED_HOLD_DURATION = 20.0   # How long expanded frames stay open
OFFLINE_GRACE_S = 30            # relay down this long -> "offline" in header
RELAY_URL = "wss://leeloobot.xyz/ws"

TYPE_CHAR_DELAY = 0.025         # typewriter: seconds per character
TYPE_LINE_DELAY = 0.08          # typewriter: pause after each line
WRAP_CHARS = 21                 # characters per line in an expanded frame
RAIN_BARS = 20                  # 20 x 15 min = next 5 hours
RAIN_TIMELINE = "now +1h +2h +3h +4h"

HINT_COLOR = (74, 74, 106)

# Static system prompts (cached by the API — keep them free of changing data)
MUSIC_DETECT_SYSTEM = """You detect music mentions in casual messages between friends.
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

COSMIC_TIME_SYSTEM = """Write a 65-word, Tolkien-style paragraph describing the given date as a day in its present season, naming the Sun's zodiac sign and the current Moon phase, and setting it between the fading Age of Pisces and the coming Age of Aquarius. Include one vivid, locally accurate example of animal behavior and one of plant life (bud, flower, or root) that are actively changing at the given place and time, with a hint of scientific wit.

Write ONLY the 65-word paragraph, no preamble or explanation."""

# Words that suggest a message talks about music. Messages without any of
# them skip the Claude call (saves one API call per ordinary message).
_MUSIC_HINT = re.compile(
    r"\b(song|songs|track|tracks|album|albums|band|artist|music|listen|listening|"
    r"listened|playlist|spotify|bumping|banger|bop|tune|tunes|remix|single|record|"
    r"mixtape|ep|lp|vinyl|playing|play|heard|hear|feat|ft|dj|rapper|singer|"
    r"concert|show|setlist|soundtrack|on repeat)\b",
    re.IGNORECASE,
)
_SPOTIFY_LINK = re.compile(r'open\.spotify\.com/(?:intl-[a-z]+/)?track/([a-zA-Z0-9]+)')
_SPOTIFY_URI = re.compile(r'(spotify:track:[a-zA-Z0-9]+)')


class UIState(Enum):
    """UI display states"""
    NORMAL = auto()
    EXPANDING = auto()
    EXPANDED = auto()
    COLLAPSING = auto()
    LISTENING = auto()
    PROCESSING = auto()


FRAME_STATES = (UIState.EXPANDING, UIState.EXPANDED, UIState.COLLAPSING)
VOICE_STATES = (UIState.LISTENING, UIState.PROCESSING)


# =============================================================================
# Helper functions (pure — covered by tests/test_brain_helpers.py)
# =============================================================================

def load_json(path):
    """Load JSON file safely"""
    try:
        with open(path, 'r') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_json_atomic(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


_fb_warned = False


def write_to_framebuffer(img, fb_path=FB_PATH):
    """Write a full 480x320 PIL image to the LCD framebuffer as RGB565."""
    global _fb_warned
    try:
        data = rgb_to_rgb565_fast(img).tobytes()
        with open(fb_path, 'wb') as fb:
            fb.write(data)
    except (FileNotFoundError, PermissionError) as e:
        if not _fb_warned:
            _fb_warned = True
            print(f"[BRAIN] Cannot write to framebuffer {fb_path}: {e}")


def wrap_text(text, width=WRAP_CHARS):
    """Word-wrap text for the expanded frame. Words longer than the line are split."""
    lines, current = [], ""
    for word in (text or "").split():
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# The LCD font (DejaVu Sans Mono) has Latin, Greek and Cyrillic letters only.
_EMOJI_WORDS = {
    '❤': '<3', '♥': '<3', '💜': '<3', '💙': '<3', '💚': '<3', '🧡': '<3', '💛': '<3', '🖤': '<3',
    '😍': '(love)', '🥰': '(love)', '😘': '(kiss)', '🔥': '(fire)', '😂': '(lol)', '🤣': '(lol)',
    '😭': '(cry)', '😢': ':(', '😊': ':)', '🙂': ':)', '😀': ':D', '😁': ':D', '😎': 'B)',
    '👍': '(+1)', '👎': '(-1)', '🙏': '(thanks)', '🎉': '(party)', '💯': '100', '✨': '*',
    '👀': '(eyes)', '💀': '(dead)', '🤔': '(hmm)', '🎵': '(music)', '🎶': '(music)', '🎧': '(music)',
    '’': "'", '‘': "'", '“': '"', '”': '"', '„': '"', '–': '-', '—': '-', '−': '-',
    '\u2011': '-', '…': '...', '•': '-',
}
MAX_MESSAGE_CHARS = 400


def clean_display_text(text, limit=None):
    """Make text safe for the LCD font: common emoji become short words,
    characters without a glyph are removed, whitespace is collapsed.
    `limit` cuts very long text (a 4000-character message would scroll for minutes)."""
    out = []
    dropped = False
    for ch in str(text or ""):
        if ch in _EMOJI_WORDS:
            out.append(f" {_EMOJI_WORDS[ch]} " if len(_EMOJI_WORDS[ch]) > 1 and ord(ch) > 0x2100 else _EMOJI_WORDS[ch])
        elif ch.isspace():
            out.append(" ")
        elif ch.isprintable() and (ord(ch) < 0x0530 or 0x1E00 <= ord(ch) <= 0x1EFF or ch == '€'):
            out.append(ch)    # Latin (also Vietnamese), Greek, Cyrillic
        elif ch not in ('\ufe0f', '\u200d'):
            dropped = True
    cleaned = " ".join("".join(out).split())
    if not cleaned and dropped:
        return "(text that leeloo can't show)"
    if limit and len(cleaned) > limit:
        cleaned = cleaned[:limit - 3].rstrip() + "..."
    return cleaned


def format_display_text(text, color, size="normal"):
    """Format a block of text into typewriter content lines (text, size, color)."""
    lines = wrap_text(clean_display_text(text))
    if not lines:
        return [("no response", size, color)]
    return [(line, size, color) for line in lines]


def looks_like_music(text):
    """Cheap pre-filter: is it worth asking Claude if this message names music?"""
    return bool(_MUSIC_HINT.search(text or ""))


def spotify_uri_in_text(text):
    """Return 'spotify:track:<id>' if the text holds a Spotify track link or URI."""
    link = _SPOTIFY_LINK.search(text or "")
    if link:
        return f"spotify:track:{link.group(1)}"
    uri = _SPOTIFY_URI.search(text or "")
    return uri.group(1) if uri else None


def build_rain_viz(weather_data):
    """Rain chart for the expanded weather frame, from the REAL 15-minute
    precipitation forecast (weather_data['precip_15min_mm']).

    Returns typewriter lines. No forecast data -> no chart (empty list).
    """
    values = (weather_data or {}).get('precip_15min_mm') or []
    values = values[:RAIN_BARS]
    if not values:
        return []
    if max(values) <= 0:
        return [("no rain next 5 hrs", "normal", COLORS['lavender'])]

    blocks = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']
    bars = []
    for mm in values:
        if mm <= 0:
            level = 0
        else:
            # 0.1 mm/15min = drizzle (level 1) ... 2.4+ mm/15min = downpour (level 7)
            level = 1 + min(6, int(mm / 0.4))
        bars.append(blocks[level])
    return [
        ("".join(bars), "normal", COLORS['lavender']),
        (RAIN_TIMELINE, "normal", COLORS['white']),
    ]


def roll_forward(dt, now):
    """If dt is in the past, move it forward by whole years until it is not.
    (The language model sometimes answers with a past year.)"""
    guard = 0
    while dt < now and guard < 10:
        try:
            dt = dt.replace(year=dt.year + 1)
        except ValueError:          # Feb 29
            dt = dt.replace(year=dt.year + 1, day=28)
        guard += 1
    return dt


def contact_names(members):
    """Normalise a members list (strings or {'name': ...}) to unique name strings."""
    names = []
    for m in members or []:
        name = m.get('name') if isinstance(m, dict) else m
        if isinstance(name, str) and name.strip() and name not in names:
            names.append(name.strip())
    return names


# =============================================================================
# LEELOO Brain
# =============================================================================

class LeelooBrain:
    """Main orchestrator — coordinates all LEELOO subsystems"""

    def __init__(self):
        print("[BRAIN] Initializing LEELOO Brain...")

        # UI State
        self.ui_state = UIState.NORMAL
        self.expanded_frame = None        # which FrameType is expanded
        self.message_view_active = False  # for double-tap routing
        self._voice_busy = False          # a tap-to-talk session is running
        self._expand_task = None          # current frame task (expand/hold/collapse)
        self._frame_lock = asyncio.Lock() # only one frame draws at a time
        self._deferred_frames = []        # frames that arrived during a voice session
        self._deferred_runner_active = False
        self._welcome_qr_active = False   # album art box shows a QR code
        self._background_tasks = set()    # strong refs for fire-and-forget tasks

        # Config
        self.device_config = load_json(DEVICE_CONFIG_PATH)
        self.crew_config = load_json(CREW_CONFIG_PATH)

        # Display
        self.display = LeelooDisplay(preview_mode=False)
        self._render_lock = threading.Lock()
        self._last_render_sig = None
        self._display_wake = asyncio.Event()

        # Subsystems
        self.led = LEDManager(num_leds=self.device_config.get('num_leds', 3))
        self.tap = TapManager(callback=self._on_tap)
        self._led_ambient_state = None

        deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

        if deepgram_key:
            self.voice = VoiceManager(deepgram_key)
            print("[BRAIN] Voice: enabled (Deepgram)")
        else:
            self.voice = None
            print("[BRAIN] Voice: disabled (no DEEPGRAM_API_KEY)")

        if anthropic_key:
            self.intent_router = IntentRouter(anthropic_key, get_context_fn=self._get_intent_context)
        else:
            self.intent_router = None
            print("[BRAIN] Intent: disabled (no ANTHROPIC_API_KEY)")

        self.message_mgr = MessageManager()
        print(f"[BRAIN] Messages: {self.message_mgr.get_total_unread()} unread")

        self.ws_client = LeelooClient(relay_url=RELAY_URL, config_path=CREW_CONFIG_PATH)
        self._setup_ws_callbacks()
        self._ws_down_since = None
        self._crew_error_shown = False
        self._queued_count = 0
        print(f"[BRAIN] WebSocket: {'configured' if self.ws_client.is_configured() else 'no crew'}")

        # Ensure ws_client uses the user's chosen name, not the stale crew config default
        self.user_name = self.device_config.get('user_name', '')
        if self.user_name:
            self.ws_client.config.display_name = self.user_name

        # Data caches (written by the refresh thread, read by the event loop)
        self.weather_data = None
        self.weather_time = 0
        self.music_data = None
        self.album_art_path = None
        self.time_data = {}
        self.contacts = contact_names(self.crew_config.get('members', []))
        self.last_weather_fetch = 0
        self.last_music_fetch = 0
        self._last_playing_at = 0         # last time Spotify reported "playing"
        self._refresh_lock = threading.Lock()

        # Timezone
        tz_name = self.device_config.get('timezone')
        try:
            self.local_tz = ZoneInfo(tz_name) if tz_name else None
        except Exception:
            self.local_tz = None
        if hasattr(gadget_data, 'set_local_tz'):
            gadget_data.set_local_tz(self.local_tz)

        # Location
        self.latitude = self.device_config.get('latitude')
        self.longitude = self.device_config.get('longitude')
        self.location_configured = self.latitude is not None and self.longitude is not None

        # Hang scheduling: a proposed hang waits for confirmation. Stored on
        # disk (see _pending_hang_dt) so a restart does not lose it.

        # Fonts for typewriter
        try:
            self.font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
            self.font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 18)
            self.font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
        except OSError:
            self.font = ImageFont.load_default()
            self.font_large = self.font
            self.font_small = self.font

        print("[BRAIN] Initialization complete")

    # =========================================================================
    # SMALL UTILITIES
    # =========================================================================

    def _now(self):
        """Current device-local time (timezone-aware when a timezone is configured)."""
        return datetime.now(tz=self.local_tz) if self.local_tz else datetime.now()

    @property
    def _pending_hang_dt(self):
        """ISO datetime of the hang proposal that waits for confirmation (or None)."""
        try:
            stored = gadget_data.load_data().get('pending_hang')
        except Exception:
            return None
        if isinstance(stored, dict):
            return stored.get('datetime') or None
        return stored or None

    @_pending_hang_dt.setter
    def _pending_hang_dt(self, value):
        try:
            data = gadget_data.load_data()
            if value:
                data['pending_hang'] = {'datetime': value, 'by': 'crew'}
            elif 'pending_hang' in data:
                del data['pending_hang']
            else:
                return
            gadget_data.save_data(data)
        except Exception as e:
            print(f"[BRAIN] Could not store pending hang: {e}")

    def _spawn(self, coro):
        """Start a background task and keep a reference so it is not garbage-collected.
        Exceptions are printed instead of being lost."""
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)

        def _done(t):
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                print(f"[BRAIN] Background task error: {t.exception()!r}")

        task.add_done_callback(_done)
        return task

    async def _in_thread(self, fn, *args):
        """Run blocking work (network, PIL) off the event loop."""
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    def _led_notify(self, make_coro):
        """Start an LED notification — but never while the microphone records
        (WS2812B and I2S share the DMA controller; LED activity kills the audio)."""
        if self.ui_state == UIState.LISTENING or self.led.recording:
            return
        self._spawn(make_coro())

    def _wake_display(self):
        self._display_wake.set()

    def _frame_active(self):
        return (self.ui_state in FRAME_STATES or
                (self._expand_task is not None and not self._expand_task.done()))

    def _has_live_spotify(self):
        return os.path.exists(SPOTIFY_TOKENS_PATH)

    def _connected(self):
        return bool(self.ws_client and self.ws_client.connected)

    def _crew_code(self):
        return self.crew_config.get('invite_code', self.crew_config.get('crew_code', ''))

    def _get_intent_context(self):
        """Build context string for intent router"""
        messages = None
        try:
            messages = self.message_mgr.get_history_24h()
        except Exception:
            pass
        now = self._now()
        return build_context(
            weather_data=self.weather_data if self._weather_is_fresh() else None,
            music_data=self.music_data,
            contacts=self.contacts,
            messages=messages,
            now=now.replace(tzinfo=None),
            pending_hang=self._hang_context(),
            next_hang=(self._hang_label(self._next_hang()) if self._next_hang() else None),
        )

    def _hang_context(self):
        pending_dt, by = self._pending_hang()
        if pending_dt is not None:
            who = "this user" if by == 'me' else by
            return f"{pending_dt.strftime('%A %Y-%m-%d %H:%M')}, proposed by {who}, not confirmed yet"
        return None

    # =========================================================================
    # FRAMES — the only way to show an expanded frame
    # =========================================================================

    def show_frame(self, frame_type, lines, duration=EXPANDED_HOLD_DURATION, from_voice=False,
                   reactable=False):
        """Show an expanded frame. Replaces any frame that is open now.

        Safe to call from sync callbacks on the event loop. A frame that
        arrives during a voice session is held back until the session ends
        (drawing during recording disturbs the audio and the UI state).
        """
        if self._voice_busy and not from_voice:
            # Shown after the session, in order of arrival (the newest 3)
            self._deferred_frames.append((frame_type, lines, duration, False, reactable))
            del self._deferred_frames[:-3]
            return None
        return self._start_frame_task(
            lambda: self.expand_frame(frame_type, lines, duration, reactable=reactable))

    def _start_frame_task(self, make_coro):
        """Make `make_coro()` the current frame task. The frame that is open
        now is cancelled and has finished its cleanup before the new one starts."""
        previous = self._expand_task
        self._expand_task = asyncio.ensure_future(self._replace_frame(previous, make_coro))
        return self._expand_task

    async def _replace_frame(self, previous, make_coro):
        if previous is not None and not previous.done():
            previous.cancel()
            await asyncio.gather(previous, return_exceptions=True)
        await make_coro()

    def dismiss_frame(self):
        if self._expand_task is not None and not self._expand_task.done():
            self._expand_task.cancel()

    async def expand_frame(self, frame_type: FrameType, content_lines, duration=EXPANDED_HOLD_DURATION,
                           reactable=False):
        """Expand a frame, typewriter the content, hold, then collapse.
        Use show_frame() unless you are already inside the current frame task.
        reactable: the frame shows a crew message; double/triple tap send love/fire."""
        async with self._frame_lock:
            self.ui_state = UIState.EXPANDING
            self.expanded_frame = frame_type
            self.message_view_active = bool(reactable)
            try:
                album_x = await self._in_thread(self.display.album_frame_x, self.album_art_path)
                box_right = album_x - 10  # 5px gap + 5px padding
                animator = FrameAnimator(self.display, box_right=box_right, fb_path=FB_PATH)

                print(f"[BRAIN] Expanding {frame_type.value}...")
                await animator.async_expand(frame_type)

                self.ui_state = UIState.EXPANDED
                content_lines = self._fit_lines(content_lines, box_right - 7 - 25)
                overflow = await self._typewriter(content_lines, box_right)

                if overflow:
                    await self._scroll_content(overflow, duration)
                else:
                    await asyncio.sleep(duration)

                self.ui_state = UIState.COLLAPSING
                await animator.async_collapse(frame_type)

            except asyncio.CancelledError:
                print("[BRAIN] Frame dismissed")
                raise
            finally:
                self.ui_state = UIState.NORMAL
                self.expanded_frame = None
                self.message_view_active = False
                self.last_music_fetch = 0   # pick up new album art on the next refresh
                # Repaint the normal screen now (in a thread) and WAIT until it
                # is complete, also when this task is cancelled again meanwhile.
                # Otherwise the next frame would start to draw while the repaint
                # still writes to the screen.
                repaint = asyncio.ensure_future(self._in_thread(self._render_normal, True))
                cancelled_again = False
                while not repaint.done():
                    try:
                        await asyncio.shield(repaint)
                    except asyncio.CancelledError:
                        cancelled_again = True
                    except Exception as e:
                        print(f"[BRAIN] Render after frame failed: {e}")
                if cancelled_again:
                    raise asyncio.CancelledError()

    @staticmethod
    def _text_width(font, text):
        try:
            return font.getlength(text)
        except AttributeError:
            return len(text) * 8

    def _fit_lines(self, lines, width):
        """Re-wrap every line that is wider than the frame in PIXELS for its font.
        (A "large" line holds about 16 characters, a "normal" line about 21.)"""
        fitted = []
        for text, size, color in lines:
            font = self._line_font(size)
            # +2 px: a full 21-character normal line is 176.9 px in a 176 px frame
            width_ok = width + 2
            if not text or self._text_width(font, text) <= width_ok:
                fitted.append((text, size, color))
                continue
            current = ""
            for word in text.split(" "):
                candidate = f"{current} {word}" if current else word
                if self._text_width(font, candidate) <= width_ok:
                    current = candidate
                    continue
                if current:
                    fitted.append((current, size, color))
                while len(word) > 1 and self._text_width(font, word) > width_ok:
                    cut = len(word) - 1
                    while cut > 1 and self._text_width(font, word[:cut]) > width_ok:
                        cut -= 1
                    fitted.append((word[:cut], size, color))
                    word = word[cut:]
                current = word
            if current:
                fitted.append((current, size, color))
        return fitted

    def _line_font(self, size):
        if size == "large":
            return self.font_large
        if size == "small":
            return self.font_small
        return self.font

    async def _typewriter(self, content_lines, box_right):
        """Typewriter effect — writes text character by character to the framebuffer.
        Returns scroll info if the content is taller than the frame, else None."""
        content_x = 7 + 10
        content_y = 16 + 25
        line_height = 22
        text_region_width = max(10, box_right - 7 - 25)
        char_delay = TYPE_CHAR_DELAY
        line_delay = TYPE_LINE_DELAY

        max_y = SCREEN_HEIGHT - 20          # bottom border ~row 304
        visible_height = max_y - content_y

        total_content_height = sum(
            (line_height // 2) if text == "" else line_height
            for text, _size, _color in content_lines
        )
        has_overflow = total_content_height > visible_height

        full_content_img = Image.new(
            'RGB', (text_region_width, max(total_content_height, visible_height)), COLORS['bg'])
        full_draw = ImageDraw.Draw(full_content_img)

        try:
            fb = open(FB_PATH, 'r+b')
        except OSError:
            print("[BRAIN] Cannot open framebuffer for typewriter")
            fb = None

        y_pos = content_y
        try:
            for line_text, font_size, line_color in content_lines:
                if line_text == "":
                    y_pos += line_height // 2
                    continue

                line_font = self._line_font(font_size)
                line_is_visible = fb is not None and y_pos + line_height <= max_y

                if line_is_visible:
                    line_img = Image.new('RGB', (text_region_width, line_height), COLORS['bg'])
                    line_draw = ImageDraw.Draw(line_img)
                    for end in range(1, len(line_text) + 1):
                        if line_text[end - 1] != " ":
                            line_draw.rectangle([0, 0, text_region_width, line_height], fill=COLORS['bg'])
                            line_draw.text((0, 2), line_text[:end], font=line_font, fill=line_color)
                            rgb565 = rgb_to_rgb565_fast(line_img)
                            for row in range(rgb565.shape[0]):
                                fb.seek(((y_pos + row) * SCREEN_WIDTH + content_x) * 2)
                                fb.write(rgb565[row, :].tobytes())
                        await asyncio.sleep(char_delay)

                render_y = y_pos - content_y
                full_draw.text((0, render_y + 2), line_text, font=line_font, fill=line_color)

                y_pos += line_height
                # Always yield: a loop without await starves the microphone reader
                await asyncio.sleep(line_delay if line_is_visible else 0)
        finally:
            if fb is not None:
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

    async def _scroll_content(self, overflow, hold_duration):
        """Scroll overflowed content upward through the visible area, then hold."""
        content_img = overflow['image']
        visible_h = overflow['visible_height']
        content_x = overflow['content_x']
        content_y = overflow['content_y']
        max_scroll = overflow['total_height'] - visible_h

        frame_delay = 0.07   # ~14 fps, 1 px per frame
        try:
            fb = open(FB_PATH, 'r+b')
        except OSError:
            await asyncio.sleep(hold_duration)
            return

        try:
            # Convert the whole content ONCE; each scroll step only copies rows
            rgb565 = rgb_to_rgb565_fast(content_img)
            rows = [rgb565[r, :].tobytes() for r in range(rgb565.shape[0])]
            visible_rows = min(visible_h, SCREEN_HEIGHT - content_y)
            for scroll_offset in range(0, max_scroll + 1):
                for row in range(visible_rows):
                    fb.seek(((content_y + row) * SCREEN_WIDTH + content_x) * 2)
                    fb.write(rows[scroll_offset + row])
                await asyncio.sleep(frame_delay)

            elapsed_scroll = max_scroll * frame_delay
            await asyncio.sleep(max(hold_duration - elapsed_scroll - 2.0, 3.0))
        finally:
            fb.close()

    # =========================================================================
    # WEBSOCKET CALLBACKS (sync, called on the event loop)
    # =========================================================================

    def _setup_ws_callbacks(self):
        c = self.ws_client
        c.on_message = self._on_ws_message
        c.on_reaction = self._on_ws_reaction
        c.on_song_push = self._on_ws_song_push
        c.on_nudge = self._on_ws_nudge
        c.on_hang_propose = self._on_ws_hang_propose
        c.on_hang_confirm = self._on_ws_hang_confirm
        c.on_member_joined = self._on_member_joined
        c.on_member_offline = self._on_member_offline
        c.on_spotify_auth = self._on_spotify_auth
        c.on_roster = self._on_roster
        c.on_queued_message = self._on_ws_queued

    def _remember_contact(self, name):
        """Add a crew member to the contact list (persisted in crew_config.json)."""
        if not isinstance(name, str) or not name.strip():
            return
        name = name.strip()
        if name in self.contacts or name == self.user_name or name.lower() == 'leeloo':
            return
        self.contacts.append(name)
        try:
            config = load_json(CREW_CONFIG_PATH)
            config['members'] = list(self.contacts)
            save_json_atomic(CREW_CONFIG_PATH, config)
            self.crew_config = config
        except OSError as e:
            print(f"[BRAIN] Could not save crew members: {e}")
        self._wake_display()

    def _on_roster(self, names):
        for name in names or []:
            self._remember_contact(name)

    def _on_member_joined(self, name):
        print(f"[BRAIN] {name} came online")
        self._remember_contact(name)

    def _on_member_offline(self, name):
        # Members stay in the crew list when their device is offline.
        print(f"[BRAIN] {name} went offline")

    def _on_ws_message(self, sender, text):
        """Incoming crew message — show it, and look for a song mention"""
        print(f"[BRAIN] Message from {sender}: {text}")
        self._remember_contact(sender)
        self.message_mgr.add_message(sender, text)
        self._led_notify(self.led.message_received)
        self.show_frame(FrameType.MESSAGES, [
            (f"from {sender.lower()}", "large", COLORS['lavender']),
            ("", None, None),
            *format_display_text(clean_display_text(text, MAX_MESSAGE_CHARS), COLORS['white']),
            ("", None, None),
            ("2 taps = love", "small", HINT_COLOR),
            ("3 taps = fire", "small", HINT_COLOR),
        ], reactable=True)
        self._spawn(self._show_music_from_message(sender, text))

    async def _show_music_from_message(self, sender, text):
        """If a crew message names a song, show its album art."""
        try:
            query = await self._detect_music_in_message(text)
            if not query:
                return
            print(f"[BRAIN] Music detected in message: '{query}'")
            result = await self._in_thread(music_manager.search_and_save_shared, query, sender)
            if result:
                print(f"[BRAIN] Album art updated: {result['artist']} - {result['track']}")
                self.last_music_fetch = 0   # the data loop picks it up
        except Exception as e:
            print(f"[BRAIN] Music detection error: {e}")

    def _on_ws_reaction(self, sender, reaction_type):
        print(f"[BRAIN] Reaction from {sender}: {reaction_type}")
        self._led_notify(self.led.ack)
        word = "fire!" if reaction_type == 'fire' else "love"
        self.show_frame(FrameType.MESSAGES, [
            (f"{sender.lower()} sent", "normal", COLORS['white']),
            (word, "large", COLORS['rose'] if reaction_type != 'fire' else COLORS['orange']),
        ], duration=5.0)

    def _on_ws_song_push(self, sender, payload):
        """Incoming song push — download album art + scancode, then show it"""
        artist = payload.get('artist', '')
        track = payload.get('track', '')
        note = payload.get('note', '')
        spotify_uri = payload.get('spotify_uri', '')
        print(f"[BRAIN] Song from {sender}: {artist} - {track}")
        self._remember_contact(sender)
        self._led_notify(self.led.music_received)

        async def _fetch_art():
            query = spotify_uri if spotify_uri else f"{artist} {track}"
            result = await self._in_thread(music_manager.search_and_save_shared, query, sender)
            if result:
                self.last_music_fetch = 0

        self._spawn(_fetch_art())

        self.show_frame(FrameType.ALBUM, [
            (f"{sender.lower()} shared", "large", COLORS['green']),
            ("", None, None),
            *format_display_text(f"{artist} - {track}", COLORS['white']),
            *([("", None, None), *format_display_text(note, COLORS['lavender'])] if note else []),
        ], duration=15.0)

    def _on_ws_nudge(self, sender):
        print(f"[BRAIN] Nudge from {sender}!")
        self._led_notify(lambda: self.led.nudge(duration=30))
        self.show_frame(FrameType.MESSAGES, [
            (f"{sender.lower()}", "large", COLORS['lavender']),
            ("nudged you", "normal", COLORS['white']),
        ], duration=6.0)

    def _parse_hang(self, datetime_str):
        """ISO string -> datetime comparable with self._now(). None if invalid."""
        try:
            dt = datetime.fromisoformat(datetime_str)
        except (TypeError, ValueError):
            return None
        if self.local_tz:
            return dt.astimezone(self.local_tz) if dt.tzinfo else dt.replace(tzinfo=self.local_tz)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt

    @staticmethod
    def _hang_label(dt):
        return dt.strftime("%a %b %-d · %-I:%M%p").lower()

    def _pending_hang(self):
        """The hang proposal that waits for an answer: (datetime, proposed_by).
        proposed_by is 'me' or a crew member's name. A proposal whose time
        has passed is removed. Returns (None, None) when there is none."""
        try:
            stored = gadget_data.load_data().get('pending_hang')
        except Exception:
            stored = None
        if isinstance(stored, str):                 # older format
            stored = {'datetime': stored, 'by': 'crew'}
        if not isinstance(stored, dict):
            return None, None
        dt = self._parse_hang(stored.get('datetime'))
        if dt is None or dt <= self._now():
            self._pending_hang_dt = None
            return None, None
        return dt, stored.get('by') or 'crew'

    def _set_pending_hang(self, dt, proposed_by):
        try:
            data = gadget_data.load_data()
            data['pending_hang'] = {'datetime': dt.isoformat(), 'by': proposed_by}
            gadget_data.save_data(data)
        except Exception as e:
            print(f"[BRAIN] Could not store pending hang: {e}")

    def _next_hang(self):
        """The saved (confirmed) hang as a datetime in the future, or None."""
        try:
            hang = gadget_data.get_next_hang()
        except Exception:
            hang = None
        dt = self._parse_hang((hang or {}).get('datetime'))
        return dt if dt is not None and dt > self._now() else None

    def _save_pending_hang(self):
        """Make the pending proposal the next hang. Returns the datetime, or
        None when there was no valid proposal (nothing is saved then)."""
        dt, _by = self._pending_hang()
        self._pending_hang_dt = None
        if dt is None:
            return None
        try:
            set_next_hang(dt, set_by="voice")
            print(f"[BRAIN] Next hang saved: {dt.isoformat()}")
            self._wake_display()
            return dt
        except Exception as e:
            print(f"[BRAIN] Failed to save hang time: {e}")
            return None

    def _on_ws_hang_propose(self, sender, datetime_str, description):
        """Incoming hang proposal — remember it so a confirm can save it"""
        print(f"[BRAIN] Hang proposal from {sender}: {datetime_str}")
        dt = self._parse_hang(datetime_str)
        if dt is not None and dt <= self._now():
            dt = None                                # the time has passed: nothing to answer
        if dt is not None:
            self._set_pending_hang(dt, sender)
        when = self._hang_label(dt) if dt is not None else ""
        self._led_notify(self.led.message_received)
        self.show_frame(FrameType.MESSAGES, [
            (f"{sender.lower()} wants to", "large", COLORS['lavender']),
            ("hang!", "large", COLORS['lavender']),
            ("", None, None),
            *format_display_text(f"{when} {description or ''}".strip() or "(no time given)",
                                 COLORS['white']),
            ("", None, None),
            *([('tap + say "i\'m in"', "small", COLORS['tan'])] if dt is not None else []),
        ])

    def _on_ws_hang_confirm(self, sender):
        """A crew member confirmed. The first confirmation makes the proposal
        the next hang on every device; later ones only report who is in."""
        print(f"[BRAIN] Hang confirmed by {sender}!")
        self._led_notify(self.led.ack)
        saved = self._save_pending_hang()
        # Show a time only for the proposal this device knows about. (An older
        # confirmed hang could be a different one.)
        when = saved
        self.show_frame(FrameType.MESSAGES, [
            ("hang confirmed!" if saved else f"{sender.lower()} is in!", "large", COLORS['lavender']),
            ("", None, None),
            *([(f"{sender.lower()} is in", "normal", COLORS['white'])] if saved
              else [("for the hang", "normal", COLORS['white'])]),
            *([(self._hang_label(when), "normal", COLORS['purple'])] if when else []),
        ], duration=10.0)

    def _on_ws_queued(self, sender, msg_type, payload, timestamp):
        """A message that the server stored while this device was offline.
        Stored silently; one summary frame is shown for the whole batch."""
        payload = payload if isinstance(payload, dict) else {}
        self._remember_contact(sender)
        if time.time() - (timestamp or 0) > 86400:
            return   # older than the 24 h message history
        if msg_type == 'text':
            self.message_mgr.add_message(sender, payload.get('text', ''), timestamp)
        elif msg_type == 'song_push':
            artist, track = payload.get('artist', ''), payload.get('track', '')
            self.message_mgr.add_message(sender, f"shared a song: {artist} - {track}", timestamp)
            query = payload.get('spotify_uri') or f"{artist} {track}"

            async def _fetch_art():
                if await self._in_thread(music_manager.search_and_save_shared, query, sender):
                    self.last_music_fetch = 0
            self._spawn(_fetch_art())
        elif msg_type == 'hang_propose':
            dt = self._parse_hang(payload.get('datetime', ''))
            if dt is None or dt <= self._now():
                return   # the proposed time has passed
            self._set_pending_hang(dt, sender)
            self.message_mgr.add_message(
                sender, f"wants to hang {self._hang_label(dt)} {payload.get('description', '')}".strip(),
                timestamp)
        elif msg_type == 'hang_confirm':
            self._save_pending_hang()
            self.message_mgr.add_message(sender, "confirmed the hang", timestamp)
        else:
            return

        self._queued_count += 1
        if self._queued_count == 1:
            self._spawn(self._show_queued_summary())
        self._wake_display()

    async def _show_queued_summary(self):
        await asyncio.sleep(2.0)   # let the whole batch arrive
        count, self._queued_count = self._queued_count, 0
        self._led_notify(self.led.message_received)
        self.show_frame(FrameType.MESSAGES, [
            ("while you were", "large", COLORS['lavender']),
            ("away...", "large", COLORS['lavender']),
            ("", None, None),
            (f"{count} new message{'s' if count != 1 else ''}", "normal", COLORS['white']),
            ("", None, None),
            ("tap and say", "small", COLORS['tan']),
            ('"what did i miss"', "small", COLORS['tan']),
        ], duration=12.0)

    def _on_spotify_auth(self, tokens):
        """Spotify OAuth tokens received via WebSocket"""
        print("[BRAIN] Spotify connected! Triggering music refresh...")
        self.last_music_fetch = 0
        self.show_frame(FrameType.ALBUM, [
            ("spotify connected!", "large", COLORS['green']),
            ("", None, None),
            ("your music will now", "normal", COLORS['white']),
            ("show on your LEELOO", "normal", COLORS['white']),
        ], duration=10.0)

    # =========================================================================
    # DATA REFRESH (blocking — runs in a worker thread)
    # =========================================================================

    def _update_time(self):
        now = self._now()
        self.time_data = {
            'time_str': now.strftime('%-I:%M %p'),
            'date_str': now.strftime('%b %-d'),
            'seconds': now.second,
        }

    def _weather_is_fresh(self):
        return self.weather_data is not None and (time.time() - self.weather_time) < WEATHER_MAX_AGE

    def _weather_for_display(self):
        if not self.location_configured:
            return None                      # display shows "set location"
        if self._weather_is_fresh():
            return self.weather_data
        if self.last_weather_fetch == 0:
            return None                      # nothing fetched yet
        return {'error': True}               # display shows "weather offline"

    def _update_weather(self):
        """Refresh weather if due. A failed fetch keeps the last good data
        and tries again after one minute. Invented values are never shown."""
        if not self.location_configured:
            return
        now = time.time()
        if now < self.last_weather_fetch:
            return   # next fetch time not reached
        data = get_weather(self.latitude, self.longitude,
                           timezone=self.device_config.get('timezone'))
        if data:
            self.weather_data = data
            self.weather_time = now
            self.last_weather_fetch = now + WEATHER_REFRESH_INTERVAL
            print(f"[BRAIN] Weather: {data.get('temp_f', '?')}°F")
        else:
            self.last_weather_fetch = now + WEATHER_RETRY_INTERVAL

    def _music_interval(self, now):
        """Tiered polling: 20s playing, 60s recently played, 120s idle."""
        if not self.last_music_fetch:
            return 0
        if self.music_data and self.music_data.get('is_playing'):
            return MUSIC_REFRESH_INTERVAL
        if now - self._last_playing_at < 300:
            return 60
        return 120

    def _update_music(self):
        """Refresh music data if due (now playing via OAuth, else last shared track)."""
        now = time.time()
        if now - self.last_music_fetch < self._music_interval(now):
            return
        self.last_music_fetch = now

        result = music_manager.update_music_display()

        if self._welcome_qr_active:
            return   # the album art box shows a QR code — do not replace it

        if not result:
            self.music_data = None
            self.album_art_path = None
            return

        live = result.get('source') == 'currently_playing' and self._has_live_spotify()
        self.music_data = {
            'artist': result.get('artist', ''),
            'album': result.get('album', ''),
            'track': result.get('track', ''),
            'bpm': result.get('bpm'),
            'listeners': result.get('listeners'),
            'pushed_by': result.get('pushed_by'),
            'spotify_uri': result.get('spotify_uri'),
            # Only a live OAuth answer says what is playing NOW. The JSON
            # cache holds the state from when the file was written.
            'is_playing': bool(live and result.get('is_playing', False)),
            'last_updated': now if live else result.get('timestamp', now),
        }
        if self.music_data['is_playing']:
            self._last_playing_at = now
        art = result.get('album_art_cached')
        self.album_art_path = art if art and os.path.exists(art) else None

    def _refresh_data(self):
        """Blocking refresh of weather + music. One at a time."""
        with self._refresh_lock:
            try:
                self._update_weather()
            except Exception as e:
                print(f"[BRAIN] Weather refresh error: {e}")
            try:
                self._update_music()
            except Exception as e:
                print(f"[BRAIN] Music refresh error: {e}")

    def _sync_ambient_led(self):
        """Match the ambient LED colour to playback (event loop only).
        Never touches the LEDs during a voice session."""
        if self._voice_busy:
            return
        playing = bool(self.music_data and self.music_data.get('is_playing'))
        new_state = "playing" if playing else "idle"
        if new_state != self._led_ambient_state:
            self._led_ambient_state = new_state
            self._spawn(self.led.set_ambient(new_state))

    # =========================================================================
    # DISPLAY RENDERING
    # =========================================================================

    def _status_text(self):
        """Header text that replaces 'tap to talk' (None = default)."""
        if self.ui_state == UIState.LISTENING:
            return "listening..."
        if self.ui_state == UIState.PROCESSING:
            return "thinking..."
        if (self.ws_client.is_configured() and self._ws_down_since is not None
                and time.time() - self._ws_down_since > OFFLINE_GRACE_S):
            return "offline"
        return None

    def _render_normal(self, force=False):
        """Render the normal UI to the framebuffer — only if something changed.
        Blocking (200-500 ms on a Pi Zero): call via _in_thread()."""
        with self._render_lock:
            self._update_time()
            status = self._status_text()
            weather = self._weather_for_display()
            music = self.music_data
            unread = self.message_mgr.get_unread_counts()
            hang = format_countdown_display()
            art = self.album_art_path

            if status in ("listening...", "thinking..."):
                # During a voice session repaint once per state, not per clock tick
                sig = ('voice', status)
            else:
                sig = (
                    status,
                    self.time_data['time_str'], self.time_data['date_str'],
                    self.time_data['seconds'] // 10,
                    json.dumps(weather, sort_keys=True, default=str),
                    json.dumps(music and {k: music.get(k) for k in
                                          ('artist', 'album', 'track', 'listeners', 'pushed_by')},
                               sort_keys=True, default=str),
                    art,
                    tuple(self.contacts), tuple(sorted(unread.items())),
                    json.dumps(hang, sort_keys=True, default=str),
                )
            if not force and sig == self._last_render_sig:
                return False

            img = self.display.render(
                weather, self.time_data, list(self.contacts), music,
                album_art_path=art, unread_counts=unread,
                status_text=status, hang=hang,
            )
            write_to_framebuffer(img)
            self._last_render_sig = sig
            return True

    # =========================================================================
    # TAP HANDLING
    # =========================================================================

    async def _on_tap(self, tap_type):
        """Tap events from TapManager. Callbacks can overlap, so every guard
        flag is set BEFORE the first await."""
        print(f"[BRAIN] Tap: {tap_type}")
        try:
            if tap_type == 'single_tap':
                await self._handle_single_tap()
            elif tap_type == 'double_tap':
                await self._handle_double_tap()
            elif tap_type == 'triple_tap':
                await self._handle_triple_tap()
        except Exception as e:
            print(f"[BRAIN] Tap handler error: {e}")
            traceback.print_exc()

    async def _handle_single_tap(self):
        """Single tap — dismiss the open frame, else start a voice session"""
        if self._voice_busy:
            return   # one voice session at a time (two arecord processes corrupt I2S)
        if self._frame_active():
            self.dismiss_frame()
            return

        self._voice_busy = True   # before any await
        try:
            await self.led.ack()
            if self.voice and self.intent_router:
                await self._voice_interaction()
            elif self.voice:
                await self._voice_only()
            else:
                self._voice_busy = False
                self.show_frame(FrameType.WEATHER, [
                    ("voice is off", "large", COLORS['tan']),
                    ("", None, None),
                    *format_display_text("add DEEPGRAM_API_KEY to the .env file", COLORS['white']),
                ], duration=10.0)
        finally:
            self._voice_busy = False

    async def _handle_double_tap(self):
        """Double tap — heart reaction (message view) or nudge"""
        if self._voice_busy:
            return
        if self.message_view_active:
            print("[BRAIN] Double tap → heart reaction")
            ok = await self.ws_client.send_reaction('love')
            await (self.led.ack() if ok else self.led.error())
        elif self._frame_active():
            # A frame that is not a crew message is open (weather, "sent"...).
            # A nudge from here would surprise the crew with a 30 s LED signal.
            print("[BRAIN] Double tap ignored (a frame is open)")
        else:
            print("[BRAIN] Double tap → nudge")
            await self._send_nudge()

    async def _handle_triple_tap(self):
        """Triple tap — fire reaction (only during message view)"""
        if self._voice_busy or not self.message_view_active:
            return
        print("[BRAIN] Triple tap → fire reaction")
        ok = await self.ws_client.send_reaction('fire')
        await (self.led.ack() if ok else self.led.error())

    async def _send_nudge(self, from_voice=False):
        ok = self._connected() and await self.ws_client.send_nudge()
        if ok:
            self._led_notify(lambda: self.led.nudge(duration=3))
            self.show_frame(FrameType.MESSAGES, [
                ("nudge sent", "large", COLORS['lavender']),
            ], duration=4.0, from_voice=from_voice)
        else:
            self._led_notify(self.led.error)
            self._show_offline("nudge not sent", from_voice=from_voice)

    def _show_offline(self, title, from_voice=True):
        self.show_frame(FrameType.MESSAGES, [
            (title, "large", COLORS['rose']),
            ("", None, None),
            *format_display_text("no link to your crew right now. check wifi and try again.",
                                 COLORS['white']),
        ], duration=10.0, from_voice=from_voice)

    # =========================================================================
    # VOICE INTERACTION
    # =========================================================================

    async def _voice_only(self):
        """No intent router — transcribe and show the text."""
        try:
            self.ui_state = UIState.LISTENING
            self._wake_display()
            await self.led.listening()
            transcript = await self.voice.record_and_transcribe()
            if transcript:
                self.show_frame(FrameType.WEATHER, [
                    ("you said:", "large", COLORS['tan']),
                    ("", None, None),
                    *format_display_text(transcript, COLORS['white']),
                    ("", None, None),
                    *format_display_text("commands are off: add ANTHROPIC_API_KEY to the .env file.",
                                         COLORS['rose']),
                ], duration=12.0, from_voice=True)
            else:
                self.show_frame(FrameType.WEATHER, self._no_transcript_lines(),
                                duration=6.0, from_voice=True)
        finally:
            await asyncio.shield(self.led.off())
            self._end_voice_session()

    def _end_voice_session(self):
        """Return the UI to normal after a voice session (every exit path)."""
        if self.ui_state in VOICE_STATES:
            self.ui_state = UIState.NORMAL
        self._voice_busy = False
        # A frame that arrived during the session (message, nudge, hang...)
        # is shown now — after the voice result frame, if there is one.
        if self._deferred_frames and not self._deferred_runner_active:
            self._deferred_runner_active = True
            self._spawn(self._show_deferred())
        self._wake_display()

    async def _show_deferred(self):
        """Show the frames that were held back, one after the other, each
        after the frame before it has closed."""
        try:
            while self._deferred_frames and not self._voice_busy:
                current = self._expand_task
                if current is not None and not current.done():
                    await asyncio.gather(current, return_exceptions=True)
                    continue
                if self._voice_busy or not self._deferred_frames:
                    break     # a new voice session: it shows them when it ends
                self.show_frame(*self._deferred_frames.pop(0))
                await asyncio.sleep(0)
        finally:
            self._deferred_runner_active = False

    def _no_transcript_lines(self):
        """What to show when the recording gave no text."""
        return self._voice_failure_lines() or [
            ("didn't catch that", "large", COLORS['tan']),
            ("", None, None),
            *format_display_text("tap, wait for the green light, then speak.", COLORS['white']),
        ]

    def _voice_failure_lines(self):
        """Explain an empty transcript when the cause is a fault (not silence)."""
        error = getattr(self.voice, 'last_error', None)
        text = {
            'network': "can't reach the voice service. check wifi and try again.",
            'auth': "the voice service refused the key. check DEEPGRAM_API_KEY.",
            'mic': "the microphone gave no sound. restart leeloo if this continues.",
        }.get(error)
        if not text:
            return None
        return [
            ("can't hear you", "large", COLORS['rose']),
            ("", None, None),
            *format_display_text(text, COLORS['white']),
        ]

    async def _voice_interaction(self):
        """Full voice interaction: listen → transcribe → route → act.
        The caller has set _voice_busy. Every exit path (also cancellation)
        returns the UI and the LEDs to the normal state."""
        led_released = False
        try:
            # Ambient LED fully off before any audio (DMA conflict with I2S)
            await self.led._cancel_ambient()

            self.ui_state = UIState.LISTENING
            self._wake_display()
            await self.led.listening()
            print("[BRAIN] Listening...")

            transcript = await self.voice.record_and_transcribe()
            print(f"[BRAIN] Transcript: '{transcript}'")

            if not transcript:
                led_released = True
                await self.led.error(end_recording=True)
                self.show_frame(FrameType.WEATHER, self._no_transcript_lines(),
                                duration=6.0 if self.voice.last_error is None else 10.0,
                                from_voice=True)
                return

            self.ui_state = UIState.PROCESSING
            self._wake_display()
            self.led.start_processing()   # stopped by led.off() / led.error()

            # Give Claude fresh music context if the data is old, but a person
            # is waiting: allow Spotify half a second at most.
            if time.time() - self.last_music_fetch > 60:
                self.last_music_fetch = 0
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._in_thread(self._refresh_data)), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

            intent = await self.intent_router.route(transcript)

            led_released = True
            await self.led.off()
            # The session stays "busy" (header: "thinking...") until the result
            # is on the screen. A tap in this time must not start a second
            # session, and frames from other sources stay deferred.
            await self._execute_intent(intent)

        except Exception as e:
            print(f"[BRAIN] Voice error: {e}")
            traceback.print_exc()
            led_released = True
            await self.led.error(end_recording=True)
        finally:
            if not led_released:
                # Cancelled while the LED showed listening/processing
                await asyncio.shield(self.led.off())
            self._end_voice_session()

    async def _execute_intent(self, intent):
        """Execute a classified intent. Every branch gives feedback on the screen."""
        action = intent.action
        params = intent.params if isinstance(intent.params, dict) else {}
        show = lambda frame, lines, duration=EXPANDED_HOLD_DURATION: \
            self.show_frame(frame, lines, duration, from_voice=True)

        if action == "WEATHER_EXPAND":
            weather = self.weather_data if self._weather_is_fresh() else None
            lines = format_display_text(intent.response_text, COLORS['tan'])
            rain = build_rain_viz(weather)
            if rain:
                lines = lines + [("", None, None)] + rain
            show(FrameType.WEATHER, lines)

        elif action == "ALBUM_INFO":
            show(FrameType.ALBUM, format_display_text(intent.response_text, COLORS['green']))

        elif action == "MESSAGE_SEND":
            msg = str(params.get('message', '') or '').strip()
            if not msg:
                show(FrameType.MESSAGES, [
                    ("say it again?", "large", COLORS['lavender']),
                    ("", None, None),
                    *format_display_text("i didn't catch the message.", COLORS['white']),
                ], 8.0)
            elif self._connected() and await self.ws_client.send_message(msg):
                show(FrameType.MESSAGES, [
                    ("message sent", "large", COLORS['lavender']),
                    ("", None, None),
                    *format_display_text(msg, COLORS['white']),
                ], 10.0)
            else:
                self._show_offline("message not sent")

        elif action == "MESSAGE_READOUT":
            messages = self.message_mgr.get_history_24h()
            lines = [("messages", "large", COLORS['lavender']), ("", None, None)]
            for m in reversed(messages):   # oldest first, like a chat
                lines.append((str(m.get('sender', '?')).lower(), "small", COLORS['lavender']))
                lines.extend(format_display_text(
                    clean_display_text(m.get('text', ''), MAX_MESSAGE_CHARS), COLORS['white']))
                lines.append(("", None, None))
            if not messages:
                lines.append(("no messages", "normal", COLORS['white']))
            show(FrameType.MESSAGES, lines)
            self.message_mgr.mark_all_read()

        elif action == "NUDGE":
            await self._send_nudge(from_voice=True)

        elif action == "SONG_PUSH":
            await self._intent_song_push(params, show)

        elif action == "HANG_PROPOSE":
            await self._intent_hang_propose(intent, params, show)

        elif action == "HANG_CONFIRM":
            pending_dt, proposed_by = self._pending_hang()
            target = pending_dt or self._next_hang()
            if target is None:
                show(FrameType.MESSAGES, [
                    ("no hang to confirm", "large", COLORS['lavender']),
                    ("", None, None),
                    *format_display_text('say "let\'s hang saturday at 8" to propose one.',
                                         COLORS['white']),
                ], 10.0)
            elif pending_dt is not None and proposed_by == 'me':
                # You cannot confirm your own proposal
                show(FrameType.MESSAGES, [
                    ("waiting for your crew", "large", COLORS['lavender']),
                    ("", None, None),
                    (self._hang_label(pending_dt), "normal", COLORS['purple']),
                    *format_display_text('you proposed this. a friend must say "i\'m in".',
                                         COLORS['white']),
                ], 10.0)
            elif self._connected() and await self.ws_client.send_hang_confirm():
                self._led_notify(self.led.ack)
                self._save_pending_hang()
                show(FrameType.MESSAGES, [
                    ("you're in!", "large", COLORS['lavender']),
                    ("", None, None),
                    (self._hang_label(target), "normal", COLORS['purple']),
                ], 10.0)
            else:
                self._show_offline("not confirmed")

        elif action == "TELEGRAM_SETUP":
            lines = [
                ("telegram setup", "large", COLORS['lavender']),
                ("", None, None),
                *format_display_text("message @Leeloo2259_bot on Telegram and send /start",
                                     COLORS['white']),
                ("", None, None),
            ]
            if self._crew_code():
                lines.append(("your crew code:", "normal", COLORS['green']))
                lines.append((self._crew_code(), "large", COLORS['green']))
            show(FrameType.MESSAGES, lines)

        elif action == "SPOTIFY_SETUP":
            self._show_spotify_qr()

        elif action == "TIME_QUERY":
            time_text = await self._generate_cosmic_time()
            now = self._now()
            show(FrameType.TIME, [
                (now.strftime('%-I:%M %p').lower(), "large", COLORS['purple']),
                (now.strftime('%A, %b %-d').lower(), "normal", COLORS['purple']),
                ("", None, None),
                *format_display_text(time_text, COLORS['white']),
            ])

        else:   # UNKNOWN or anything unexpected
            text = intent.response_text or 'not sure what you mean. try "what\'s the weather".'
            show(FrameType.WEATHER, format_display_text(text, COLORS['tan']), 10.0)

    async def _intent_song_push(self, params, show):
        if params.get('current'):
            music = self.music_data or {}
            if not music.get('spotify_uri'):
                show(FrameType.ALBUM, [
                    ("nothing to share", "large", COLORS['green']),
                    ("", None, None),
                    *format_display_text("no song is playing. say the song name to share it.",
                                         COLORS['white']),
                ], 10.0)
                return
            result = music
        else:
            query = str(params.get('query', '') or '').strip()
            print(f"[BRAIN] Song push search: '{query}'")
            result = await self._in_thread(music_manager.search_and_save_shared, query, 'You') \
                if query else None
            if not result and not music_manager.spotify_client_secret():
                show(FrameType.ALBUM, [
                    ("spotify is off", "large", COLORS['rose']),
                    ("", None, None),
                    *format_display_text("SPOTIFY_CLIENT_SECRET is missing in the .env file.",
                                         COLORS['white']),
                ], 12.0)
                return
            if not result:
                show(FrameType.ALBUM, [
                    ("couldn't find it", "large", COLORS['green']),
                    ("", None, None),
                    *format_display_text(f"searched for: {query or '?'}", COLORS['white']),
                    ("", None, None),
                    *format_display_text("try the artist and the song title.", COLORS['white']),
                ], 10.0)
                return
            self.last_music_fetch = 0

        sent = self._connected() and await self.ws_client.push_song(
            result.get('spotify_uri', ''), result.get('artist', ''),
            result.get('track', ''), result.get('album', ''),
            album_art_url=result.get('album_art_url', '') or '')
        title = f"{result.get('artist', '')} - {result.get('track', '')}"
        if sent:
            show(FrameType.ALBUM, [
                ("song shared!", "large", COLORS['green']),
                ("", None, None),
                *format_display_text(title, COLORS['white']),
            ], 10.0)
        else:
            show(FrameType.ALBUM, [
                ("not shared", "large", COLORS['rose']),
                ("", None, None),
                *format_display_text(title, COLORS['white']),
                ("", None, None),
                *format_display_text("no link to your crew right now.", COLORS['white']),
            ], 10.0)

    async def _intent_hang_propose(self, intent, params, show):
        dt = self._parse_hang(str(params.get('datetime', '') or ''))
        if dt is None:
            show(FrameType.MESSAGES, [
                ("when?", "large", COLORS['lavender']),
                ("", None, None),
                *format_display_text('i didn\'t catch the time. try "let\'s hang saturday at 8".',
                                     COLORS['white']),
            ], 10.0)
            return
        now = self._now()
        if dt < now and (now - dt).days < 1:
            # "tonight at 8" said at 9 pm. Do not move it to next year.
            show(FrameType.MESSAGES, [
                ("that time has passed", "large", COLORS['lavender']),
                ("", None, None),
                (self._hang_label(dt), "normal", COLORS['purple']),
                *format_display_text("say a time that is still to come.", COLORS['white']),
            ], 10.0)
            return
        dt = roll_forward(dt, now)      # the model answered with a past YEAR
        desc = str(params.get('description', '') or '')
        print(f"[BRAIN] Hang propose: {dt.isoformat()} {desc}")
        if self._connected() and await self.ws_client.send_hang_propose(dt.isoformat(), desc):
            self._set_pending_hang(dt, 'me')
            show(FrameType.MESSAGES, [
                ("hang proposed!", "large", COLORS['lavender']),
                ("", None, None),
                (self._hang_label(dt), "normal", COLORS['purple']),
                *format_display_text(intent.response_text, COLORS['white']),
            ])
        else:
            self._show_offline("hang not sent")

    # =========================================================================
    # CLAUDE HELPERS
    # =========================================================================

    async def _detect_music_in_message(self, text):
        """Return a Spotify search query (or track URI) if the message names music."""
        uri = spotify_uri_in_text(text)
        if uri:
            return uri
        client = self.intent_router.client if self.intent_router else None
        if not client or not looks_like_music(text):
            return None
        try:
            raw = await self._in_thread(ask_claude, client, MUSIC_DETECT_SYSTEM, text, 100)
            result = extract_json(raw)
            if result.get('music') and isinstance(result.get('query'), str):
                return result['query'].strip() or None
        except Exception as e:
            print(f"[BRAIN] Music detection classify error: {e}")
        return None

    async def _generate_cosmic_time(self):
        """Tolkien-style description of the current date and time"""
        now = self._now()
        fallback = f"it is {now.strftime('%-I:%M %p')} on {now.strftime('%A, %B %-d')}"
        client = self.intent_router.client if self.intent_router else None
        if not client:
            return fallback
        where = []
        if self.device_config.get('zip_code'):
            where.append(f"ZIP {self.device_config['zip_code']}")
        if self.location_configured:
            where.append(f"lat {float(self.latitude):.2f}, lon {float(self.longitude):.2f}")
        prompt = (
            f"Current date: {now.strftime('%B %d, %Y')}\n"
            f"Current time: {now.strftime('%I:%M %p')}\n"
            f"Day of week: {now.strftime('%A')}\n"
            f"Location: {' '.join(where) if where else 'unknown (keep nature examples general)'}"
        )
        try:
            text = await self._in_thread(ask_claude, client, COSMIC_TIME_SYSTEM, prompt, 250)
            return text or fallback
        except Exception as e:
            print(f"[BRAIN] Cosmic time error: {e}")
            return fallback

    # =========================================================================
    # MAIN LOOPS
    # =========================================================================

    async def _display_loop(self):
        """Repaints the normal screen when its content changed (checked every
        second, or at once after _wake_display())."""
        print("[BRAIN] Display loop started")
        while True:
            try:
                idle = self.ui_state == UIState.NORMAL and not self._frame_active()
                if idle or self.ui_state in VOICE_STATES:
                    await self._in_thread(self._render_normal)
            except Exception as e:
                print(f"[BRAIN] Display error: {e}")
            try:
                await asyncio.wait_for(self._display_wake.wait(), DISPLAY_REFRESH_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._display_wake.clear()

    async def _data_loop(self):
        """Keeps weather and music current. The network calls run in a worker
        thread, so a slow network never stops the clock or the taps."""
        while True:
            try:
                if not self._voice_busy:
                    await self._in_thread(self._refresh_data)
                    self._sync_ambient_led()
            except Exception as e:
                print(f"[BRAIN] Data loop error: {e}")
            await asyncio.sleep(1.0)

    def _may_create_crew(self):
        """May this device register its crew code on the server?
        Yes if it created the crew, or if it was a member before."""
        return bool(self.crew_config.get('is_creator') or self.ws_client.config.crew_id)

    async def _ws_connection_loop(self):
        """WebSocket connection with auto-reconnect"""
        retry_delay = 5
        max_delay = 60
        self._ws_down_since = time.time()

        while True:
            try:
                print(f"[BRAIN] Connecting to relay ({RELAY_URL})...")
                if await self.ws_client.connect():
                    joined = await self.ws_client.rejoin_crew()
                    if not joined and self.ws_client.connected:
                        if self._may_create_crew():
                            # Our crew is unknown to the server (for example after
                            # the server lost its state) — register the same code.
                            display_name = self.user_name or 'LEELOO'
                            joined = bool(await self.ws_client.create_crew(display_name))
                        elif not self._crew_error_shown:
                            # A crew code typed at setup that the server does not
                            # know. Do NOT create it: the user would sit alone in
                            # a new crew and never know.
                            self._crew_error_shown = True
                            self.show_frame(FrameType.MESSAGES, [
                                ("crew not found", "large", COLORS['rose']),
                                ("", None, None),
                                *format_display_text(
                                    f"no crew with the code {self._crew_code()}. check the code "
                                    "with your friend, then run setup again.", COLORS['white']),
                            ], duration=30.0)
                    if joined:
                        print("[BRAIN] WebSocket connected and crew joined!")
                        session_start = time.time()
                        self._ws_down_since = None
                        self._wake_display()

                        listen_task = asyncio.create_task(self.ws_client.listen())
                        keepalive_task = asyncio.create_task(self.ws_client.keepalive())
                        try:
                            await asyncio.wait(
                                [listen_task, keepalive_task],
                                return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for task in (listen_task, keepalive_task):
                                task.cancel()
                        print("[BRAIN] WebSocket disconnected")
                        # Reset the backoff only after a stable session. Two
                        # devices with the same id replace each other at once;
                        # without this they would reconnect every 5 s forever.
                        if time.time() - session_start >= 60:
                            retry_delay = 5
                    else:
                        print("[BRAIN] Could not join crew")
                else:
                    print("[BRAIN] WebSocket connection failed")
            except Exception as e:
                print(f"[BRAIN] WebSocket error: {e}")

            if self._ws_down_since is None:
                self._ws_down_since = time.time()
            try:
                await self.ws_client.disconnect()
            except Exception:
                pass

            print(f"[BRAIN] Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)

    # =========================================================================
    # FIRST BOOT WELCOME
    # =========================================================================

    def _send_first_boot_welcome(self):
        """Store a welcome message on first boot after setup.
        Returns True if this is the first boot (welcome not yet sent)."""
        welcome_flag = os.path.join(LEELOO_HOME, '.welcome_sent')
        if os.path.exists(welcome_flag) or not self.user_name:
            return False

        welcome = f"welcome {self.user_name}!"
        if self._crew_code():
            welcome += f" your crew code is {self._crew_code()}."
        if self.device_config.get('telegram_opted_in', False):
            welcome += " say 'telegram setup' for help anytime"

        self.message_mgr.add_message("leeloo", welcome)
        try:
            with open(welcome_flag, 'w') as f:
                f.write('1')
        except OSError:
            pass
        return True

    def _generate_spotify_oauth_url(self):
        """Spotify OAuth URL for the QR code. `state` MUST be the device_id
        that the relay server knows — the server uses it to route the tokens."""
        from urllib.parse import quote
        device_id = self.ws_client.config.device_id
        if not device_id:
            print("[BRAIN] WARNING: No device_id yet for Spotify OAuth URL")
            device_id = 'leeloo_unknown'
        redirect_uri = "https://leeloobot.xyz/spotify/callback"
        scopes = "user-read-currently-playing user-read-playback-state"
        return (
            "https://accounts.spotify.com/authorize?"
            f"client_id={music_manager.spotify_client_id()}&"
            "response_type=code&"
            f"redirect_uri={quote(redirect_uri)}&"
            f"scope={quote(scopes)}&"
            f"state={quote(device_id)}&"
            "show_dialog=true"
        )

    def _generate_qr_image(self, qr_url, label_text, sublabel_text, label_color, hint_text=None):
        """Generate a 243x304 image with a QR code for the album art box.
        Returns the image path, or None on failure. Blocking."""
        try:
            import qrcode

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
            qr_size = min(W - 20, 200)
            qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST).convert('RGB')
            qr_y = 15
            img.paste(qr_img, ((W - qr_size) // 2, qr_y))

            y = qr_y + qr_size + 10
            for text, color in ((label_text, label_color), (sublabel_text, label_color),
                                (hint_text, HINT_COLOR)):
                if not text:
                    continue
                try:
                    tw = self.font_small.getlength(text)
                except AttributeError:
                    tw = len(text) * 7
                draw.text(((W - tw) // 2, y), text, font=self.font_small, fill=color)
                y += 22

            qr_path = os.path.join(LEELOO_HOME, '.welcome_qr.png')
            img.save(qr_path, 'PNG')
            return qr_path
        except Exception as e:
            print(f"[BRAIN] QR image generation failed: {e}")
            return None

    async def _qr_frame(self, make_qr_args, lines, duration=60.0):
        """Show a QR code in the album art box together with an expanded frame.
        The album art is restored when the frame closes (or is dismissed).
        Must run inside the current frame task."""
        self._welcome_qr_active = True      # the music refresh must not replace the QR
        original_art_path = self.album_art_path
        try:
            qr_path = await self._in_thread(self._generate_qr_image, *make_qr_args)
            if qr_path:
                self.album_art_path = qr_path
                await self._in_thread(self._render_normal, True)
            await self.expand_frame(FrameType.MESSAGES, lines, duration=duration)
        finally:
            self._welcome_qr_active = False
            sharing_img = os.path.join(LEELOO_HOME, 'leeloo_for_sharing.png')
            if original_art_path and os.path.exists(original_art_path):
                self.album_art_path = original_art_path
            elif os.path.exists(sharing_img):
                self.album_art_path = sharing_img
            else:
                self.album_art_path = None
            self.last_music_fetch = 0
            try:
                os.remove(os.path.join(LEELOO_HOME, '.welcome_qr.png'))
            except OSError:
                pass
            self._wake_display()

    async def _spotify_qr_frame(self, intro="connect spotify"):
        """QR code that links the user's Spotify account (60 s, tap to skip)."""
        if not self.ws_client.config.device_id:
            # The server routes the tokens by device id. Without it the QR
            # would send the tokens nowhere.
            await self.expand_frame(FrameType.MESSAGES, [
                ("not ready yet", "large", COLORS['rose']),
                ("", None, None),
                *format_display_text("no link to the leeloo server. check wifi, then say "
                                     '"connect spotify".', COLORS['white']),
            ], duration=10.0)
            return
        await self._qr_frame(
            (self._generate_spotify_oauth_url(), "scan to sync", "your Spotify", COLORS['green']),
            [
                (intro, "large", COLORS['green']),
                ("", None, None),
                ("scan QR to sync", "normal", COLORS['green']),
                ("your Spotify", "large", COLORS['green']),
                ("", None, None),
                ("shows what you're", "small", COLORS['white']),
                ("listening to now", "small", COLORS['white']),
                ("", None, None),
                ("tap device to skip", "small", HINT_COLOR),
            ])

    def _show_spotify_qr(self):
        """Voice command "connect spotify"."""
        if self._has_live_spotify():
            self.show_frame(FrameType.ALBUM, [
                ("spotify is connected", "large", COLORS['green']),
            ], duration=6.0, from_voice=True)
        else:
            self._start_frame_task(self._spotify_qr_frame)

    async def _show_welcome_with_qr(self):
        """First-boot welcome:
        Phase 1: Telegram QR (if the user chose Telegram at setup) — 60s
        Phase 2: Spotify QR (if Spotify is not connected) — 60s
        Later: say "connect spotify" or "telegram setup" to see them again."""
        crew_code = self._crew_code()
        if self.device_config.get('telegram_opted_in', False) and crew_code:
            await self._qr_frame(
                (f"https://t.me/Leeloo2259_bot?start={crew_code.replace('-', '')}",
                 "scan for Telegram", crew_code, COLORS['lavender']),
                [
                    (f"welcome {self.user_name}!", "large", COLORS['lavender']),
                    ("", None, None),
                    (f"crew: {crew_code}", "normal", COLORS['green']),
                    ("share with friends", "small", COLORS['white']),
                    ("", None, None),
                    ("scan QR to connect", "normal", COLORS['tan']),
                    ("Telegram", "large", COLORS['tan']),
                    ("", None, None),
                    ('say "telegram setup"', "small", COLORS['lavender']),
                    ("to see this again", "small", COLORS['lavender']),
                ])

        await self._offer_spotify()

    async def _offer_spotify(self):
        """Offer the Spotify QR once (also after a skipped welcome)."""
        if self._has_live_spotify():
            return
        # The relay connection supplies the device id for the QR. Wait a little.
        for _ in range(20):
            if self.ws_client.config.device_id:
                break
            await asyncio.sleep(0.5)
        if self.ws_client.config.device_id:
            try:
                with open(SPOTIFY_OFFERED_FLAG, 'w') as f:
                    f.write('1')
            except OSError:
                pass
            await self._spotify_qr_frame(intro="one more thing...")

    # =========================================================================
    # RUN
    # =========================================================================

    async def run(self):
        """Main entry point — start all subsystems"""
        print("[BRAIN] Starting LEELOO Brain...")
        print(f"[BRAIN] Crew: {self.contacts}")
        print(f"[BRAIN] Location: {'configured' if self.location_configured else 'not set'}")

        # Move console off LCD
        os.system("sudo con2fbmap 1 0 2>/dev/null")

        # First paint at once (no network), then the loops fill in the data
        await self._in_thread(self._render_normal, True)

        self._led_ambient_state = "idle"
        await self.led.set_ambient("idle")

        is_first_boot = self._send_first_boot_welcome()

        tasks = [
            asyncio.create_task(self._display_loop()),
            asyncio.create_task(self._data_loop()),
            asyncio.create_task(self.tap.start()),
        ]
        if self.ws_client.is_configured():
            tasks.append(asyncio.create_task(self._ws_connection_loop()))

        print("[BRAIN] All subsystems started. LEELOO is alive!")

        if is_first_boot:
            self._start_frame_task(self._show_welcome_with_qr)
        elif not self._has_live_spotify() and not os.path.exists(SPOTIFY_OFFERED_FLAG):
            # The welcome was skipped before the Spotify step: offer it once more
            self._start_frame_task(self._offer_spotify)

        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            self.tap.stop()
            self.led.off_sync()
            try:
                await self.ws_client.disconnect()
            except Exception:
                pass
            print("[BRAIN] Goodbye!")


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Entry point. A fatal error exits with code 1 so that systemd
    (Restart=on-failure) starts LEELOO again."""
    try:
        brain = LeelooBrain()
        asyncio.run(brain.run())
    except KeyboardInterrupt:
        print("\n[BRAIN] Interrupted")
    except Exception as e:
        print(f"[BRAIN] Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
