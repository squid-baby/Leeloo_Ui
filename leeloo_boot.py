#!/usr/bin/env python3
"""
LEELOO Boot Sequence
Handles the complete startup flow:
1. Splash screen (already shown by systemd)
2. WiFi check/setup (captive portal if first run, WiFi-only recovery
   portal if the saved WiFi stops working)
3. Post-WiFi: crew registration, deferred geocoding
4. Main UI

Exit codes: fatal errors (and a crashed brain) exit 1 so systemd's
Restart=on-failure restarts the service. Ctrl+C during the boot steps exits 0.
(Once the brain runs it handles Ctrl+C itself and returns, which looks the
same as a crash from here, so that exits 1 too.)
"""

import os
import sys
import time
import json
import subprocess
import asyncio
import textwrap
import threading

# Add boot directory to path
sys.path.insert(0, '/home/pi/leeloo-ui/boot')
sys.path.insert(0, '/home/pi/leeloo-ui')

from boot.leeloo_splash import show_splash, clear_screen, write_to_fb

# Configuration
LEELOO_HOME = '/home/pi/leeloo-ui'
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, 'device_config.json')
CREW_CONFIG_PATH = os.path.join(LEELOO_HOME, 'crew_config.json')
FIRST_RUN_COMPLETE = os.path.join(LEELOO_HOME, '.first_run_complete')
RELAY_URL = "wss://leeloobot.xyz/ws"

# WiFi recovery timing (seconds)
BOOT_WIFI_WAIT_S = 180           # Saved WiFi must come up within this at boot
AUTOCONNECT_GRACE_S = 45         # Let NetworkManager autoconnect before we step in
RECOVERY_PORTAL_WINDOW_S = 600   # Recovery portal waits this long for new WiFi details
INTERNET_WAIT_S = 30             # After WiFi joins, wait this long for internet
WIFI_POLL_S = 5


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
    # First run = setup never completed. setup_complete is only written once
    # the portal confirmed the home WiFi (catches partial clean slates that
    # deleted device_config.json but left .first_run_complete behind)
    config = load_json(DEVICE_CONFIG_PATH)
    if not config.get('setup_complete', False):
        return True
    if not os.path.exists(FIRST_RUN_COMPLETE):
        # Setup finished but we stopped before writing the flag. Running the
        # full portal now would be refused anyway (it rejects config changes
        # on a set-up device), so just record it.
        mark_first_run_complete()
    return False


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
    except Exception:
        return False


def get_wifi_ssid():
    try:
        # nmcli (handles escaped ':' in names), then iwgetid as a fallback
        from connect_saved_wifi import get_wlan0_ssid
        ssid = get_wlan0_ssid()
        if ssid:
            return ssid
        result = subprocess.run(
            ['iwgetid', '-r'], capture_output=True, text=True, timeout=5
        )
        return (result.stdout.strip() or None) if result.returncode == 0 else None
    except Exception:
        return None


def is_wifi_up():
    """True when NetworkManager has wlan0 connected to a network (not our own AP)"""
    try:
        from connect_saved_wifi import get_wlan0_ssid
        ssid = get_wlan0_ssid()
    except Exception as e:
        print(f"[BOOT] WiFi status check error: {e}")
        return False
    return bool(ssid) and not ssid.startswith('LEE-')


def get_ap_ssid():
    """SSID this device broadcasts in setup mode"""
    try:
        from wifi_manager import get_device_id
        return f"LEE-{get_device_id()}"
    except Exception:
        return "LEE-XXXX"


def disconnect_wifi_for_setup():
    """
    Fully release wlan0 for hostapd AP mode.
    NetworkManager (via netplan) aggressively reclaims wlan0,
    so we must stop NM entirely during AP mode.
    """
    try:
        from wifi_manager import release_wlan0_for_ap
        release_wlan0_for_ap()
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


# ============================================
# LCD: WiFi setup / recovery states
# ============================================

_lcd_lock = threading.Lock()   # Portal worker threads and boot both draw


