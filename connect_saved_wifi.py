#!/usr/bin/env python3
"""
Connect to saved WiFi credentials after setup
Used by the captive portal (in-process) and by the boot WiFi recovery loop.
Uses NetworkManager for reliable WiFi connection.

SECURITY: the SSID and password come from the open captive portal and this
runs as root. Every command is an argv list (never shell=True), so the values
reach nmcli as single literal arguments and can't inject shell commands.
"""

import os
import json
import sys
import subprocess
import time

from wifi_manager import stop_ap_mode as _wm_stop_ap_mode

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")

WIFI_IFACE = "wlan0"
CONNECT_WAIT_S = 30          # How long to wait for wlan0 to come up on the new network
SCAN_ATTEMPTS = 3            # Tries when NM hasn't seen the network in a scan yet
RESCAN_WAIT_S = 5
NM_WIFI_TYPES = ('802-11-wireless', 'wifi')  # nmcli -t prints the long name


def run_cmd(argv, timeout=30):
    """Run an argv command (no shell) and return (success, stdout, stderr)"""
    if not isinstance(argv, (list, tuple)):
        raise TypeError("run_cmd needs an argv list, never a shell string")
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        # str(TimeoutExpired) repeats argv, which can contain the WiFi password
        return False, "", f"{argv[0]} timed out after {timeout}s"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {getattr(e, 'strerror', None) or 'command failed'}"


# ============================================
# nmcli terse-output parsing
# ============================================

