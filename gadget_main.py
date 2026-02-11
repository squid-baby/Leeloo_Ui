#!/usr/bin/env python3
"""
LEELOO Main - Main loop that renders UI to LCD
Runs continuously, updating display every second
"""

import os
import sys
import json
import time
import struct
from datetime import datetime
from zoneinfo import ZoneInfo

from gadget_display import LeelooDisplay
from gadget_weather import get_weather

# Config paths
LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, "crew_config.json")
ALBUM_ART_DIR = os.path.join(LEELOO_HOME, "album_art")

# Weather refresh interval
WEATHER_REFRESH_INTERVAL = 600  # 10 minutes


def rgb_to_rgb565(r, g, b):
    """Convert RGB888 to RGB565 for LCD framebuffer"""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def write_to_framebuffer(img, fb_path="/dev/fb1"):
    """Write PIL image to LCD framebuffer in RGB565 format"""
    with open(fb_path, 'wb') as fb:
        for y in range(320):
            for x in range(480):
                r, g, b = img.getpixel((x, y))
                pixel = rgb_to_rgb565(r, g, b)
                fb.write(struct.pack('H', pixel))


def load_device_config():
    """Load device configuration (location, etc.)"""
    try:
        with open(DEVICE_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_crew_config():
    """Load crew configuration (contacts, etc.)"""
    try:
        with open(CREW_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_current_music():
    """Load currently playing/shared music data"""
    music_state_path = os.path.join(LEELOO_HOME, "current_music.json")
    try:
        with open(music_state_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_album_art_path(music_data):
    """Get path to album art for current music"""
    if not music_data:
        return None

    # Check for cached album art
    if 'album_art_cached' in music_data:
        cached_path = music_data['album_art_cached']
        if os.path.exists(cached_path):
            return cached_path

    # Check album art directory
    if 'spotify_uri' in music_data:
        # Use URI hash as filename
        import hashlib
        uri_hash = hashlib.md5(music_data['spotify_uri'].encode()).hexdigest()[:12]
        art_path = os.path.join(ALBUM_ART_DIR, f"{uri_hash}.jpg")
        if os.path.exists(art_path):
            return art_path

    return None


def main_loop():
    """Main display loop"""
    print("Starting LEELOO main loop...")

    # Move console off LCD
    os.system("sudo con2fbmap 1 0 2>/dev/null")

    display = LeelooDisplay(preview_mode=False)

    # Cache weather data
    weather_data = None
    last_weather_fetch = 0

    # Load initial configs
    device_config = load_device_config()
    crew_config = load_crew_config()

    # Get location for weather (if configured)
    latitude = device_config.get('latitude')
    longitude = device_config.get('longitude')
    location_configured = latitude is not None and longitude is not None

    # Get timezone from config (set during captive portal setup from ZIP code)
    tz_name = device_config.get('timezone')
    try:
        local_tz = ZoneInfo(tz_name) if tz_name else None
    except (KeyError, Exception):
        local_tz = None
    if local_tz:
        print(f"Using timezone: {tz_name}")
    else:
        print("No timezone configured, using system time")

    print(f"Device config: {device_config}")
    print(f"Crew config: {crew_config}")
    print(f"Location configured: {location_configured}")

    while True:
        try:
            current_time = time.time()

            # ===== WEATHER =====
            # Only fetch if location is configured
            if location_configured:
                if weather_data is None or (current_time - last_weather_fetch) > WEATHER_REFRESH_INTERVAL:
                    try:
                        weather_data = get_weather(latitude, longitude, timezone=tz_name)
                        last_weather_fetch = current_time
                        print(f"Weather updated: {weather_data.get('temp_f', '?')}°F")
                    except Exception as e:
                        print(f"Weather fetch failed: {e}")
                        # Keep old data if we had it
            else:
                # No location - show empty state
                weather_data = None

            # ===== TIME =====
            # Use configured timezone from ZIP code, or fall back to system time
            now = datetime.now(tz=local_tz) if local_tz else datetime.now()
            time_data = {
                'time_str': now.strftime('%-I:%M %p'),
                'date_str': now.strftime('%b %-d'),
                'seconds': now.second,
            }

            # ===== CONTACTS =====
            # From crew config, or empty
            contacts = crew_config.get('members', [])

            # ===== MUSIC =====
            # From current music state, or empty
            music_data = load_current_music()
            if music_data:
                album_data = {
                    'artist': music_data.get('artist', ''),
                    'track': music_data.get('track', ''),
                    'bpm': music_data.get('bpm'),
                    'listeners': music_data.get('listeners'),
                    'pushed_by': music_data.get('pushed_by'),
                    'spotify_uri': music_data.get('spotify_uri'),
                }
                album_art_path = get_album_art_path(music_data)
            else:
                # No music shared yet - empty state
                album_data = None
                album_art_path = None

            # ===== RENDER =====
            img = display.render(
                weather_data,
                time_data,
                contacts,
                album_data,
                album_art_path=album_art_path
            )

            # Write to LCD
            write_to_framebuffer(img)

            # Update every second (for time slider animation)
            time.sleep(1)

        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)


def main():
    """Entry point - called by leeloo_boot.py after setup is complete"""
    # Move console off LCD first
    os.system("sudo con2fbmap 1 0 2>/dev/null")

    # Run main loop
    main_loop()


if __name__ == "__main__":
    main()
