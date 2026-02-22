#!/bin/bash
# =============================================================================
# LEELOO Full Setup Script
# =============================================================================
# Run ONCE from the cloned repo on a fresh Pi OS Lite (32-bit) install.
# Must run as root from /home/pi/leeloo-ui:
#
#   sudo bash boot/leeloo_setup.sh
#
# Takes ~10 minutes. Ends with a reboot prompt.
# After reboot, copy .env and the device is ready for first-run (captive portal).
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()  { echo -e "${CYAN}[LEELOO]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# =============================================================================
# VALIDATE
# =============================================================================

if [ "$EUID" -ne 0 ]; then
    fail "Run as root: sudo bash boot/leeloo_setup.sh"
fi

LEELOO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
log "Working directory: $LEELOO_DIR"

# Verify we're on a Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
    warn "This doesn't look like a Raspberry Pi. Continuing anyway..."
fi

# Verify 32-bit OS (required for WS2812B DMA on Pi Zero)
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    fail "64-bit OS detected ($ARCH). LEELOO requires 32-bit Pi OS (armv7l). Reflash with 32-bit Pi OS Lite."
fi
ok "Architecture: $ARCH (32-bit confirmed)"

echo ""
echo "=================================================="
echo "  LEELOO Unit Setup — $(date '+%Y-%m-%d %H:%M')"
echo "=================================================="
echo ""

# =============================================================================
# STEP 1: APT PACKAGES
# =============================================================================
log "[1/7] Installing system packages..."

apt-get update -qq

apt-get install -y \
    python3-pip \
    python3-dev \
    python3-smbus \
    git \
    hostapd \
    dnsmasq \
    libopenblas0 \
    libjpeg-dev \
    libfreetype6-dev \
    i2c-tools \
    alsa-utils \
    2>/dev/null

ok "System packages installed"

# =============================================================================
# STEP 2: PYTHON PACKAGES (system-wide, no venv)
# =============================================================================
log "[2/7] Installing Python packages..."

# Install each package individually so one failure doesn't block the rest
while IFS= read -r line; do
    # Skip comments and blank lines
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
    # Strip inline comments
    pkg=$(echo "$line" | sed 's/#.*//' | tr -d ' ')
    [ -z "$pkg" ] && continue
    echo "  Installing: $pkg"
    pip3 install --break-system-packages "$pkg" --quiet 2>&1 | grep -v "already satisfied" || \
        warn "  Could not install $pkg — continuing"
done < "$LEELOO_DIR/requirements.txt"

# Verify critical imports
python3 -c "import PIL; import numpy; import websockets; print('[OK] Core imports verified')" \
    || fail "Core Python package import failed (PIL/numpy/websockets). Run pip3 install manually."

ok "Python packages installed"

# =============================================================================
# STEP 3: WAVESHARE LCD DRIVER
# =============================================================================
log "[3/7] Installing Waveshare 3.5\" LCD driver..."

DTBO_SRC="$LEELOO_DIR/boot/waveshare35a.dtbo"
DTBO_DST="/boot/firmware/overlays/waveshare35a.dtbo"

if [ ! -f "$DTBO_SRC" ]; then
    fail "waveshare35a.dtbo not found at $DTBO_SRC. Make sure you cloned the full repo."
fi

cp "$DTBO_SRC" "$DTBO_DST"
chmod 755 "$DTBO_DST"
ok "waveshare35a.dtbo installed ($(wc -c < "$DTBO_DST") bytes)"

# =============================================================================
# STEP 4: /boot/firmware/config.txt
# =============================================================================
log "[4/7] Configuring /boot/firmware/config.txt..."

CONFIG="/boot/firmware/config.txt"
cp "$CONFIG" "${CONFIG}.backup-$(date +%Y%m%d)"
ok "Backed up config.txt"

patch_config() {
    local key="$1"
    local value="$2"
    local section="$3"  # optional: "[all]" etc.

    if grep -q "^${key}=" "$CONFIG"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$CONFIG"
    elif grep -q "^#${key}=" "$CONFIG"; then
        sed -i "s|^#${key}=.*|${key}=${value}|" "$CONFIG"
    else
        echo "${key}=${value}" >> "$CONFIG"
    fi
}

# Disable KMS (required — KMS takes over fb0 and breaks waveshare on fb1)
sed -i 's|^dtoverlay=vc4-kms-v3d|#dtoverlay=vc4-kms-v3d|' "$CONFIG" || true

# Prevent firmware from injecting video= into cmdline (breaks framebuffer)
patch_config "disable_fw_kms_setup" "1"

# I2C (ADXL345 accelerometer)
patch_config "dtparam=i2c_arm" "on"

# I2S (INMP441 microphone)
patch_config "dtparam=i2s" "on"

# SPI (required for Waveshare LCD)
patch_config "dtparam=spi" "on"

# MUST disable audio — shares PWM0 DMA with WS2812B LEDs
patch_config "dtparam=audio" "off"

# Force HDMI framebuffer (fb0) even with no monitor plugged in
# Without this, waveshare lands on fb0 instead of fb1 on headless boot
patch_config "hdmi_force_hotplug" "1"

# Allow 2 framebuffers (fb0 = HDMI, fb1 = LCD)
patch_config "max_framebuffers" "2"

# Waveshare 3.5" LCD overlay
if ! grep -q "^dtoverlay=waveshare35a" "$CONFIG"; then
    echo "" >> "$CONFIG"
    echo "# Waveshare 3.5inch LCD" >> "$CONFIG"
    echo "dtoverlay=waveshare35a" >> "$CONFIG"
fi

# INMP441 I2S Microphone
if ! grep -q "^dtoverlay=googlevoicehat-soundcard" "$CONFIG"; then
    echo "" >> "$CONFIG"
    echo "# INMP441 I2S Microphone" >> "$CONFIG"
    echo "dtoverlay=googlevoicehat-soundcard" >> "$CONFIG"
fi

ok "config.txt configured"

# =============================================================================
# STEP 5: /boot/firmware/cmdline.txt
# =============================================================================
log "[5/7] Configuring /boot/firmware/cmdline.txt..."

CMDLINE="/boot/firmware/cmdline.txt"
cp "$CMDLINE" "${CMDLINE}.backup-$(date +%Y%m%d)"

# Read current line (must stay single line)
CURRENT=$(cat "$CMDLINE")

# Switch console from tty1 to tty3 (keeps tty1/tty2 clean, less visual noise)
CURRENT=$(echo "$CURRENT" | sed 's/console=tty1/console=tty3/')
if ! echo "$CURRENT" | grep -q "console=tty3"; then
    CURRENT="$CURRENT console=tty3"
fi

# Suppress kernel log spam
CURRENT=$(echo "$CURRENT" | sed 's/loglevel=[0-9]/loglevel=3/')
if ! echo "$CURRENT" | grep -q "loglevel="; then
    CURRENT="$CURRENT loglevel=3"
fi

# Hide Plymouth/boot logos
for param in "quiet" "plymouth.ignore-serial-consoles" "logo.nologo"; do
    if ! echo "$CURRENT" | grep -q "$param"; then
        CURRENT="$CURRENT $param"
    fi
done

# Hide text cursor on boot
if ! echo "$CURRENT" | grep -q "vt.global_cursor_default=0"; then
    CURRENT="$CURRENT vt.global_cursor_default=0"
fi

# CRITICAL: Keep console on fb0 (HDMI), NOT on fb1 (LCD)
# Without this, the terminal renders on the LCD and corrupts the display
CURRENT=$(echo "$CURRENT" | sed 's/fbcon=map:[0-9]*/fbcon=map:0/')
if ! echo "$CURRENT" | grep -q "fbcon=map:0"; then
    CURRENT="$CURRENT fbcon=map:0"
fi

# Clean up extra spaces and write (must be single line, no trailing newline)
echo "$CURRENT" | tr -s ' ' | tr -d '\n' > "$CMDLINE"
echo "" >> "$CMDLINE"  # single trailing newline required

ok "cmdline.txt configured"

# =============================================================================
# STEP 6: SYSTEMD SERVICES
# =============================================================================
log "[6/7] Installing systemd services..."

cp "$LEELOO_DIR/boot/leeloo.service" /etc/systemd/system/
cp "$LEELOO_DIR/boot/leeloo-splash.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable leeloo.service
systemctl enable leeloo-splash.service

# Disable conflicting services
systemctl disable bluetooth.service 2>/dev/null || true

ok "Services installed and enabled"

# =============================================================================
# STEP 7: PERMISSIONS & CLEANUP
# =============================================================================
log "[7/7] Setting permissions..."

chown -R pi:pi "$LEELOO_DIR"
chmod +x "$LEELOO_DIR/leeloo_boot.py"
chmod +x "$LEELOO_DIR/boot/leeloo_splash.py"

# Make sure album_art directory exists
mkdir -p "$LEELOO_DIR/album_art"
chown pi:pi "$LEELOO_DIR/album_art"

ok "Permissions set"

# =============================================================================
# DONE
# =============================================================================
# =============================================================================
# VERIFY: Print key config settings so we can confirm they're correct
# =============================================================================
echo ""
echo "=================================================="
echo "  CONFIG VERIFICATION"
echo "=================================================="
echo ""
echo "--- Key lines from /boot/firmware/config.txt ---"
grep -E "hdmi_force_hotplug|waveshare|vc4-kms|disable_fw_kms|audio=|spi=|i2c_arm|max_framebuffers|googlevoicehat" /boot/firmware/config.txt || true
echo ""
echo "--- /boot/firmware/cmdline.txt ---"
cat /boot/firmware/cmdline.txt
echo ""
echo "--- waveshare35a.dtbo ---"
ls -la /boot/firmware/overlays/waveshare35a.dtbo
md5sum /boot/firmware/overlays/waveshare35a.dtbo
echo ""
echo "  Expected MD5: d46683bf262ffa1b532851590a96907c"
echo ""

echo "=================================================="
echo -e "  ${GREEN}LEELOO Setup Complete!${NC}"
echo "=================================================="
echo ""
echo "NEXT STEP: Deploy the .env file with API keys:"
echo ""
echo "  From your Mac (in the Leeloo_UI project dir):"
MY_IP=$(hostname -I | awk '{print $1}')
echo "  sshpass -p gadget scp .env pi@${MY_IP}:/home/pi/leeloo-ui/.env"
echo ""
echo "Then reboot:"
echo "  sudo reboot"
echo ""
echo "After reboot, Leeloo will broadcast a WiFi AP: LEELOO-SETUP"
echo ""
