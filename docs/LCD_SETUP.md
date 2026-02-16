# Waveshare 3.5inch RPi LCD (A) Setup Guide
## For Raspberry Pi Zero 2 W with Raspbian Trixie (Debian 13)

This guide documents the complete setup process for getting the Waveshare 3.5" LCD (Model A) working on a Raspberry Pi Zero 2 W running the newer Raspbian Trixie OS.

---

## Hardware
- **Display**: Waveshare 3.5inch RPi LCD (A) - 480x320 resolution
- **Board**: Raspberry Pi Zero 2 W
- **OS**: Raspbian GNU/Linux 13 (Trixie)
- **Driver IC**: ILI9486
- **Touchscreen**: ADS7846

---

## Important Notes

### Why Standard LCD-show Scripts Don't Work
The official Waveshare `LCD-show` scripts were designed for older Raspberry Pi OS versions. On newer Raspbian Trixie (Debian 13), several changes break the automated installer:

1. **Boot partition moved**: `/boot/config.txt` → `/boot/firmware/config.txt`
2. **Missing libraries**: `libraspberrypi-dev` package removed, breaking `fbcp` compilation
3. **Display architecture changed**: New kernels use different framebuffer handling

This guide provides the manual fix for these issues.

---

## Setup Steps

### 1. Initial SSH Connection
```bash
# From your Mac/PC, connect to the Pi
ssh pi@gadget.local
# Password: gadget (or your configured password)
```

### 2. Clone LCD Driver Repository
```bash
cd ~
git clone https://github.com/waveshareteam/LCD-show.git
cd LCD-show/
```

### 3. Configure Boot Firmware (Critical Fix)

The LCD-show installer modifies the wrong config file. You must manually edit the **actual** config file:

```bash
# Backup the current config
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup

# Disable conflicting KMS driver
sudo sed -i 's/^dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d/' /boot/firmware/config.txt
sudo sed -i 's/^max_framebuffers=2/#max_framebuffers=2/' /boot/firmware/config.txt

# Add LCD configuration to the end of the file
sudo tee -a /boot/firmware/config.txt << 'EOF'

# Waveshare 3.5inch LCD Configuration
dtparam=spi=on
dtoverlay=waveshare35a
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 320 60 6 0 0 0
hdmi_drive=2
EOF
```

### 4. Copy Display Overlay Files
```bash
# Copy the overlay to the boot partition
sudo cp ~/LCD-show/waveshare35a.dtbo /boot/firmware/overlays/
```

### 5. Configure Console Framebuffer Mapping

Edit the kernel command line to map the console to the LCD:

```bash
# Edit cmdline.txt to add framebuffer console mapping
sudo sed -i 's/console=tty1/console=tty1 fbcon=map:01 fbcon=font:VGA8x8/' /boot/firmware/cmdline.txt
```

### 6. Fix rc.local Service

The LCD-show installer creates an `/etc/rc.local` that tries to run `fbcp`, which doesn't compile on newer systems. Fix it:

```bash
sudo tee /etc/rc.local << 'EOF'
#!/bin/sh -e
#
# rc.local
#

# Print the IP address
_IP=$(hostname -I) || true
if [ "$_IP" ]; then
  printf "My IP address is %s\n" "$_IP"
fi

# Map console to fb1 (LCD)
con2fbmap 1 1

exit 0
EOF

# Ensure it's executable
sudo chmod +x /etc/rc.local
```

### 7. Configure X11 for Desktop (Optional)

If you want to run a desktop environment on the LCD:

```bash
# Create X11 configuration to use the LCD framebuffer
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/99-fbdev.conf << 'EOF'
Section "Device"
  Identifier "myfb"
  Driver "fbdev"
  Option "fbdev" "/dev/fb1"
EndSection
EOF
```

### 8. Reboot
```bash
sudo reboot
```

---

## Verification

After reboot, verify the LCD is working:

```bash
# Check framebuffer devices exist
ls -la /dev/fb*
# Should show: fb0 and fb1

# Check LCD driver loaded
dmesg | grep fb1
# Should show: "fb_ili9486 frame buffer, 480x320"

# Verify console mapping
con2fbmap 1
# Should show: "console 1 is mapped to framebuffer 1"

# Check overlays loaded
dmesg | grep -i waveshare
# Should show SPI and touchscreen initialization
```

