#!/bin/bash
# Properly start AP mode by telling NetworkManager to back off

echo "Stopping NetworkManager management of wlan0..."
sudo nmcli dev set wlan0 managed no

echo "Waiting for interface to settle..."
sleep 2

echo "Starting AP mode via wifi_manager..."
cd /home/pi/leeloo-ui
sudo python3 wifi_manager.py start_ap

echo ""
echo "AP mode should now be active!"
echo "Check with: sudo iw dev wlan0 info"
