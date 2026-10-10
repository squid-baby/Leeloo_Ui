#!/usr/bin/env python3
"""
Connect to saved WiFi credentials after setup
Used by the captive portal (in-process) and by the boot WiFi recovery loop.
Uses NetworkManager for reliable WiFi connection.

SECURITY: the SSID and password come from the open captive portal and this
runs as root. Every command is an argv list (never shell=True), so the values
reach nmcli as single literal arguments and can't inject shell commands.

The service already runs as root, so nmcli is called directly (no sudo): a
subprocess timeout then kills nmcli itself instead of orphaning it under a
killed sudo. Activations also get nmcli's own --wait, a few seconds shorter
than the subprocess timeout, so nmcli normally gives up cleanly first.
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
FAILED_CONNECT_WAIT_S = 5    # Shorter wait once nmcli already gave a definite error
SCAN_ATTEMPTS = 3            # Tries when NM hasn't seen the network in a scan yet
RESCAN_WAIT_S = 5
NM_WIFI_TYPES = ('802-11-wireless', 'wifi')  # nmcli -t prints the long name
NMCLI_WAIT_MARGIN_S = 5      # Subprocess timeout is this much longer than nmcli --wait
MIN_NMCLI_WAIT_S = 5


def is_nmcli_activation(argv):
    """nmcli commands that wait for NetworkManager to activate a connection"""
    argv = list(argv)
    return (argv[:1] == ['nmcli']
            and (argv[1:4] == ['device', 'wifi', 'connect'] or argv[1:3] == ['connection', 'up']))


def with_nmcli_wait(argv, timeout):
    """
    (argv, subprocess timeout) for running argv within ~timeout seconds.
    An nmcli activation gets nmcli's own `--wait N`, with the subprocess
    timeout NMCLI_WAIT_MARGIN_S longer than N, so nmcli gives up by itself
    instead of being killed halfway.
    """
    argv = list(argv)
    if not is_nmcli_activation(argv):
        return argv, timeout
    wait_s = max(MIN_NMCLI_WAIT_S, int(timeout) - NMCLI_WAIT_MARGIN_S)
    return ['nmcli', '--wait', str(wait_s)] + argv[1:], wait_s + NMCLI_WAIT_MARGIN_S


def run_cmd(argv, timeout=30):
    """Run an argv command (no shell) and return (success, stdout, stderr)"""
    if not isinstance(argv, (list, tuple)):
        raise TypeError("run_cmd needs an argv list, never a shell string")
    argv, timeout = with_nmcli_wait(argv, timeout)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
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


def parse_active_wlan_profile(active_output):
    """Return the profile active on wlan0 from `nmcli -t -f NAME,DEVICE connection show --active`"""
    for line in active_output.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_terse(line)
        if len(fields) >= 2 and fields[-1].strip() == WIFI_IFACE:
            return ':'.join(fields[:-1])
    return None


def parse_profile_psk(psk_output):
    """Return the password from `nmcli -s -g 802-11-wireless-security.psk connection show id NAME`"""
    value = psk_output.rstrip('\n')
    if not value:
        return None
    # -g output is terse: a ':' or '\\' in the password is backslash-escaped
    return ':'.join(split_nmcli_terse(value))


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

def build_connect_argv(ssid, password, hidden=False):
    """nmcli command that creates + activates a new WiFi profile"""
    argv = ['nmcli', 'device', 'wifi', 'connect', ssid,
            'password', password, 'ifname', WIFI_IFACE]
    if hidden:
        # Hidden networks never show up in a scan: probe for the SSID directly
        argv += ['hidden', 'yes']
    return argv


def build_set_psk_argv(profile_name, password):
    """nmcli command that stores a (possibly new) password in an existing profile"""
    return ['nmcli', 'connection', 'modify', 'id', profile_name,
            'wifi-sec.psk', password]


def build_get_psk_argv(profile_name):
    """nmcli command that prints the password stored in a profile (root only)"""
    return ['nmcli', '-s', '-g', '802-11-wireless-security.psk',
            'connection', 'show', 'id', profile_name]


def build_profile_up_argv(profile_name):
    """nmcli command that activates an existing profile on wlan0"""
    return ['nmcli', 'connection', 'up', 'id', profile_name,
            'ifname', WIFI_IFACE]


def build_delete_profile_argv(profile_name):
    """nmcli command that deletes a saved profile"""
    return ['nmcli', 'connection', 'delete', 'id', profile_name]


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


def get_active_wlan_profile(run=run_cmd):
    """Name of the NetworkManager profile currently active on wlan0, or None"""
    ok, stdout, _ = run(['nmcli', '-t', '-f', 'NAME,DEVICE', 'connection', 'show', '--active'])
    if not ok:
        return None
    return parse_active_wlan_profile(stdout)


def get_profile_psk(profile_name, run=run_cmd):
    """Password stored in a profile, or None if it can't be read"""
    ok, stdout, _ = run(build_get_psk_argv(profile_name))
    if not ok:
        return None
    return parse_profile_psk(stdout)


