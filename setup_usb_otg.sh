#!/bin/bash
# LEELOO USB OTG Setup — run this FROM YOUR LAPTOP (not on the Pi)
# Makes the Pi Zero appear as a USB ethernet adapter so you can always
# SSH in even without WiFi.
#
# Usage: bash setup_usb_otg.sh
# Requires: ssh access to pi@leeloo.local (runs while WiFi still works)

PI="pi@leeloo.local"
PASS="gadget"

echo "=== LEELOO USB OTG Setup ==="
echo "Connecting to $PI..."

ssh -o StrictHostKeyChecking=no $PI "bash -s" << 'REMOTE'
set -e

CONFIG=/boot/firmware/config.txt
CMDLINE=/boot/firmware/cmdline.txt

echo "[1/4] Checking current config..."
grep -q "dtoverlay=dwc2" $CONFIG && echo "  dtoverlay=dwc2 already present" || {
    echo "  Adding dtoverlay=dwc2 to $CONFIG..."
    echo "" | sudo tee -a $CONFIG
    echo "# USB OTG gadget ethernet (added by setup_usb_otg.sh)" | sudo tee -a $CONFIG
    echo "dtoverlay=dwc2" | sudo tee -a $CONFIG
}

echo "[2/4] Checking cmdline.txt..."
grep -q "g_ether" $CMDLINE && echo "  g_ether already present" || {
    echo "  Adding modules-load=dwc2,g_ether to $CMDLINE..."
    sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' $CMDLINE
}

echo "[3/4] Installing usb-gadget helper (optional — for hostname over USB)..."
# Give the Pi a stable IP on the USB interface
IFACE_FILE="/etc/network/interfaces.d/usb0"
if [ ! -f "$IFACE_FILE" ]; then
    sudo tee $IFACE_FILE > /dev/null << 'EOF'
auto usb0
iface usb0 inet static
    address 192.168.7.2
    netmask 255.255.255.0
    network 192.168.7.0
    broadcast 192.168.7.255
EOF
    echo "  USB static IP: 192.168.7.2"
fi

echo "[4/4] Installing change_wifi helper..."
sudo tee /usr/local/bin/leeloo-wifi > /dev/null << 'EOF'
#!/bin/bash
# Usage: leeloo-wifi "SSID" "password"
# Or just: leeloo-wifi   (interactive)

if [ -z "$1" ]; then
    read -p "WiFi SSID: " SSID
    read -s -p "Password: " PASS
    echo
else
    SSID="$1"
    PASS="$2"
fi

echo "Connecting to '$SSID'..."
nmcli device wifi connect "$SSID" password "$PASS"
echo "Done. Check status with: nmcli connection show"
EOF
sudo chmod +x /usr/local/bin/leeloo-wifi

echo ""
echo "=== Setup complete. Rebooting Pi in 5 seconds... ==="
echo "After reboot, plug a USB cable into the Pi's DATA port (center port)"
echo "Then SSH with: ssh pi@192.168.7.2 (Mac/Linux)"
echo "           or: ssh pi@raspberrypi.local (if mDNS works on USB)"
sleep 5
sudo reboot
REMOTE

echo ""
echo "Pi is rebooting."
echo ""
echo "NEXT STEPS (after Pi boots ~30s):"
echo "  1. Plug USB-A to micro-USB into the Pi's CENTER (OTG) port"
echo "  2. Your laptop needs a driver:"
echo "     Mac/Linux: works automatically"
echo "     Windows:   install RNDIS driver or use Zadig"
echo "  3. SSH in: ssh pi@192.168.7.2"
echo "  4. Change WiFi: leeloo-wifi 'NewSSIDName' 'NewPassword'"
