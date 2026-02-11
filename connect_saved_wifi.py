#!/usr/bin/env python3
"""
Connect to saved WiFi credentials after setup
This runs after the captive portal completes
Uses NetworkManager for reliable WiFi connection
"""

import os
import json
import sys
import subprocess
import time

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
DEVICE_CONFIG_PATH = os.path.join(LEELOO_HOME, "device_config.json")

def run_cmd(cmd):
    """Run command and return success status"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def stop_ap_mode():
    """Stop AP mode services"""
    print("Stopping AP mode...")
    run_cmd("sudo systemctl stop hostapd")
    run_cmd("sudo systemctl stop dnsmasq")
    time.sleep(1)

def connect_with_networkmanager(ssid, password):
    """Connect to WiFi using NetworkManager (more reliable)"""
    print(f"Connecting to {ssid} using NetworkManager...")

    # Re-enable NetworkManager control of wlan0
    run_cmd("sudo nmcli dev set wlan0 managed yes")
    time.sleep(1)

    # Check if already connected to this SSID
    success, stdout, _ = run_cmd("nmcli -t -f active,ssid dev wifi")
    if f"yes:{ssid}" in stdout:
        print(f"  Already connected to {ssid}")
        return True

    # Try to find existing connection for this SSID
    # Connection name might be "SSID" or "netplan-wlan0-SSID" or something else
    success, stdout, _ = run_cmd("nmcli -t -f NAME,TYPE con show")
    connection_name = None

    for line in stdout.strip().split('\n'):
        if ':wifi' in line:
            name = line.split(':')[0]
            # Check if this connection is for our SSID
            success, details, _ = run_cmd(f"nmcli -t -f 802-11-wireless.ssid con show '{name}'")
            if ssid in details:
                connection_name = name
                break

    if connection_name:
        # Connection exists, just activate it
        print(f"  Activating existing connection: {connection_name}")
        success, _, stderr = run_cmd(f"sudo nmcli con up '{connection_name}'")
        if not success:
            print(f"  Warning: Failed to activate - {stderr}")
    else:
        # Create new connection
        print(f"  Creating new connection...")
        success, _, stderr = run_cmd(
            f"sudo nmcli dev wifi connect '{ssid}' password '{password}'"
        )
        if not success:
            print(f"  Warning: Failed to connect - {stderr}")

    # Wait for connection to stabilize
    print("  Waiting for connection...")
    for i in range(15):
        time.sleep(1)
        success, stdout, _ = run_cmd("nmcli -t -f active,ssid dev wifi")
        if f"yes:{ssid}" in stdout:
            print(f"  ✅ Connected to {ssid}")
            return True

    return False

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

    # Stop AP mode first
    stop_ap_mode()

    # Connect using NetworkManager
    if connect_with_networkmanager(wifi_ssid, wifi_password):
        print(f"\n✅ Successfully connected to {wifi_ssid}")

        # Now that we have internet, geocode the ZIP code
        print("\n🌍 Geocoding ZIP code to coordinates for weather...")
        try:
            subprocess.run(
                ['python3', '/home/pi/leeloo-ui/geocode_zip.py'],
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
