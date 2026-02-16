#!/bin/bash
# Improved captive portal test with proper detection support

echo "============================================================"
echo "LEELOO Captive Portal Test v2"
echo "============================================================"
echo ""
echo "This will:"
echo "  1. Stop NetworkManager control of wlan0"
echo "  2. Start AP mode (LEE-XXXX WiFi)"
echo "  3. Configure dnsmasq with captive portal detection URLs"
echo "  4. Start Flask captive portal web server"
echo ""
echo "WARNING: This will disconnect the Pi from WiFi!"
read -p "Continue? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted"
    exit 0
fi

cd /home/pi/leeloo-ui

echo ""
echo "[1/5] Stopping main service..."
sudo systemctl stop gadget.service 2>/dev/null || true

echo ""
echo "[2/5] Telling NetworkManager to release wlan0..."
sudo nmcli dev set wlan0 managed no

echo ""
echo "[3/5] Starting AP mode with improved config..."
sudo python3 wifi_manager.py start_ap

echo ""
echo "[4/5] Checking dnsmasq config..."
echo "Config file location: /etc/dnsmasq.d/leeloo-ap.conf"
sudo cat /etc/dnsmasq.d/leeloo-ap.conf

echo ""
echo "[5/5] Starting captive portal web server..."
echo ""
echo "=========================================="
echo "AP MODE IS NOW ACTIVE!"
echo "=========================================="
echo ""
echo "WiFi Network: LEE-XXXX (check output above for exact name)"
echo "Portal URL: http://192.168.4.1"
echo ""
echo "Connect your phone/laptop to the LEE-XXXX WiFi network."
echo "The captive portal should auto-open."
echo ""
echo "Press Ctrl+C when done testing."
echo ""

# Start Flask server (this blocks)
sudo python3 captive_portal.py

echo ""
echo "Captive portal stopped."
echo ""
echo "Checking if WiFi credentials were saved..."
if sudo python3 connect_saved_wifi.py; then
    echo "✅ Successfully connected to WiFi!"
    echo "Waiting for connection to stabilize..."
    sleep 3
    echo "Restarting main service..."
    sudo systemctl start gadget.service
    echo ""
    echo "✅ Setup complete! Device is ready."
else
    echo "⚠️  No WiFi credentials or connection failed."
    echo "Stopping AP mode manually..."
    sudo python3 -c 'from wifi_manager import stop_ap_mode; stop_ap_mode()'
    sudo nmcli dev set wlan0 managed yes
    sudo systemctl start gadget.service
fi

echo ""
echo "Done!"