def list_wifi_profiles(run=run_cmd):
    """Names of all saved WiFi profiles, or None if nmcli couldn't list them"""
    ok, stdout, _ = run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'])
    if not ok:
        return None
    return parse_wifi_profile_names(stdout)


def find_profiles_for_ssid(ssid, run=run_cmd):
    """
    Names of saved NetworkManager WiFi profiles whose SSID is exactly `ssid`.
    (Profile names can be "SSID", "netplan-wlan0-SSID", "preconfigured", ...)
    """
    matches = []
    for name in list_wifi_profiles(run) or []:
        ok, details, _ = run(['nmcli', '-t', '-f', '802-11-wireless.ssid',
                              'connection', 'show', 'id', name])
        if ok and parse_profile_ssid(details) == ssid:
            matches.append(name)
    return matches


# ============================================
# Mode switching + connecting
# ============================================

def ap_mode_active(run=run_cmd):
    """True while hostapd runs (as the systemd service or started directly)"""
    ok, _, _ = run(['systemctl', 'is-active', '--quiet', 'hostapd'])
    if ok:
        return True
    ok, _, _ = run(['pgrep', '-x', 'hostapd'])
    return ok


def networkmanager_running(run=run_cmd):
    ok, _, _ = run(['systemctl', 'is-active', '--quiet', 'NetworkManager'])
    return ok


def ensure_wlan0_ready(run=run_cmd):
    """
    Raise wlan0 and hand it to NetworkManager. NM doesn't raise a link it
    already manages, so a wlan0 left down (e.g. by AP teardown) would stay
    "unavailable" and every connect would fail. Both commands are no-ops
    when wlan0 is already up and managed.
    """
    run(['ip', 'link', 'set', WIFI_IFACE, 'up'])
    run(['nmcli', 'device', 'set', WIFI_IFACE, 'managed', 'yes'])


def stop_ap_mode(run=run_cmd, sleep=time.sleep):
    """
    Leave AP mode (only if hostapd is really up) and make sure NetworkManager
    runs and manages a raised wlan0. Safe to call when already in client mode.
    """
    if ap_mode_active(run):
        _wm_stop_ap_mode()  # stops hostapd + dnsmasq, resets wlan0
        run(['killall', 'hostapd'])
        sleep(0.5)
    else:
        print("AP mode not active, leaving wlan0 alone")
    if not networkmanager_running(run):
        # It was stopped for AP mode
        print("Restarting NetworkManager...")
        run(['systemctl', 'start', 'NetworkManager'])
        sleep(3)
    ensure_wlan0_ready(run)


def _network_not_seen(stderr):
    """nmcli errors meaning "that SSID isn't in the scan results (yet)" """
    text = (stderr or '').lower()
    return 'no network with ssid' in text or 'could not be found' in text