def lcd_status_text(screen, **kwargs):
    """(title, lines) for a WiFi setup/recovery screen, or None if unknown"""
    ap_ssid = kwargs.get('ap_ssid') or 'LEE-XXXX'
    ssid = kwargs.get('ssid') or 'your wifi'
    minutes = kwargs.get('minutes')

    if screen == 'connecting':
        return "connecting to wifi", [f"joining {ssid}...", "this can take a minute."]
    if screen == 'success':
        return "wifi connected", [f"on {ssid}.", "starting LEELOO..."]
    if screen == 'wifi_failed':
        return "wifi failed", [
            f"could not join {ssid}.",
            f"join {ap_ssid} again on your phone and re-enter the password.",
        ]
    if screen == 'recovery':
        lines = [
            f"can't reach {ssid}.",
            f"if the wifi changed, join {ap_ssid} on your phone and enter the new password.",
        ]
        if minutes:
            lines.append(f"otherwise I'll retry {ssid} in {minutes} min.")
        return "wifi not connecting", lines
    if screen == 'retrying':
        return "retrying wifi", [
            f"trying {ssid} again...",
            f"if it still fails, setup wifi {ap_ssid} comes back.",
        ]
    return None


def render_status_screen(title, lines):
    """Simple 480x320 text screen in the LEELOO palette"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGB', (480, 320), '#1A1D2E')
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 20)
        font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    draw.rectangle([6, 6, 473, 313], outline='#A7AFD4', width=2)
    draw.text((22, 24), title, font=font_title, fill='#D6697F')
    y = 70
    for line in lines:
        for chunk in textwrap.wrap(str(line), width=44) or ['']:
            if y > 290:
                break
            draw.text((22, y), chunk, font=font_body, fill='#FFFFFF')
            y += 24
        y += 8
    return img


def portal_lcd_update(screen, **kwargs):
    """LCD callback for captive_portal.update_lcd (also used by recovery)"""
    with _lcd_lock:
        try:
            if screen == 'ap_mode':
                from leeloo_first_run import show_first_run
                show_first_run(kwargs.get('ap_ssid'))
                return
            text = lcd_status_text(screen, **kwargs)
            if text:
                write_to_fb(render_status_screen(*text))
        except Exception as e:
            print(f"[BOOT] LCD update error ({screen}): {e}")


def run_setup_portal(recovery=False, idle_timeout_s=None):
    """
    Run the captive portal in this thread. Blocks until the home WiFi is
    confirmed (True); in recovery mode returns False if no new WiFi details
    arrived within idle_timeout_s. Errors propagate to the caller.
    """
    from captive_portal import run_captive_portal
    return run_captive_portal(
        lcd_update_callback=portal_lcd_update,
        recovery=recovery,
        idle_timeout_s=idle_timeout_s,
    )


# ============================================
# WIFI RECOVERY (decision logic takes fakes in tests)
# ============================================

def wait_for_wifi(is_connected, timeout_s, sleep=time.sleep, clock=time.monotonic,
                  poll_s=WIFI_POLL_S):
    """Poll is_connected() until True (returns True) or timeout_s passes (False)"""
    deadline = clock() + timeout_s
    while True:
        if is_connected():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(poll_s, remaining))


def boot_wifi_check(is_connected, connect_saved, sleep=time.sleep, clock=time.monotonic,
                    timeout_s=BOOT_WIFI_WAIT_S, grace_s=AUTOCONNECT_GRACE_S,
                    poll_s=WIFI_POLL_S):
    """
    True if WiFi is up within timeout_s. First give NetworkManager's own
    autoconnect a chance, then actively connect the saved network, then
    wait out the rest of the time.
    """
    start = clock()
    if wait_for_wifi(is_connected, min(grace_s, timeout_s), sleep, clock, poll_s):
        return True
    print("[BOOT] WiFi not up yet, connecting saved network...")
    try:
        if connect_saved():
            return True
    except Exception as e:
        print(f"[BOOT] Saved WiFi connect error: {e}")
    remaining = timeout_s - (clock() - start)
    return wait_for_wifi(is_connected, max(remaining, 0), sleep, clock, poll_s)


def wifi_recovery_loop(run_portal, retry_saved, on_retry=None, sleep=time.sleep,
                       max_rounds=None):
    """
    Alternate between the WiFi-only setup portal and retrying the saved WiFi
    until one works, so a router outage never strands the device.
    Returns 'portal' (new WiFi details worked) or 'saved' (old WiFi is back).
    max_rounds is for tests; the device loops until WiFi works.
    """
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        print(f"[BOOT] WiFi recovery round {rounds}: setup portal open")
        try:
            if run_portal():
                return 'portal'
        except Exception as e:
            print(f"[BOOT] Recovery portal error: {e}")
            sleep(WIFI_POLL_S)  # Don't spin if the portal can't start
        if on_retry:
            on_retry()
        print("[BOOT] Retrying saved WiFi...")
        try:
            if retry_saved():
                return 'saved'
        except Exception as e:
            print(f"[BOOT] Saved WiFi retry error: {e}")
    return None


def ensure_wifi_or_recover():
    """
    Set-up device: wait ~3 min for the saved WiFi. If it doesn't come up,
    run the WiFi-only recovery portal (user, crew and location are kept),
    retrying the saved WiFi every ~10 min. Returns once WiFi is connected.
    """
    config = load_json(DEVICE_CONFIG_PATH)
    ssid = config.get('wifi_ssid')
    password = config.get('wifi_password')

    def connect_saved():
        from connect_saved_wifi import switch_to_wifi
        return switch_to_wifi(ssid, password)

    show_splash(f"Connecting to {ssid or 'WiFi'}...", 35)
    if boot_wifi_check(is_wifi_up, connect_saved):
        return 'saved'

    print(f"[BOOT] No WiFi after {BOOT_WIFI_WAIT_S}s, starting WiFi recovery")

    def run_portal():
        disconnect_wifi_for_setup()
        return run_setup_portal(recovery=True, idle_timeout_s=RECOVERY_PORTAL_WINDOW_S)

    def on_retry():
        portal_lcd_update('retrying', ssid=ssid, ap_ssid=get_ap_ssid())

    def retry_saved():
        return boot_wifi_check(is_wifi_up, connect_saved, grace_s=0)

    return wifi_recovery_loop(run_portal, retry_saved, on_retry)


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

class BrainStoppedError(RuntimeError):
    """The main UI returned. It should run until power-off, so this is a failure."""


def run_main_ui():
    show_splash("Loading UI...", 90)
    time.sleep(0.5)
    try:
        from leeloo_brain import main as brain_main
    except ModuleNotFoundError as e:
        if e.name != 'leeloo_brain':
            raise  # A dependency of the brain is missing: that's a real failure
        try:
            from gadget_main import main as gadget_main
        except ModuleNotFoundError:
            print("No main UI available, running static display")
            run_static_display()
            return
        gadget_main()
        raise BrainStoppedError("gadget_main() returned")

    brain_main()
    # leeloo_brain.main() prints and swallows crashes, so getting here means
    # the brain stopped. Raise so we exit non-zero and systemd restarts us.
    raise BrainStoppedError("leeloo_brain.main() returned, brain stopped")


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

        # Blocks until the home WiFi is confirmed. A wrong password brings
        # the setup AP back (LCD explains), so this only returns on success.
        print("Waiting for WiFi setup via captive portal...")
        if not run_setup_portal(recovery=False):
            raise RuntimeError("Setup portal stopped before WiFi was confirmed")
        mark_first_run_complete()
    else:
        # Step 2b: Saved WiFi, with WiFi-only recovery portal if it's gone
        show_splash("Checking WiFi...", 30)
        ensure_wifi_or_recover()

    # Step 3: WiFi is connected (both paths above only return once it is)
    wifi_ssid = get_wifi_ssid() or 'WiFi'
    print(f"WiFi connected: {wifi_ssid}")
    show_splash(f"Connected to {wifi_ssid}", 50)
    if not wait_for_wifi(check_wifi_connected, INTERNET_WAIT_S):
        print("[BOOT] WiFi joined but no internet yet - continuing")

    # Step 3a: Deferred geocoding (if needed)
    show_splash("Setting up location...", 55)
    do_deferred_geocoding()

    # Step 3b: Crew registration prep
    show_splash("Checking crew...", 60)
    do_crew_registration()

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


def boot_entry(main_fn=None):
    """Run the boot sequence and return the process exit code"""
    try:
        (main_fn or main)()
    except KeyboardInterrupt:
        # Deliberate stop (Ctrl+C when run by hand): clean exit
        print("\nBoot interrupted")
        try:
            clear_screen()
        except Exception:
            pass
        return 0
    except Exception as e:
        print(f"Boot error: {e}")
        import traceback
        traceback.print_exc()
        try:
            show_splash(f"Error: {str(e)[:40]}", 0)
        except Exception as splash_error:
            print(f"Boot error splash failed: {splash_error}")
        time.sleep(10)
        # Non-zero so systemd (Restart=on-failure) restarts the service
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(boot_entry())
