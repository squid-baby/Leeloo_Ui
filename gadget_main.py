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

from gadget_display import LEELOODisplay
from gadget_weather import get_weather
from leeloo_data import format_countdown_display, load_data

# Config file path
CONFIG_PATH = os.environ.get("GADGET_CONFIG_PATH", "/home/pi/leeloo_config.json")
ALBUM_ART_PATH = "/home/pi/doorways-album.jpg"


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


def check_setup_needed():
    """Check if first-time setup is needed"""
    if not os.path.exists(CONFIG_PATH):
        return True
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        # Check for required fields
        required = ['user_name', 'contacts', 'location']
        for field in required:
            if not config.get(field):
                return True
        return not config.get('setup_complete', False)
    except:
        return True


def run_captive_portal_setup():
    """Run the captive portal setup flow"""
    from gadget_setup import lcd_update_handler
    from captive_portal import run_captive_portal

    print("Starting captive portal setup...")
    run_captive_portal(lcd_update_callback=lcd_update_handler)

def main_loop():
    """Main display loop"""
    print("Starting LEELOO main loop...")

    # Move console off LCD
    os.system("sudo con2fbmap 1 0 2>/dev/null")

    display = LEELOODisplay(preview_mode=True)

    # Cache weather data (refresh every 10 minutes)
    weather_data = None
    last_weather_fetch = 0
    WEATHER_REFRESH_INTERVAL = 600  # 10 minutes

    # Load contacts from config
    try:
        import json
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
        contacts = config.get('contacts', ['Amy', 'Ben'])
    except:
        contacts = ['Amy', 'Ben']

    # Album data (will be dynamic later)
    album_data = {
        'artist': 'Cinnamon Chasers',
        'track': 'Doorways',
        'bpm': 120,
        'listeners': '262K',
        'pushed_by': 'Amy',
    }

    while True:
        try:
            # Refresh weather if needed
            current_time = time.time()
            if weather_data is None or (current_time - last_weather_fetch) > WEATHER_REFRESH_INTERVAL:
                try:
                    weather_data = get_weather()
                    last_weather_fetch = current_time
                    print(f"Weather updated: {weather_data['temp_f']}°F")
                except Exception as e:
                    print(f"Weather fetch failed: {e}")
                    if weather_data is None:
                        weather_data = {'temp_f': 72, 'uv_raw': 5, 'rain_24h_inches': 0}

            # Time data
            now = datetime.now()
            time_data = {
                'time_str': now.strftime('%-I:%M %p'),
                'date_str': now.strftime('%b %-d'),
                'seconds': now.second,
            }

            # Render
            img = display.render(
                weather_data,
                time_data,
                contacts,
                album_data,
                album_art_path=ALBUM_ART_PATH if os.path.exists(ALBUM_ART_PATH) else None
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
            time.sleep(5)

def main():
    """Entry point"""
    # Move console off LCD first
    os.system("sudo con2fbmap 1 0 2>/dev/null")

    # Check if setup is needed
    if check_setup_needed():
        print("First-time setup required...")
        run_captive_portal_setup()
        # After setup completes, reload config and continue to main loop

    # Run main loop
    main_loop()


if __name__ == "__main__":
    main()