---

## Display Rotation

To rotate the display, you can modify the device tree overlay. Edit `/boot/firmware/config.txt` and change the waveshare overlay line:

### Standard rotations:
```bash
# 0° (default)
dtoverlay=waveshare35a

# 90° rotation
dtoverlay=waveshare35a,rotate=90

# 180° rotation
dtoverlay=waveshare35a,rotate=180

# 270° rotation
dtoverlay=waveshare35a,rotate=270
```

After changing rotation, reboot:
```bash
sudo reboot
```

---

## Switching Back to HDMI

To disable the LCD and use HDMI output:

```bash
# Comment out the LCD overlay
sudo sed -i 's/^dtoverlay=waveshare35a/#dtoverlay=waveshare35a/' /boot/firmware/config.txt

# Re-enable KMS driver
sudo sed -i 's/^#dtoverlay=vc4-kms-v3d/dtoverlay=vc4-kms-v3d/' /boot/firmware/config.txt

# Reset console mapping
sudo sed -i 's/fbcon=map:01/fbcon=map:10/' /boot/firmware/cmdline.txt

sudo reboot
```

---

## Troubleshooting

### White Screen
- The LCD is powered but not receiving signal
- Verify `/boot/firmware/config.txt` has the LCD configuration (not `/boot/config.txt`)
- Check that `dtoverlay=waveshare35a` line exists
- Ensure `vc4-kms-v3d` overlay is commented out

### Static/Noise on Screen
- Console framebuffer mapping is incorrect
- Verify `fbcon=map:01` is in `/boot/firmware/cmdline.txt`
- Run `con2fbmap 1 1` to manually map console to LCD

### Boot Hangs on rc-local.service
- The `fbcp` command in `/etc/rc.local` is blocking
- Follow Step 6 to fix the rc.local file
- Ensure `fbcp &` line is commented out or removed

### Touchscreen Not Working
- Check that SPI is enabled: `dtparam=spi=on` in config.txt
- Verify touchscreen driver loaded: `dmesg | grep ads7846`
- Should show: "ADS7846 Touchscreen"

---

## Technical Details

### Framebuffer Architecture
- **fb0**: Primary framebuffer (HDMI/virtual console)
- **fb1**: LCD display (480x320, ILI9486 driver)
- Console text mode: 60x40 characters (VGA8x8 font)

### Display Specifications
- **Resolution**: 480x320 pixels
- **Interface**: SPI (spi0.0)
- **Speed**: 16 MHz SPI clock
- **Video Memory**: 300 KiB
- **Refresh Rate**: ~33 fps
- **Touch**: Resistive (ADS7846), GPIO 17 (pen interrupt)

### Boot Configuration Files
- **Firmware config**: `/boot/firmware/config.txt` (main config, read by firmware)
- **Kernel cmdline**: `/boot/firmware/cmdline.txt` (kernel boot parameters)
- **Startup script**: `/etc/rc.local` (runs at end of boot)
- **Overlays**: `/boot/firmware/overlays/` (device tree overlays)

---

## Reference Links
- [Waveshare Wiki](https://www.waveshare.com/wiki/3.5inch_RPi_LCD_(A))
- [LCD-show Repository](https://github.com/waveshareteam/LCD-show)
- [Raspberry Pi Config.txt Documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html)

---

## Summary

The key fixes for newer Raspberry Pi OS (Trixie):

1. ✅ Edit `/boot/firmware/config.txt` (not `/boot/config.txt`)
2. ✅ Disable `vc4-kms-v3d` overlay
3. ✅ Add `waveshare35a` overlay
4. ✅ Configure console framebuffer mapping: `fbcon=map:01`
5. ✅ Fix `/etc/rc.local` to remove broken `fbcp` call
6. ✅ Map console to fb1: `con2fbmap 1 1`

**Result**: LCD displays boot screen, boot messages, and console terminal at 480x320 resolution with working touchscreen.

---

*Document created: 2026-02-07*
*Tested on: Raspberry Pi Zero 2 W, Raspbian Trixie (Debian 13)*