# nmcli errors meaning NetworkManager tried and gave up on this connection
# (wrong password, password too short, activation failed)
NM_DEFINITE_FAILURES = ('secrets were required', 'activation failed', 'property is invalid')


def _definite_failure(stderr):
    """
    nmcli already gave up for good (e.g. "Secrets were required" for a wrong
    password). Not when the network just isn't in the scan list, when the
    command timed out, or when NM wasn't ready yet: the connection may still
    come up then, so those keep the full wait.
    """
    text = (stderr or '').lower()
    if _network_not_seen(text) or 'timed out' in text or 'timeout' in text:
        return False
    return any(marker in text for marker in NM_DEFINITE_FAILURES)


def run_with_rescan(argv, run=run_cmd, sleep=time.sleep, attempts=SCAN_ATTEMPTS,
                    rescan_wait_s=RESCAN_WAIT_S, deadline=None, clock=time.monotonic):
    """
    Run an nmcli connect/up command. Right after NetworkManager starts its
    scan list can still be empty, which fails with "No network with SSID"
    even when the password is right, so rescan and try again a few times
    (but not past `deadline`, a clock() value, when one is given).
    """
    result = (False, "", "")
    for attempt in range(1, attempts + 1):
        result = run(argv)
        success, _, stderr = result
        if success or not _network_not_seen(stderr) or attempt == attempts:
            return result
        if deadline is not None and clock() + rescan_wait_s >= deadline:
            print("  Out of time for rescans")
            return result
        print(f"  Network not in scan list yet, rescanning ({attempt}/{attempts})...")
        run(['nmcli', 'device', 'wifi', 'rescan', 'ifname', WIFI_IFACE])
        sleep(rescan_wait_s)
    return result


def _deadline_run(deadline, clock):
    """run_cmd whose per-command timeout never runs (much) past the deadline"""
    def run(argv):
        remaining = deadline - clock()
        return run_cmd(argv, timeout=max(5, min(30, int(remaining))))
    return run