def split_nmcli_terse(line):
    """
    Split one line of `nmcli -t` output into fields.
    In terse mode nmcli separates fields with ':' and escapes a literal
    ':' or '\\' inside a value with a backslash ("My\\:Net" -> "My:Net").
    """
    fields = []
    current = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if ch == ':':
            fields.append(''.join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    fields.append(''.join(current))
    return fields


def parse_active_ssid(wifi_list_output):
    """Return the SSID marked active in `nmcli -t -f ACTIVE,SSID dev wifi list`, or None"""
    for line in wifi_list_output.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_terse(line)
        if len(fields) >= 2 and fields[0].strip().lower() == 'yes':
            ssid = ':'.join(fields[1:])
            if ssid:
                return ssid
    return None


def parse_wifi_profile_names(con_show_output):
    """Return WiFi profile names from `nmcli -t -f NAME,TYPE connection show`"""
    names = []
    for line in con_show_output.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_terse(line)
        if len(fields) >= 2 and fields[-1].strip() in NM_WIFI_TYPES:
            names.append(':'.join(fields[:-1]))
    return names


def parse_profile_ssid(profile_output):
    """Return the value of `nmcli -t -f 802-11-wireless.ssid connection show id NAME`"""
    for line in profile_output.splitlines():
        fields = split_nmcli_terse(line)
        if len(fields) >= 2 and fields[0] == '802-11-wireless.ssid':
            # Re-join in case this nmcli didn't escape a ':' in the SSID
            return ':'.join(fields[1:])
    return None


# ============================================
# argv builders (kept separate so tests can check them)
# ============================================

def build_connect_argv(ssid, password):
    """nmcli command that creates + activates a new WiFi profile"""
    return ['sudo', 'nmcli', 'device', 'wifi', 'connect', ssid,
            'password', password, 'ifname', WIFI_IFACE]


def build_set_psk_argv(profile_name, password):
    """nmcli command that stores a (possibly new) password in an existing profile"""
    return ['sudo', 'nmcli', 'connection', 'modify', 'id', profile_name,
            'wifi-sec.psk', password]


def build_profile_up_argv(profile_name):
    """nmcli command that activates an existing profile on wlan0"""
    return ['sudo', 'nmcli', 'connection', 'up', 'id', profile_name,
            'ifname', WIFI_IFACE]


# ============================================
# NetworkManager queries
# ============================================

def get_wlan0_ssid(run=run_cmd):
    """
    SSID that wlan0 is connected to according to NetworkManager, or None.
    Requires the device state to be 'connected' (not just associating).
    """
    ok, stdout, _ = run(['nmcli', '-t', '-f', 'DEVICE,STATE', 'device'])
    if not ok:
        return None
    wlan_connected = False
    for line in stdout.splitlines():
        fields = split_nmcli_terse(line)
        if len(fields) >= 2 and fields[0] == WIFI_IFACE:
            wlan_connected = fields[1].strip().startswith('connected')
            break
    if not wlan_connected:
        return None

    ok, stdout, _ = run(['nmcli', '-t', '-f', 'ACTIVE,SSID', 'device', 'wifi', 'list',
                         '--rescan', 'no', 'ifname', WIFI_IFACE])
    if not ok:
        return None
    return parse_active_ssid(stdout)


def is_connected_to(ssid, run=run_cmd):
    """True only if wlan0 is connected to exactly this SSID"""
    return bool(ssid) and get_wlan0_ssid(run) == ssid


def find_profiles_for_ssid(ssid, run=run_cmd):
    """
    Names of saved NetworkManager WiFi profiles whose SSID is exactly `ssid`.
    (Profile names can be "SSID", "netplan-wlan0-SSID", "preconfigured", ...)
    """
    ok, stdout, _ = run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'])
    if not ok:
        return []
    matches = []
    for name in parse_wifi_profile_names(stdout):
        ok, details, _ = run(['nmcli', '-t', '-f', '802-11-wireless.ssid',
                              'connection', 'show', 'id', name])
        if ok and parse_profile_ssid(details) == ssid:
            matches.append(name)
    return matches


# ============================================
# Mode switching + connecting
# ============================================

def stop_ap_mode():
    """Stop AP mode services and restore NetworkManager"""
    _wm_stop_ap_mode()  # stops hostapd + dnsmasq, flushes wlan0
    run_cmd(['sudo', 'killall', 'hostapd'])
    time.sleep(0.5)
    # Restart NetworkManager (it was stopped for AP mode)
    print("Restarting NetworkManager...")
    run_cmd(['sudo', 'systemctl', 'start', 'NetworkManager'])
    time.sleep(3)


def _network_not_seen(stderr):
    """nmcli errors meaning "that SSID isn't in the scan results (yet)" """
    text = (stderr or '').lower()
    return 'no network with ssid' in text or 'could not be found' in text


def run_with_rescan(argv, run=run_cmd, sleep=time.sleep, attempts=SCAN_ATTEMPTS,
                    rescan_wait_s=RESCAN_WAIT_S):
    """
    Run an nmcli connect/up command. Right after NetworkManager starts its
    scan list can still be empty, which fails with "No network with SSID"
    even when the password is right, so rescan and try again a few times.
    """
    result = (False, "", "")
    for attempt in range(1, attempts + 1):
        result = run(argv)
        success, _, stderr = result
        if success or not _network_not_seen(stderr) or attempt == attempts:
            return result
        print(f"  Network not in scan list yet, rescanning ({attempt}/{attempts})...")
        run(['sudo', 'nmcli', 'device', 'wifi', 'rescan', 'ifname', WIFI_IFACE])
        sleep(rescan_wait_s)
    return result


def connect_with_networkmanager(ssid, password, run=run_cmd, sleep=time.sleep,
                                wait_s=CONNECT_WAIT_S):
    """
    Connect to WiFi using NetworkManager (more reliable).
    Returns True only once nmcli shows wlan0 connected to exactly `ssid`.
    """
    print(f"Connecting to {ssid} using NetworkManager...")

    # Ensure NM is managing wlan0
    run(['sudo', 'nmcli', 'device', 'set', WIFI_IFACE, 'managed', 'yes'])
    sleep(1)

    # Check if already connected to this SSID
    if is_connected_to(ssid, run):
        print(f"  Already connected to {ssid}")
        return True

    profiles = find_profiles_for_ssid(ssid, run)
    if profiles:
        # Connection exists: store the password the user just gave us (the
        # router password may have changed), then activate it. Don't fall back
        # to 'device wifi connect' here: each failed retry would add another
        # duplicate profile for the same SSID.
        profile = profiles[0]
        print(f"  Activating existing connection: {profile}")
        if password:
            run(build_set_psk_argv(profile, password))
        success, _, stderr = run_with_rescan(build_profile_up_argv(profile), run, sleep)
        if not success:
            print(f"  Warning: Failed to activate - {stderr.strip()}")
    else:
        # Create new connection
        print("  Creating new connection...")
        success, _, stderr = run_with_rescan(build_connect_argv(ssid, password), run, sleep)
        if not success:
            print(f"  Warning: Failed to connect - {stderr.strip()}")

    # Wait for connection to stabilize
    print("  Waiting for connection...")
    for _ in range(wait_s):
        if is_connected_to(ssid, run):
            print(f"  ✅ Connected to {ssid}")
            return True
        sleep(1)

    print(f"  ❌ wlan0 did not connect to {ssid}")
    return False


def switch_to_wifi(ssid, password):
    """Leave AP mode and join `ssid`. True only if the connection is confirmed."""
    if not ssid or not password:
        print("No WiFi credentials to connect with")
        return False
    stop_ap_mode()
    return connect_with_networkmanager(ssid, password)


def main():
    """Check if setup is complete and connect to WiFi"""
    try:
        with open(DEVICE_CONFIG_PATH, 'r') as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No device config file found")
        return 1

    # Check if setup is complete
    if not config.get('setup_complete'):
        print("Setup not complete yet")
        return 1

    # Get WiFi credentials
    wifi_ssid = config.get('wifi_ssid')
    wifi_password = config.get('wifi_password')

    if not wifi_ssid or not wifi_password:
        print("No WiFi credentials saved")
        return 1

    # Stop AP mode, then connect using NetworkManager
    if switch_to_wifi(wifi_ssid, wifi_password):
        print(f"\n✅ Successfully connected to {wifi_ssid}")

        # Now that we have internet, geocode the ZIP code
        print("\n🌍 Geocoding ZIP code to coordinates for weather...")
        try:
            subprocess.run(
                ['python3', os.path.join(LEELOO_HOME, 'geocode_zip.py')],
                check=False  # Don't fail if geocoding fails
            )
        except Exception as e:
            print(f"⚠️  Geocoding failed: {e}")

        return 0
    else:
        print(f"\n❌ Failed to connect to {wifi_ssid}")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
