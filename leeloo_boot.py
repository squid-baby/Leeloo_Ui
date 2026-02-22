#!/usr/bin/env python3
"""
LEELOO Boot Sequence
Handles the complete startup flow:
1. Splash screen (already shown by systemd)
2. WiFi check/setup (captive portal if first run)
3. Post-WiFi: crew registration, deferred geocoding
4. Main UI
"""

import os
import sys
import time
import json
import subprocess
import asyncio

# Add boot directory to path
sys.path.insert(0, '/home/pi/leeloo-ui/boot')
sys.path.insert(0, '/home/pi/leeloo-ui')

from boot.leeloo_splash import show_splash, clear_screen

# Configuration
LEELOO_HOME = '/home/pi/leeloo-ui'
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, 'device_config.json')
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, 'crew_config.json')
FIRST_RUN_COMPLETE = os.path.join(LEELOO_HOME, '.first_run_complete')
RELAY_URL = "wss://leeloobot.xyz/ws"


def load_json(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def is_first_run():
    if not os.path.exists(FIRST_RUN_COMPLETE):
        return True
    # Also treat as first run if setup was never completed (catches partial clean slates
    # that deleted device_config.json but left .first_run_complete behind)
    config = load_json(DEVICE_CONFIG_PATH)
    return not config.get('setup_complete', False)


def mark_first_run_complete():
    with open(FIRST_RUN_COMPLETE, 'w') as f:
        f.write('1')


def check_wifi_connected():
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except:
        return False


def get_wifi_ssid():
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.startswith('yes:'):
                    return line.split(':', 1)[1]
        result = subprocess.run(
            ['iwgetid', '-r'], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except:
        return None


def disconnect_wifi_for_setup():
    """
    Fully release wlan0 for hostapd AP mode.
    NetworkManager (via netplan) aggressively reclaims wlan0,
    so we must stop NM entirely during AP mode.
    """
    try:
        # Stop NetworkManager completely — it fights hostapd for wlan0
        subprocess.run(
            ['sudo', 'systemctl', 'stop', 'NetworkManager'],
            capture_output=True, timeout=10
        )
        # Also kill wpa_supplicant which NM may have spawned
        subprocess.run(
            ['sudo', 'killall', 'wpa_supplicant'],
            capture_output=True, timeout=5
        )
        print("[BOOT] NetworkManager stopped, wlan0 free for AP mode")
        time.sleep(1)
    except Exception as e:
        print(f"[BOOT] WiFi disconnect error: {e}")


def reconnect_wifi_after_setup():
    """Re-enable NetworkManager after AP mode is done."""
    try:
        subprocess.run(
            ['sudo', 'systemctl', 'start', 'NetworkManager'],
            capture_output=True, timeout=10
        )
        print("[BOOT] NetworkManager restarted")
        time.sleep(3)
    except Exception as e:
        print(f"[BOOT] WiFi reconnect error: {e}")


def run_first_run_screen():
    try:
        from leeloo_first_run import show_first_run
        show_first_run("leeloo")
        return True
    except Exception as e:
        print(f"First run screen error: {e}")
        return False


def start_captive_portal():
    try:
        from captive_portal import run_captive_portal
        run_captive_portal()
    except Exception as e:
        print(f"Captive portal error: {e}")
        try:
            from wifi_manager import start_ap_mode
            ssid = start_ap_mode()
            print(f"AP started via wifi_manager: {ssid}")
        except Exception as e2:
            print(f"Simple AP also failed: {e2}")


# ============================================
# POST-WIFI: Deferred geocoding
# ============================================

def do_deferred_geocoding():
    """
    After WiFi connects, convert ZIP -> lat/lon -> timezone.
    Updates device_config.json with coordinates and timezone.
    """
    config = load_json(DEVICE_CONFIG_PATH)
    zip_code = config.get('zip_code')

    # Skip if already geocoded or no ZIP
    if not zip_code or (config.get('latitude') and config.get('timezone')):
        return

    print(f"[BOOT] Deferred geocoding for ZIP {zip_code}...")

    try:
        import requests

        # ZIP -> lat/lon via Nominatim
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=US&format=json&limit=1"
        resp = requests.get(url, headers={'User-Agent': 'LEELOO/1.0'}, timeout=10)
        if resp.status_code != 200 or not resp.json():
            print(f"[BOOT] Geocoding failed for ZIP {zip_code}")
            return

        data = resp.json()[0]
        lat = float(data['lat'])
        lon = float(data['lon'])
        config['latitude'] = lat
        config['longitude'] = lon
        print(f"[BOOT] ZIP {zip_code} -> lat={lat}, lon={lon}")

        # lat/lon -> timezone via Open-Meteo
        tz_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m&forecast_days=1"
            f"&timezone=auto"
        )
        tz_resp = requests.get(tz_url, headers={'User-Agent': 'LEELOO/1.0'}, timeout=10)
        if tz_resp.status_code == 200:
            tz_data = tz_resp.json()
            timezone = tz_data.get('timezone')
            if timezone:
                config['timezone'] = timezone
                print(f"[BOOT] Timezone: {timezone}")

        save_json(DEVICE_CONFIG_PATH, config)
        print("[BOOT] Geocoding complete, config updated")

    except Exception as e:
        print(f"[BOOT] Geocoding error: {e}")


# ============================================
# POST-WIFI: Crew registration
# ============================================

def do_crew_registration():
    """
    After WiFi connects, register or join crew on the relay server.
    Reads crew_config.json written by the captive portal.
    """
    crew_config = load_json(CREW_CONFIG_PATH)
    if not crew_config:
        print("[BOOT] No crew config found, skipping registration")
        return

    invite_code = crew_config.get('invite_code')
    is_creator = crew_config.get('is_creator', False)
    device_config = load_json(DEVICE_CONFIG_PATH)
    device_name = device_config.get('user_name', 'LEELOO')

    if not invite_code:
        print("[BOOT] No invite code in crew config")
        return

    print(f"[BOOT] Crew registration: {'creating' if is_creator else 'joining'} {invite_code}")

    # Registration happens via WebSocket when leeloo_brain.py starts.
    # The LeelooClient reads crew_config.json and handles create/join.
    # We just need to make sure the config format is correct.

    # Ensure crew_code field exists (LeelooClient expects this)
    if 'crew_code' not in crew_config:
        crew_config['crew_code'] = invite_code
        save_json(CREW_CONFIG_PATH, crew_config)

    # Also ensure display_name is set
    if 'display_name' not in crew_config:
        crew_config['display_name'] = device_name
        save_json(CREW_CONFIG_PATH, crew_config)

    print(f"[BOOT] Crew config ready for relay connection")


# ============================================
# MAIN BOOT SEQUENCE
# ============================================

def run_main_ui():
    show_splash("Loading UI...", 90)
    time.sleep(0.5)
    try:
        from leeloo_brain import main as brain_main
        brain_main()
    except ImportError:
        try:
            from gadget_main import main as gadget_main
            gadget_main()
        except ImportError:
            print("No main UI available, running static display")
            run_static_display()
    except Exception as e:
        print(f"Main UI error: {e}")
        show_splash(f"UI error: {str(e)[:40]}", 90)
        time.sleep(5)


def run_static_display():
    from gadget_display import LeelooDisplay
    from datetime import datetime
    import numpy as np

    display = LeelooDisplay(preview_mode=False)
    weather_data = {'temp_f': 72, 'uv_raw': 5, 'rain_24h_inches': 0}
    now = datetime.now()
    time_data = {
        'time_str': now.strftime('%-I:%M %p'),
        'date_str': now.strftime('%b %-d'),
        'seconds': now.second
    }
    contacts = []
    album_data = {
        'artist': 'LEELOO',
        'track': 'Setup Complete',
    }

    img = display.render(weather_data, time_data, contacts, album_data)

    def rgb_to_rgb565(img):
        arr = np.array(img)
        r = (arr[:, :, 0] >> 3).astype(np.uint16)
        g = (arr[:, :, 1] >> 2).astype(np.uint16)
        b = (arr[:, :, 2] >> 3).astype(np.uint16)
        return (r << 11) | (g << 5) | b

    rgb565 = rgb_to_rgb565(img)
    with open('/dev/fb1', 'wb') as fb:
        fb.write(rgb565.tobytes())

    while True:
        time.sleep(60)
        now = datetime.now()
        time_data['time_str'] = now.strftime('%-I:%M %p')
        time_data['date_str'] = now.strftime('%b %-d')
        time_data['seconds'] = now.second
        img = display.render(weather_data, time_data, contacts, album_data)
        rgb565 = rgb_to_rgb565(img)
        with open('/dev/fb1', 'wb') as fb:
            fb.write(rgb565.tobytes())


def main():
    print("=" * 50)
    print("LEELOO Boot Sequence Starting")
    print("=" * 50)

    # Step 1: Splash
    show_splash("Initializing...", 10)
    time.sleep(0.5)

    # Step 2: Check first run
    first_run = is_first_run()
    print(f"First run: {first_run}")

    if first_run:
        # ALWAYS run captive portal on first run — user needs to set up
        # name, crew, etc. even if WiFi happens to be connected.
        print("First run - disconnecting WiFi for captive portal setup")
        show_splash("Starting setup...", 20)
        time.sleep(0.5)

        # Disconnect any existing WiFi so AP mode can take over wlan0
        disconnect_wifi_for_setup()

        run_first_run_screen()

        import threading
        portal_thread = threading.Thread(target=start_captive_portal, daemon=True)
        portal_thread.start()
        print("Captive portal started in background")

        # Wait for WiFi (up to 10 minutes)
        print("Waiting for WiFi setup via captive portal...")
        MAX_WIFI_RETRIES = 120
        wifi_retry_count = 0
        while wifi_retry_count < MAX_WIFI_RETRIES:
            wifi_connected = check_wifi_connected()
            wifi_ssid = get_wifi_ssid()
            if wifi_connected and wifi_ssid and not wifi_ssid.startswith('LEE-'):
                print(f"WiFi connected: {wifi_ssid}")
                mark_first_run_complete()
                break
            wifi_retry_count += 1
            if wifi_retry_count % 12 == 0:
                print(f"Still waiting for WiFi... ({wifi_retry_count * 5}s)")
            time.sleep(5)
        else:
            print("WiFi connection timeout - continuing anyway")
            mark_first_run_complete()

    # Step 3: Verify WiFi
    show_splash("Checking WiFi...", 30)
    wifi_connected = check_wifi_connected()
    wifi_ssid = get_wifi_ssid()

    if wifi_connected and wifi_ssid:
        print(f"WiFi connected: {wifi_ssid}")
        show_splash(f"Connected to {wifi_ssid}", 50)
        time.sleep(1)

        # Step 3a: Deferred geocoding (if needed)
        show_splash("Setting up location...", 55)
        do_deferred_geocoding()

        # Step 3b: Crew registration prep
        show_splash("Checking crew...", 60)
        do_crew_registration()

    else:
        print("WiFi not connected, showing setup screen...")
        run_first_run_screen()
        MAX_WIFI_RETRIES = 60
        wifi_retry_count = 0
        while wifi_retry_count < MAX_WIFI_RETRIES:
            if check_wifi_connected():
                do_deferred_geocoding()
                do_crew_registration()
                break
            wifi_retry_count += 1
            time.sleep(5)
        else:
            print("WiFi reconnect timeout - continuing to UI anyway")

    # Step 4: Crew status
    show_splash("Checking crew...", 70)
    time.sleep(0.5)

    if os.path.exists(CREW_CONFIG_PATH):
        crew = load_json(CREW_CONFIG_PATH)
        crew_code = crew.get('invite_code', crew.get('crew_code', ''))
        print(f"Crew configured: {crew_code}")
        show_splash(f"Crew: {crew_code}", 80)
        time.sleep(0.5)
    else:
        print("No crew configured")
        show_splash("No crew (setup later)", 80)
        time.sleep(1)

    # Step 5: Launch main UI
    show_splash("Starting LEELOO...", 95)
    time.sleep(0.5)

    print("Launching main UI...")
    run_main_ui()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nBoot interrupted")
        clear_screen()
    except Exception as e:
        print(f"Boot error: {e}")
        import traceback
        traceback.print_exc()
        show_splash(f"Error: {str(e)[:40]}", 0)
        time.sleep(10)
