#!/usr/bin/env python3
"""
WiFi Manager - Handle AP mode and client mode switching
"""

import subprocess
import time
import os

# AP Mode Configuration
AP_SSID = "Gadget-Setup"
AP_IP = "192.168.4.1"
AP_NETMASK = "255.255.255.0"
AP_DHCP_START = "192.168.4.2"
AP_DHCP_END = "192.168.4.254"

# Config paths
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DNSMASQ_CONF = "/etc/dnsmasq.d/gadget-ap.conf"
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"


def run_command(cmd, check=False):
    """Run a shell command and return result"""
    try:
        result = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            capture_output=True,
            text=True,
            timeout=30
        )
        if check and result.returncode != 0:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"stderr: {result.stderr}")
        return result
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {cmd}")
        return None
    except Exception as e:
        print(f"Command error: {e}")
        return None


def get_device_id():
    """Get unique device ID for AP SSID (last 4 chars of MAC)"""
    try:
        result = run_command(['cat', '/sys/class/net/wlan0/address'])
        if result and result.returncode == 0:
            mac = result.stdout.strip().replace(':', '')
            return mac[-4:].upper()
    except:
        pass
    return "A7X2"  # Fallback


def write_hostapd_config():
    """Write hostapd configuration for AP mode"""
    device_id = get_device_id()
    ssid = f"Gadget-{device_id}"

    config = f"""# Gadget AP Mode Configuration
interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
# No password - open network for easy setup
"""

    try:
        with open('/tmp/hostapd.conf', 'w') as f:
            f.write(config)
        run_command(['sudo', 'cp', '/tmp/hostapd.conf', HOSTAPD_CONF])
        return ssid
    except Exception as e:
        print(f"Error writing hostapd config: {e}")
        return ssid


def write_dnsmasq_config():
    """Write dnsmasq configuration for DHCP and DNS redirect"""
    config = f"""# Gadget AP Mode - DHCP and captive portal redirect
interface=wlan0
dhcp-range={AP_DHCP_START},{AP_DHCP_END},{AP_NETMASK},24h
address=/#/{AP_IP}
"""

    try:
        with open('/tmp/dnsmasq-gadget.conf', 'w') as f:
            f.write(config)
        run_command(['sudo', 'cp', '/tmp/dnsmasq-gadget.conf', DNSMASQ_CONF])
        return True
    except Exception as e:
        print(f"Error writing dnsmasq config: {e}")
        return False


def start_ap_mode():
    """
    Start WiFi Access Point mode for setup
    Returns the SSID being broadcast
    """
    print("Starting AP mode...")

    # Stop any existing WiFi connections
    run_command(['sudo', 'systemctl', 'stop', 'wpa_supplicant'])
    run_command(['sudo', 'killall', 'wpa_supplicant'])
    time.sleep(1)

    # Write config files
    ssid = write_hostapd_config()
    write_dnsmasq_config()

    # Configure wlan0 with static IP
    run_command(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'])
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'down'])
    time.sleep(0.5)
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'])
    run_command(['sudo', 'ip', 'addr', 'add', f'{AP_IP}/24', 'dev', 'wlan0'])

    # Start hostapd
    run_command(['sudo', 'systemctl', 'start', 'hostapd'])
    time.sleep(1)

    # Start dnsmasq
    run_command(['sudo', 'systemctl', 'start', 'dnsmasq'])
    time.sleep(1)

    print(f"AP mode started: {ssid}")
    return ssid


def stop_ap_mode():
    """Stop WiFi Access Point mode"""
    print("Stopping AP mode...")

    run_command(['sudo', 'systemctl', 'stop', 'hostapd'])
    run_command(['sudo', 'systemctl', 'stop', 'dnsmasq'])

    # Reset wlan0
    run_command(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'])
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'down'])
    time.sleep(0.5)

    print("AP mode stopped")


def scan_wifi_networks():
    """
    Scan for available WiFi networks
    Returns list of SSIDs
    """
    networks = []

    # Make sure wlan0 is up
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'])
    time.sleep(1)

    try:
        result = run_command(['sudo', 'iwlist', 'wlan0', 'scan'])
        if result and result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'ESSID:' in line:
                    ssid = line.split('ESSID:')[1].strip().strip('"')
                    if ssid and ssid not in networks and not ssid.startswith('Gadget-'):
                        networks.append(ssid)
    except Exception as e:
        print(f"Error scanning networks: {e}")

    return networks[:15]  # Limit to 15 networks


def connect_to_wifi(ssid, password):
    """
    Connect to a WiFi network
    Returns True if successful, False otherwise
    """
    print(f"Connecting to WiFi: {ssid}")

    # Stop AP mode if running
    stop_ap_mode()
    time.sleep(2)

    # Write wpa_supplicant config
    wpa_config = f'''ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
'''

    try:
        with open('/tmp/wpa_supplicant.conf', 'w') as f:
            f.write(wpa_config)
        run_command(['sudo', 'cp', '/tmp/wpa_supplicant.conf', WPA_SUPPLICANT_CONF])
    except Exception as e:
        print(f"Error writing wpa_supplicant config: {e}")
        return False

    # Bring up wlan0 and start wpa_supplicant
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'])
    run_command(['sudo', 'systemctl', 'restart', 'wpa_supplicant'])
    run_command(['sudo', 'wpa_cli', '-i', 'wlan0', 'reconfigure'])

    # Request DHCP
    run_command(['sudo', 'dhclient', 'wlan0'])

    # Wait for connection (up to 30 seconds)
    for i in range(30):
        time.sleep(1)
        if is_connected(ssid):
            print(f"Connected to {ssid}!")
            return True
        print(f"Waiting for connection... ({i+1}/30)")

    print(f"Failed to connect to {ssid}")
    return False


def is_connected(expected_ssid=None):
    """
    Check if connected to WiFi
    Optionally verify connected to expected SSID
    """
    result = run_command(['iwgetid', '-r'])
    if result and result.returncode == 0:
        current_ssid = result.stdout.strip()
        if current_ssid:
            if expected_ssid:
                return current_ssid == expected_ssid
            return True
    return False


def get_current_ssid():
    """Get the SSID of the currently connected network"""
    result = run_command(['iwgetid', '-r'])
    if result and result.returncode == 0:
        return result.stdout.strip()
    return None


def get_ip_address():
    """Get the current IP address of wlan0"""
    result = run_command(['hostname', '-I'])
    if result and result.returncode == 0:
        ips = result.stdout.strip().split()
        if ips:
            return ips[0]
    return None


if __name__ == "__main__":
    # Test mode
    import sys

    if len(sys.argv) < 2:
        print("Usage: wifi_manager.py [start_ap|stop_ap|scan|connect SSID PASSWORD|status]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start_ap":
        ssid = start_ap_mode()
        print(f"AP started: {ssid}")

    elif cmd == "stop_ap":
        stop_ap_mode()

    elif cmd == "scan":
        networks = scan_wifi_networks()
        print("Available networks:")
        for n in networks:
            print(f"  - {n}")

    elif cmd == "connect" and len(sys.argv) >= 4:
        ssid = sys.argv[2]
        password = sys.argv[3]
        if connect_to_wifi(ssid, password):
            print("Connected!")
        else:
            print("Connection failed")

    elif cmd == "status":
        ssid = get_current_ssid()
        ip = get_ip_address()
        if ssid:
            print(f"Connected to: {ssid}")
            print(f"IP address: {ip}")
        else:
            print("Not connected")

    else:
        print("Unknown command")
