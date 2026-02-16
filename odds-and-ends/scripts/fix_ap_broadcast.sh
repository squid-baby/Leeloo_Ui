#!/bin/bash
# Fix AP mode broadcast issues on Raspberry Pi

echo "Creating improved hostapd config..."

# Get device ID from MAC
DEVICE_ID=$(cat /sys/class/net/wlan0/address | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]')
SSID="LEE-${DEVICE_ID}"

# Write improved config with country code and better settings
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<EOF
# LEELOO AP Mode - Improved Configuration
interface=wlan0
driver=nl80211

# Network settings
ssid=${SSID}
country_code=US
ieee80211d=1
ieee80211h=0

# Hardware mode
hw_mode=g
channel=6
ieee80211n=1

# Open network (no password for setup)
auth_algs=1
wpa=0

# Broadcast settings
ignore_broadcast_ssid=0
wmm_enabled=1

# Security
macaddr_acl=0
EOF

echo "Config written. SSID will be: ${SSID}"

echo "Stopping NetworkManager control of wlan0..."
sudo nmcli dev set wlan0 managed no 2>/dev/null || true

echo "Stopping any existing hostapd/dnsmasq..."
sudo systemctl stop hostapd dnsmasq 2>/dev/null || true
sudo killall hostapd dnsmasq 2>/dev/null || true

echo "Bringing down wlan0..."
sudo ip link set wlan0 down
sleep 1

echo "Bringing up wlan0..."
sudo ip link set wlan0 up
sudo ip addr flush dev wlan0
sudo ip addr add 192.168.4.1/24 dev wlan0
sleep 1

echo "Starting hostapd..."
sudo hostapd -B /etc/hostapd/hostapd.conf

sleep 2

echo "Starting dnsmasq..."
sudo systemctl start dnsmasq

sleep 1

echo ""
echo "=========================================="
echo "AP Mode Status:"
echo "=========================================="
sudo iw dev wlan0 info
echo ""
echo "Check for SSID: ${SSID}"
echo "IP: 192.168.4.1"
