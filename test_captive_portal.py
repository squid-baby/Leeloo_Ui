#!/usr/bin/env python3
"""
Test script for captive portal WiFi setup
Run this on the Pi to test the AP mode and portal
"""

import sys
import time
import subprocess

def run_cmd(cmd):
    """Run command and print output"""
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(f"ERROR: {result.stderr}")
    return result.returncode == 0

def main():
    print("="*60)
    print("LEELOO Captive Portal Test")
    print("="*60)

    # Step 1: Stop main service
    print("\n[1/6] Stopping main gadget service...")
    run_cmd("sudo systemctl stop gadget.service")

    # Step 2: Clear WiFi config to simulate first run
    print("\n[2/6] Clearing first-run marker (simulating fresh device)...")
    run_cmd("rm -f /home/pi/leeloo-ui/.first_run_complete")

    # Step 3: Check if hostapd/dnsmasq are installed
    print("\n[3/6] Checking dependencies...")
    if not run_cmd("which hostapd"):
        print("❌ hostapd not found - install with: sudo apt-get install hostapd")
        sys.exit(1)
    if not run_cmd("which dnsmasq"):
        print("❌ dnsmasq not found - install with: sudo apt-get install dnsmasq")
        sys.exit(1)
    print("✅ Dependencies OK")

    # Step 4: Test wifi_manager directly
    print("\n[4/6] Testing WiFi manager AP mode...")
    print("This will:")
    print("  - Stop wpa_supplicant")
    print("  - Start hostapd (creates LEE-XXXX WiFi network)")
    print("  - Start dnsmasq (DHCP + DNS redirect)")
    print("\nWARNING: This will disconnect from WiFi!")
    response = input("Continue? (y/n): ")
    if response.lower() != 'y':
        print("Aborted")
        sys.exit(0)

    run_cmd("cd /home/pi/leeloo-ui && sudo python3 -c 'from wifi_manager import start_ap_mode; start_ap_mode()'")

    # Step 5: Check AP mode status
    print("\n[5/6] Checking AP mode status...")
    time.sleep(2)
    run_cmd("sudo systemctl status hostapd --no-pager -n 10")
    run_cmd("sudo systemctl status dnsmasq --no-pager -n 10")
    run_cmd("ip addr show wlan0")

    # Step 6: Start captive portal web server
    print("\n[6/6] Starting captive portal web server...")
    print("\nNow connect to the LEE-XXXX WiFi network")
    print("The portal should auto-open at http://192.168.4.1")
    print("\nPress Ctrl+C to stop")

    try:
        run_cmd("cd /home/pi/leeloo-ui && sudo python3 captive_portal.py")
    except KeyboardInterrupt:
        print("\n\nStopping...")

    # Cleanup
    print("\n\nCleanup: Stopping AP mode...")
    run_cmd("cd /home/pi/leeloo-ui && sudo python3 -c 'from wifi_manager import stop_ap_mode; stop_ap_mode()'")
    print("\nRestarting main service...")
    run_cmd("sudo systemctl start gadget.service")
    print("\n✅ Test complete")

if __name__ == "__main__":
    main()