def connect_with_networkmanager(ssid, password, run=None, sleep=time.sleep,
                                wait_s=CONNECT_WAIT_S, timeout_s=None, clock=time.monotonic,
                                failed_wait_s=FAILED_CONNECT_WAIT_S):
    """
    Connect to WiFi using NetworkManager (more reliable).
    Returns True only once nmcli shows wlan0 connected to exactly `ssid`,
    using `password` (an existing connection with some other stored password
    doesn't count, so a mistyped password is never reported as working).

    timeout_s bounds the whole attempt (rescans, nmcli calls, waiting), give
    or take one nmcli call (at most MIN_NMCLI_WAIT_S + NMCLI_WAIT_MARGIN_S).
    When nmcli already reported a definite failure (wrong password), only
    wait failed_wait_s, so the setup AP comes back sooner.

    A failed attempt leaves no new profile behind, and a password nmcli
    refuses to store counts as a failed attempt.
    """
    deadline = clock() + timeout_s if timeout_s is not None else None
    if run is None:
        run = _deadline_run(deadline, clock) if deadline is not None else run_cmd

    print(f"Connecting to {ssid} using NetworkManager...")

    # Ensure wlan0 is up and NM is managing it
    ensure_wlan0_ready(run)
    sleep(1)

    profiles = find_profiles_for_ssid(ssid, run)
    was_connected = is_connected_to(ssid, run)

    # Already connected to this SSID: only a success if it's with this password
    # (NM autoconnect of an old profile can beat us right after it starts)
    if was_connected:
        active = get_active_wlan_profile(run)
        if not password or (active and get_profile_psk(active, run) == password):
            print(f"  Already connected to {ssid}")
            return True
        print(f"  Connected to {ssid}, but not with this password yet, trying it...")
        if active:
            if active in profiles:
                profiles.remove(active)
            profiles.insert(0, active)

    expected_profile = None
    restore_psk = None
    profiles_before = None   # All WiFi profiles before 'device wifi connect' (create path)
    if profiles:
        # Connection exists: store the password the user just gave us (the
        # router password may have changed), then activate it. Don't fall back
        # to 'device wifi connect' here: each failed retry would add another
        # duplicate profile for the same SSID.
        profile = profiles[0]
        expected_profile = profile
        print(f"  Activating existing connection: {profile}")
        if password:
            old_psk = get_profile_psk(profile, run)
            if old_psk != password:
                ok, _, stderr = run(build_set_psk_argv(profile, password))
                if not ok:
                    # e.g. a password NM rejects (too short): 'connection up'
                    # would just use the old stored one and look like success
                    print(f"  ❌ Could not store the new password - {stderr.strip()}")
                    return False
                restore_psk = old_psk
        success, _, stderr = run_with_rescan(build_profile_up_argv(profile), run, sleep,
                                             deadline=deadline, clock=clock)
        if not success:
            print(f"  Warning: Failed to activate - {stderr.strip()}")
            if _definite_failure(stderr):
                wait_s = min(wait_s, failed_wait_s)
    else:
        # Create new connection
        print("  Creating new connection...")
        profiles_before = list_wifi_profiles(run)
        success, _, stderr = run_with_rescan(build_connect_argv(ssid, password), run, sleep,
                                             deadline=deadline, clock=clock)
        if (not success and _network_not_seen(stderr)
                and (deadline is None or clock() < deadline)):
            # Typed name of a network no scan shows: it may be hidden
            print("  Not in any scan, trying it as a hidden network...")
            success, _, stderr = run(build_connect_argv(ssid, password, hidden=True))
        if not success:
            print(f"  Warning: Failed to connect - {stderr.strip()}")
            if _definite_failure(stderr):
                wait_s = min(wait_s, failed_wait_s)

    # Wait for connection to stabilize
    print("  Waiting for connection...")
    for _ in range(wait_s):
        if is_connected_to(ssid, run):
            # With an existing profile, the one we just set must be the one
            # that's up (not a duplicate profile holding another password).
            # If nmcli can't say which profile is up, trust the SSID check.
            active = get_active_wlan_profile(run) if expected_profile else None
            if active is None or active == expected_profile:
                print(f"  ✅ Connected to {ssid}")
                return True
        if deadline is not None and clock() >= deadline:
            break
        sleep(1)

    print(f"  ❌ wlan0 did not connect to {ssid}")
    if not expected_profile:
        _delete_new_profiles(ssid, profiles_before, run)
    if restore_psk:
        # Don't leave the new (apparently wrong) password in a profile that
        # worked before: put the old one back so autoconnect can still use it
        print(f"  Restoring the previous password for {expected_profile}")
        run(build_set_psk_argv(expected_profile, restore_psk))
        if was_connected:
            # We were online before trying the new password: get back online
            run(build_profile_up_argv(expected_profile))
    return False


def _delete_new_profiles(ssid, profiles_before, run):
    """
    Remove profiles for `ssid` that the failed 'device wifi connect' created
    (a killed or failed nmcli can leave one behind with autoconnect on and
    the wrong password). Profiles that existed before are never touched.
    """
    if profiles_before is None:
        print("  Profile list unknown before connecting, not deleting anything")
        return
    for name in find_profiles_for_ssid(ssid, run):
        if name not in profiles_before:
            print(f"  Deleting profile left by the failed attempt: {name}")
            run(build_delete_profile_argv(name))


def switch_to_wifi(ssid, password, timeout_s=None):
    """
    Leave AP mode (if it's up) and join `ssid`. True only if the connection
    is confirmed. timeout_s (optional) bounds the connection attempt after
    AP mode is stopped.
    """
    if not ssid or not password:
        print("No WiFi credentials to connect with")
        return False
    stop_ap_mode()
    return connect_with_networkmanager(ssid, password, timeout_s=timeout_s)


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
