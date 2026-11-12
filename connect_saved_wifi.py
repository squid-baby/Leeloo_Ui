#!/usr/bin/env python3
"""
Connect to saved WiFi credentials after setup.
Runs (as root) after the captive portal completes.

SECURITY: the SSID and password come from a web form on an open access
point. They are passed to nmcli as separate arguments (argv list).
Never build a shell command string from them.
"""

import os
import json
import sys
import subprocess
import time

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")


def run_cmd(args, timeout=30):
    """Run a command given as an argument LIST (no shell).
    Returns (success, stdout, stderr)."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


def active_ssid():
    """SSID of the active WiFi connection, or None."""
    ok, stdout, _ = run_cmd(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    if ok:
        for line in stdout.splitlines():
            if line.startswith("yes:"):
                # nmcli -t escapes ':' and '\' inside values
                return line[4:].replace("\\:", ":").replace("\\\\", "\\")
    return None


def stop_ap_mode():
    """Stop AP mode services and restore NetworkManager"""
    print("Stopping AP mode...")
    run_cmd(["systemctl", "stop", "hostapd"])
    run_cmd(["systemctl", "stop", "dnsmasq"])
    run_cmd(["killall", "hostapd"])
    run_cmd(["ip", "addr", "flush", "dev", "wlan0"])
    run_cmd(["ip", "link", "set", "wlan0", "down"])
    time.sleep(0.5)
    print("Restarting NetworkManager...")
    run_cmd(["systemctl", "start", "NetworkManager"])
    time.sleep(3)


def connect_with_networkmanager(ssid, password, wait_s=20):
    """Connect to WiFi using NetworkManager. Returns True when connected."""
    print(f"Connecting to {ssid} using NetworkManager...")
    run_cmd(["nmcli", "dev", "set", "wlan0", "managed", "yes"])
    time.sleep(1)

    if active_ssid() == ssid:
        print(f"  Already connected to {ssid}")
        return True

    # Creates (or updates) the connection profile with the given password.
    # A wrong password from an earlier attempt is replaced.
    ok, _, stderr = run_cmd(
        ["nmcli", "dev", "wifi", "connect", ssid, "password", password, "ifname", "wlan0"],
        timeout=60,
    )
    if not ok:
        print(f"  nmcli connect failed: {stderr.strip()}")

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if active_ssid() == ssid:
            print(f"  Connected to {ssid}")
            return True
        time.sleep(1)
    return False


def save_config_values(values):
    """Merge values into device_config.json (atomic write)."""
    try:
        with open(DEVICE_CONFIG_PATH, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    config.update(values)
    tmp = DEVICE_CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, DEVICE_CONFIG_PATH)


def main():
    """Connect to the WiFi network saved by the portal.

    On failure the setup is marked NOT complete and `wifi_error` is set.
    leeloo_boot.py sees that and starts the setup portal again, so a wrong
    password never leaves the device without a way back.
    """
    try:
        with open(DEVICE_CONFIG_PATH, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No device config file found")
        return 1

    if not config.get("setup_complete"):
        print("Setup not complete yet")
        return 1

    wifi_ssid = config.get("wifi_ssid")
    wifi_password = config.get("wifi_password")
    if not wifi_ssid or not wifi_password:
        print("No WiFi credentials saved")
        save_config_values({"setup_complete": False, "wifi_error": "no wifi name or password"})
        return 1

    stop_ap_mode()

    if connect_with_networkmanager(wifi_ssid, wifi_password):
        print(f"\nSuccessfully connected to {wifi_ssid}")
        save_config_values({"wifi_error": None})
        # Location lookup (ZIP -> lat/lon/timezone) is done by leeloo_boot.py
        return 0

    print(f"\nFailed to connect to {wifi_ssid}")
    save_config_values({
        "setup_complete": False,
        "wifi_error": f"could not connect to {wifi_ssid}. check the password.",
    })
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
