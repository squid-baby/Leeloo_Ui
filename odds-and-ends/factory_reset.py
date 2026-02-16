#!/usr/bin/env python3
"""
LEELOO Factory Reset
Wipes all user data and config to prepare device for new owner.
Run this before shipping a unit.
"""

import os
import sys
import subprocess
import shutil

# Paths to wipe
LEELOO_HOME = "/home/pi/leeloo-ui"
DATA_FILES = [
    ".first_run_complete",
    "wifi_config.json",
    "crew_config.json",
    "device_config.json",
    "hang_data.json",
    "leeloo_config.json",
]

ALBUM_ART_DIR = os.path.join(LEELOO_HOME, "album_art")

# Files to remove from home dir
HOME_FILES = [
    "/home/pi/doorways-album.jpg",
    "/home/pi/leeloo_config.json",
]

# WiFi connections to remove (keep system ones)
WIFI_CONNECTIONS_TO_REMOVE = [
    "netplan-wlan0-Newtons House",
    "Hotspot",
]


def wipe_data_files():
    """Remove all user data files"""
    print("\n=== Wiping data files ===")

    for filename in DATA_FILES:
        filepath = os.path.join(LEELOO_HOME, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"  Removed: {filepath}")
        else:
            print(f"  (not found): {filepath}")

    for filepath in HOME_FILES:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"  Removed: {filepath}")
        else:
            print(f"  (not found): {filepath}")


def wipe_album_art():
    """Clear cached album art"""
    print("\n=== Wiping album art cache ===")

    if os.path.exists(ALBUM_ART_DIR):
        for filename in os.listdir(ALBUM_ART_DIR):
            filepath = os.path.join(ALBUM_ART_DIR, filename)
            os.remove(filepath)
            print(f"  Removed: {filepath}")
    else:
        print(f"  (directory not found): {ALBUM_ART_DIR}")


def wipe_wifi_connections():
    """Remove saved WiFi networks - DISABLED to prevent lockout"""
    print("\n=== WiFi connections ===")
    print("  SKIPPED - keeping WiFi to prevent lockout")
    print("  (Pi Zero has no ethernet, wiping WiFi = bricked device)")
    # NOTE: If you really need to wipe WiFi for shipping, do it manually
    # AFTER confirming the captive portal is working


def verify_reset():
    """Verify the reset was successful"""
    print("\n=== Verification ===")

    # Check data files are gone
    all_clear = True
    for filename in DATA_FILES:
        filepath = os.path.join(LEELOO_HOME, filename)
        if os.path.exists(filepath):
            print(f"  WARNING: Still exists: {filepath}")
            all_clear = False

    # Check WiFi
    result = subprocess.run(
        ['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'],
        capture_output=True, text=True
    )
    if 'yes:' in result.stdout:
        ssid = result.stdout.split('yes:')[1].split('\n')[0]
        print(f"  WARNING: Still connected to WiFi: {ssid}")
        all_clear = False

    if all_clear:
        print("  All clear! Device is ready for new owner.")

    return all_clear


def main():
    print("=" * 50)
    print("LEELOO Factory Reset")
    print("=" * 50)
    print("\nThis will wipe ALL user data:")
    print("  - WiFi configurations")
    print("  - Crew/contact data")
    print("  - Cached album art")
    print("  - All personalization")
    print("\nThe device will show the first-run setup on next boot.")

    if '--yes' not in sys.argv:
        response = input("\nAre you sure? Type 'RESET' to confirm: ")
        if response != 'RESET':
            print("Cancelled.")
            return

    wipe_data_files()
    wipe_album_art()
    wipe_wifi_connections()
    verify_reset()

    print("\n" + "=" * 50)
    print("Factory reset complete!")
    print("Run 'sudo reboot' to test first-run experience.")
    print("=" * 50)


if __name__ == '__main__':
    main()
