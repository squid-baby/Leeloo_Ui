#!/usr/bin/env python3
"""
WiFi Manager - Handle AP mode and client mode switching
"""

import subprocess
import time
import os

# AP Mode Configuration
AP_SSID = "LEE-Setup"
AP_IP = "192.168.4.1"
AP_NETMASK = "255.255.255.0"
AP_DHCP_START = "192.168.4.2"
AP_DHCP_END = "192.168.4.254"

# Config paths
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DNSMASQ_CONF = "/etc/dnsmasq.d/leeloo-ap.conf"
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
    ssid = f"LEE-{device_id}"

    config = f"""# LEELOO AP Mode Configuration
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
    config = f"""# LEELOO AP Mode - DHCP and captive portal redirect
interface=wlan0
bind-interfaces

# DHCP Server
dhcp-range={AP_DHCP_START},{AP_DHCP_END},{AP_NETMASK},24h
dhcp-option=3,{AP_IP}
dhcp-option=6,{AP_IP}

# DNS Settings - don't use upstream servers (no internet)
no-resolv
no-poll

# Redirect ALL DNS queries to our portal
address=/#/{AP_IP}

# Specific captive portal detection URLs
# iOS/macOS
address=/captive.apple.com/{AP_IP}
address=/apple.com/{AP_IP}

# Android
address=/clients3.google.com/{AP_IP}
address=/connectivitycheck.gstatic.com/{AP_IP}
address=/www.google.com/{AP_IP}

# Windows
address=/msftconnecttest.com/{AP_IP}
address=/www.msftconnecttest.com/{AP_IP}

# Firefox
address=/detectportal.firefox.com/{AP_IP}
"""

    try:
        with open('/tmp/dnsmasq-leeloo.conf', 'w') as f:
            f.write(config)
        run_command(['sudo', 'cp', '/tmp/dnsmasq-leeloo.conf', DNSMASQ_CONF])
        return True
    except Exception as e:
        print(f"Error writing dnsmasq config: {e}")
        return False


def start_ap_mode():
    """
    Start WiFi Access Point mode for setup.
    Expects NetworkManager to have already released wlan0
    (via 'nmcli device set wlan0 managed no' in leeloo_boot.py).
    Returns the SSID being broadcast.
    """
    print("Starting AP mode...")

    # Stop anything that might hold wlan0
    run_command(['sudo', 'systemctl', 'stop', 'hostapd'])
    run_command(['sudo', 'systemctl', 'stop', 'dnsmasq'])
    run_command(['sudo', 'killall', 'wpa_supplicant'])
    time.sleep(0.5)

    # Write config files
    ssid = write_hostapd_config()
    write_dnsmasq_config()

    # Configure wlan0 with static IP for AP
    run_command(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'])
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'down'])
    time.sleep(0.5)
    run_command(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'])
    run_command(['sudo', 'ip', 'addr', 'add', f'{AP_IP}/24', 'dev', 'wlan0'])
    time.sleep(0.5)

    # Start hostapd (creates the AP)
    result = run_command(['sudo', 'systemctl', 'start', 'hostapd'], check=True)
    if result and result.returncode != 0:
        print(f"[WIFI] hostapd failed: {result.stderr}")
        # Try running directly as fallback
        run_command(['sudo', 'hostapd', '-B', HOSTAPD_CONF])
    time.sleep(1)

    # Start dnsmasq (DHCP + DNS redirect for captive portal)
    result = run_command(['sudo', 'systemctl', 'start', 'dnsmasq'], check=True)
    if result and result.returncode != 0:
        print(f"[WIFI] dnsmasq failed: {result.stderr}")
    time.sleep(1)

    # Verify AP is broadcasting
    verify = run_command(['iwconfig', 'wlan0'])
    if verify:
        print(f"[WIFI] wlan0 state: {verify.stdout.strip()[:100]}")

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
    Scan for available WiFi networks.
    Uses iwlist scan (works even when hostapd isn't running yet).
    Called by captive portal API before AP mode fully takes over.
    Returns list of SSIDs.
    """
    networks = []

    # Try iwlist scan first (works when wlan0 is in managed mode)
    try:
        result = run_command(['sudo', 'iwlist', 'wlan0', 'scan'])
        if result and result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'ESSID:' in line:
                    ssid = line.split('ESSID:')[1].strip().strip('"')
                    if ssid and ssid not in networks and not ssid.startswith('LEE-'):
                        networks.append(ssid)
            if networks:
                return networks[:15]
    except Exception as e:
        print(f"iwlist scan error: {e}")

    # Fallback: nmcli scan (if NM is managing wlan0)
    try:
        run_command(['sudo', 'nmcli', 'device', 'wifi', 'rescan'])
        time.sleep(2)
        result = run_command(['nmcli', '-t', '-f', 'SSID', 'device', 'wifi', 'list'])
        if result and result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                ssid = line.strip()
                if ssid and ssid not in networks and not ssid.startswith('LEE-'):
                    networks.append(ssid)
    except Exception as e:
        print(f"nmcli scan error: {e}")

    return networks[:15]


def connect_to_wifi(ssid, password):
    """
    Connect to a WiFi network using NetworkManager (nmcli).
    Returns True if successful, False otherwise.
    """
    print(f"Connecting to WiFi: {ssid}")

    # Stop AP mode if running
    stop_ap_mode()
    time.sleep(1)

    # Restart NetworkManager (was stopped for AP mode)
    run_command(['sudo', 'systemctl', 'start', 'NetworkManager'])
    time.sleep(3)

    # Connect using nmcli (creates and activates connection)
    result = run_command([
        'sudo', 'nmcli', 'device', 'wifi', 'connect', ssid,
        'password', password, 'ifname', 'wlan0'
    ])

    if result and result.returncode == 0:
        print(f"Connected to {ssid}!")
        return True

    # If that failed, try creating a connection profile first
    print(f"[WIFI] Direct connect failed, trying connection profile...")
    run_command(['sudo', 'nmcli', 'connection', 'delete', f'leeloo-{ssid}'])
    result = run_command([
        'sudo', 'nmcli', 'connection', 'add',
        'type', 'wifi',
        'con-name', f'leeloo-{ssid}',
        'ifname', 'wlan0',
        'ssid', ssid,
        'wifi-sec.key-mgmt', 'wpa-psk',
        'wifi-sec.psk', password
    ])
    if result and result.returncode == 0:
        result = run_command([
            'sudo', 'nmcli', 'connection', 'up', f'leeloo-{ssid}'
        ])
        if result and result.returncode == 0:
            print(f"Connected to {ssid}!")
            return True

    # Wait and check
    for i in range(15):
        time.sleep(2)
        if is_connected(ssid):
            print(f"Connected to {ssid}!")
            return True
        print(f"Waiting for connection... ({i+1}/15)")

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
