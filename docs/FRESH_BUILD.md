# LEELOO Fresh Build Guide

> Complete instructions for building a new LEELOO unit from scratch — from bare hardware to a device ready to ship to an end user.

**Time to build one unit:** ~45 minutes (most of which is waiting for downloads)
**Time to flash from a master image:** ~5 minutes per SD card

---

## Hardware Bill of Materials

| Component | Part | ~Cost | Notes |
|-----------|------|-------|-------|
| **Raspberry Pi Zero 2 W** | RPi Zero 2 W | $15 | Must be Zero 2 W (not Zero W — too slow) |
| **MicroSD Card** | 16GB+ Class 10 / A1 | $8 | Samsung Endurance or SanDisk Industrial preferred |
| **Waveshare 3.5" LCD** | Waveshare 3.5inch RPi LCD (C) | $22 | ILI9486 driver, 480x320, GPIO HAT |
| **INMP441 Mic** | INMP441 I2S MEMS mic | $4 | 6-wire I2S, breakout board |
| **WS2812B LEDs** | WS2812B strip/ring | $3 | 3–5 LEDs, 5V, 330Ω resistor in series |
| **ADXL345 Accel** | ADXL345 breakout | $3 | I2C, 0x53 address, 5-wire |
| **Power** | 5V/2A USB-C supply | $8 | Waveshare hat powers Pi via GPIO 5V |
| **Enclosure** | Custom / 3D print | - | |

**Total hardware: ~$55–65 per unit**

---

## Wiring Reference

See `docs/WIRING_GUIDE.md` for full diagrams. Quick summary:

| Component | Signal | Pi Physical Pin | BCM GPIO |
|-----------|--------|----------------|----------|
| Waveshare LCD | SPI HAT | HAT connector | - |
| ADXL345 | SDA | Pin 3 | GPIO 2 |
| ADXL345 | SCL | Pin 5 | GPIO 3 |
| ADXL345 | INT1 | Pin 7 | GPIO 4 |
| ADXL345 | VCC | Pin 1 | 3.3V |
| ADXL345 | GND | Pin 9 | GND |
| INMP441 | SCK | Pin 12 | GPIO 18 |
| INMP441 | WS | Pin 35 | GPIO 19 |
| INMP441 | SD | Pin 38 | GPIO 20 |
| INMP441 | GND | Pin 20 | GND |
| INMP441 | VDD | Pin 17 | 3.3V |
| WS2812B | DIN | **Pin 32** | **GPIO 12** |
| WS2812B | 5V | Pin 2 | 5V |
| WS2812B | GND | Pin 6 | GND |

> **LED gotcha:** DIN wire goes to **Pin 32** (GPIO 12 / PWM0), NOT Pin 11 (GPIO 17). Easy to mix up.

---

## Step 1: Flash the SD Card

1. Download **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)**
2. Choose OS: **Raspberry Pi OS Lite (32-bit)** — the "Legacy" or current 32-bit Bullseye/Bookworm/Trixie
   - **Must be 32-bit (armv7l)** — WS2812B LED DMA does not work reliably on 64-bit
3. Click the gear icon (⚙️) in Pi Imager to pre-configure:
   - **Hostname:** `leeloo` (or `leeloo2`, `leeloo3`, etc. for multi-unit)
   - **SSH:** Enable — Use password authentication, password `gadget`
   - **Username:** `pi`
   - **WiFi:** (optional) pre-configure your dev network for initial SSH
   - **Locale:** Set your timezone
4. Flash to SD card

---

## Step 2: First SSH Access

Insert SD, power on. Wait 60 seconds.

```bash
# Find the Pi
ssh pi@leeloo.local    # or use IP: ssh pi@<IP_ADDRESS>
# Password: gadget

# If hostname conflicts with Leeloo 1, use IP directly:
# Check your router's DHCP table for the new device
```

---

## Step 3: Clone the Repo

```bash
# On the Pi:
cd /home/pi
git clone https://github.com/squid-baby/Leeloo_Ui.git leeloo-ui
cd leeloo-ui
```

---

## Step 4: Run the Setup Script

This handles everything: packages, config, LCD driver, services.

```bash
# From /home/pi/leeloo-ui:
sudo bash boot/leeloo_setup.sh
```

**What it does:**
1. Installs apt packages: `hostapd`, `dnsmasq`, `libopenblas0`, `libjpeg-dev`, `i2c-tools`, etc.
2. Installs pip packages from `requirements.txt` (system-wide, no venv)
3. Copies `boot/waveshare35a.dtbo` → `/boot/firmware/overlays/` (verified working version — **do not replace from LCD-show repo**)
4. Patches `/boot/firmware/config.txt` with all required settings
5. Patches `/boot/firmware/cmdline.txt` for clean headless boot
6. Installs and enables `leeloo.service` + `leeloo-splash.service`
7. Sets permissions

**Takes ~10 minutes.** Ends with instructions to deploy `.env` before rebooting.

---

## Step 5: Deploy the .env File

The `.env` contains all API keys. **Never commit this to git.**

```bash
# From your Mac (in the Leeloo_UI project directory):
sshpass -p 'gadget' scp .env pi@leeloo.local:/home/pi/leeloo-ui/.env
```

Contents:
```bash
DEEPGRAM_API_KEY=...      # Nova-2 STT (voice recognition)
ANTHROPIC_API_KEY=...     # Claude Haiku (intent routing)
SPOTIFY_CLIENT_ID=...     # Spotify Web API
SPOTIFY_CLIENT_SECRET=... # Spotify Web API
```

> Keys are stored in `/Users/nathanmills/Desktop/Leeloo_UI/.env` on your Mac.

---

## Step 6: Reboot and Verify

```bash
# On the Pi:
sudo reboot
```

**What to look for:**

| Check | Expected |
|-------|----------|
| LCD lights up | Splash screen → LEELOO boot animation |
| WiFi AP broadcasts | SSID: `LEELOO-SETUP` visible on phone |
| Logs clean | `sudo journalctl -u leeloo.service -f` shows no errors |
| LEDs pulse | Blue breathing pattern (ambient idle mode) |
| `/dev/fb1` exists | `ls /dev/fb*` shows `fb0` and `fb1` |

**If LCD shows blank/white:** See Troubleshooting section.

---

## Step 7: First-Run Experience (End User Setup)

Once the device is shipped, the end user:

1. **Powers on** the device
2. Sees a splash screen, then the display prompts: *"Connect to LEELOO-SETUP"*
3. **Connects phone to WiFi:** `LEELOO-SETUP` (no password)
4. **Captive portal auto-opens** (or browse to `http://192.168.4.1`)
5. Fills out the setup form:
   - WiFi network + password (their home WiFi)
   - Name
   - ZIP code (for weather)
   - Telegram opt-in (optional)
   - Crew code (to join an existing crew) or creates a new one
6. Device reboots, connects to their WiFi
7. Welcome screen shows:
   - **Phase 1 (60s):** Telegram QR code to connect `@Leeloo2259_bot`
   - **Phase 2 (60s):** Spotify OAuth QR if not already connected
8. Device is fully operational

> **Spotify auto-connect:** If the user's Spotify was previously linked (via another device on the same crew), tokens are auto-delivered by the relay server when the device joins the crew. Phase 2 is skipped.

---

## Config File Reference

### `/boot/firmware/config.txt` — Required Settings

```ini
# I2C (ADXL345 accelerometer)
dtparam=i2c_arm=on

# I2S (INMP441 microphone)
dtparam=i2s=on

# SPI (Waveshare LCD)
dtparam=spi=on

# MUST be off — audio and WS2812B share PWM0 DMA (causes 0 audio chunks if audio=on)
dtparam=audio=off

# MUST be commented out — KMS takes over fb0 and breaks waveshare on fb1
#dtoverlay=vc4-kms-v3d

# Prevent firmware from injecting video= into cmdline.txt
disable_fw_kms_setup=1

# Force HDMI fb0 even with no monitor — without this, waveshare lands on fb0 headless
hdmi_force_hotplug=1

# Allow fb0 (HDMI) + fb1 (LCD) to coexist
max_framebuffers=2

# Waveshare 3.5" LCD (ILI9486, SPI, fb_ili9486 module via fbtft)
dtoverlay=waveshare35a

# INMP441 I2S microphone
dtoverlay=googlevoicehat-soundcard
```

### `/boot/firmware/cmdline.txt` — Required Settings

This must be a single line. Key parameters:

| Parameter | Value | Why |
|-----------|-------|-----|
| `console` | `tty3` | Keeps tty1/tty2 clean |
| `loglevel` | `3` | Suppress kernel spam |
| `quiet` | - | Hide boot messages |
| `vt.global_cursor_default=0` | - | No blinking cursor on boot |
| `fbcon=map:0` | - | **Critical:** console stays on fb0 (HDMI), NOT fb1 (LCD) |
| `logo.nologo` | - | No Raspberry Pi logo |
| `plymouth.ignore-serial-consoles` | - | No Plymouth on serial |

Example (replace PARTUUID with actual value):
```
console=serial0,115200 console=tty3 loglevel=3 root=PARTUUID=XXXXXXXX-02 rootfstype=ext4 fsck.repair=yes rootwait quiet plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 fbcon=map:0 cfg80211.ieee80211_regdom=AE
```

### `device_config.json` — Per-Device Settings (created at first-run)

```json
{
  "wifi_ssid": "...",
  "wifi_password": "...",
  "user_name": "...",
  "zip_code": "...",
  "telegram_opted_in": true,
  "setup_complete": true,
  "latitude": 35.91,
  "longitude": -79.08,
  "timezone": "America/New_York",
  "num_leds": 3
}
```

> Add `"num_leds": 5` if this unit has 5 LEDs instead of 3.

---

## Critical File: waveshare35a.dtbo

The file at `boot/waveshare35a.dtbo` in this repo is the **only working version**.

- **MD5:** `d46683bf262ffa1b532851590a96907c`
- **Size:** 2379 bytes
- **Do NOT replace** with the version from the `LCD-show` GitHub repo — that version creates SPI devices instead of the `ilitek,ili9486` device that `fbtft/fb_ili9486` binds to

The setup script copies it automatically.

---

## Troubleshooting

### LCD shows blank/white screen after reboot

**Cause:** `fb1` not created, or LCD module not loading.

```bash
# Check if fb1 exists
ls /dev/fb*
# Should show: /dev/fb0  /dev/fb1

# Check if LCD module loaded
lsmod | grep fb_ili9486
# Should show: fb_ili9486

# Check dmesg for SPI errors
dmesg | grep -i "ili\|waveshare\|fbtft\|spi"
```

**Fixes:**
1. Confirm `dtoverlay=waveshare35a` is in config.txt
2. Confirm `dtparam=spi=on` is in config.txt
3. Confirm the dtbo file is from this repo (check MD5 above)
4. Confirm `hdmi_force_hotplug=1` is set — required for headless boot

### LEDs not working

```bash
# Check LED test
sudo python3 -c "import board; import neopixel; p = neopixel.NeoPixel(board.D12, 3); p.fill((0,255,0)); print('LEDs should be green')"
```

**Most common causes:**
1. DIN wire on wrong pin — must be **Pin 32** (GPIO 12), not Pin 11 (GPIO 17)
2. `dtparam=audio=off` missing from config.txt — audio and WS2812B share PWM0 DMA
3. `adafruit-circuitpython-neopixel` not installed — run `sudo pip3 install adafruit-circuitpython-neopixel --break-system-packages`
4. Running without sudo — service must run as `User=root`

### Tap sensor not detecting

```bash
sudo python3 test_tap_live.py
# Should show raw acceleration values
```

**Check:** I2C is enabled (`dtparam=i2c_arm=on`) and wiring at GPIO 2/3/4.

```bash
i2cdetect -y 1
# Should show "53" at address 0x53
```

### Voice not working / 0 audio chunks

**Most common:** LEDs were running during recording (DMA conflict).
Cancel ambient LED animations before voice recording. The brain handles this automatically — but if testing manually, stop LEDs first.

Also check:
```bash
arecord -D hw:0,0 -f S16_LE -r 16000 -c 1 -d 3 test.wav
# Should record 3 seconds without "Interrupted system call"
```

### Service crashes with `PermissionError: /dev/fb1`

The service must run as `User=root`. Check `/etc/systemd/system/leeloo.service`:
```ini
User=root
```

### Python output not showing in journalctl

Add to service file (setup script handles this automatically):
```ini
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -u /home/pi/leeloo-ui/leeloo_boot.py
```

### `No module named 'neopixel'` in logs

Run as root, not as pi user:
```bash
sudo python3 -c "import neopixel; print('OK')"
# If this works but service fails, check service is User=root
```

---

## Scaling to 100 Units — Master Image Approach

For 3+ units, building a master SD card image is the most efficient approach. You build once, flash many.

### Build the Master Image

1. Build one unit using steps 1–6 above. Confirm it works end-to-end.
2. **Remove device-specific files** (these will be regenerated at first-run):
   ```bash
   rm -f /home/pi/leeloo-ui/device_config.json
   rm -f /home/pi/leeloo-ui/crew_config.json
   rm -f /home/pi/leeloo-ui/spotify_tokens.json
   rm -f /home/pi/leeloo-ui/current_music.json
   rm -f /home/pi/leeloo-ui/.first_run_complete
   rm -f /home/pi/leeloo-ui/.welcome_sent
   rm -rf /home/pi/leeloo-ui/album_art/*.jpg
   rm -rf /home/pi/leeloo-ui/album_art/*.png
   sudo poweroff
   ```
3. Remove the SD card from the Pi
4. **Shrink the image** (optional but recommended):
   ```bash
   # On your Mac — install PiShrink via Docker, or use pishrink.sh
   # Or just use the full image
   sudo dd if=/dev/diskX of=leeloo_master_v1.img bs=4m status=progress
   ```
5. Store `leeloo_master_v1.img` — this is your master

### Flash New Units

```bash
# Using Pi Imager: select "Use custom" and pick leeloo_master_v1.img
# Or via command line:
sudo dd if=leeloo_master_v1.img of=/dev/diskX bs=4m status=progress
```

Each unit will:
- Boot directly into AP mode (captive portal)
- Have all drivers, packages, and services pre-installed
- Have the `.env` API keys pre-loaded
- Be assigned a unique device_id when it first connects to the relay

### When to Rebuild the Master Image

Rebuild the master whenever:
- `requirements.txt` changes (new pip packages)
- `boot/leeloo_setup.sh` changes (new apt packages or config)
- API keys rotate (new `.env`)
- Major code changes that need to be baked in

For minor code changes, you can `git pull` on individual units:
```bash
sshpass -p 'gadget' ssh pi@leeloo.local "cd /home/pi/leeloo-ui && git pull && sudo systemctl restart leeloo.service"
```

---

## Ongoing Maintenance

### Deploy Code Updates to a Live Unit

```bash
# Single file
sshpass -p 'gadget' scp leeloo_brain.py pi@leeloo.local:/home/pi/leeloo-ui/
sshpass -p 'gadget' ssh pi@leeloo.local "sudo systemctl restart leeloo.service"

# Full sync (from local repo)
sshpass -p 'gadget' rsync -av --exclude='.git' --exclude='__pycache__' \
    --exclude='album_art' --exclude='.env' --exclude='device_config.json' \
    --exclude='crew_config.json' --exclude='spotify_tokens.json' \
    ./ pi@leeloo.local:/home/pi/leeloo-ui/
sshpass -p 'gadget' ssh pi@leeloo.local "sudo systemctl restart leeloo.service"
```

### View Logs

```bash
# Live
sshpass -p 'gadget' ssh pi@leeloo.local "sudo journalctl -u leeloo.service -f"

# Last 50 lines
sshpass -p 'gadget' ssh pi@leeloo.local "sudo journalctl -u leeloo.service -n 50 --no-pager"
```

### Factory Reset a Unit

```bash
sshpass -p 'gadget' ssh pi@leeloo.local "sudo python3 /home/pi/leeloo-ui/factory_reset.py"
# Or manually:
sshpass -p 'gadget' ssh pi@leeloo.local "
    rm -f /home/pi/leeloo-ui/device_config.json
    rm -f /home/pi/leeloo-ui/crew_config.json
    rm -f /home/pi/leeloo-ui/spotify_tokens.json
    rm -f /home/pi/leeloo-ui/.first_run_complete
    rm -f /home/pi/leeloo-ui/.welcome_sent
    sudo reboot
"
```

---

## What Gets Created at First-Run (Do Not Pre-Populate)

These files are generated during the captive portal setup flow and should **not** exist on a fresh unit:

| File | Created by | Contains |
|------|-----------|----------|
| `device_config.json` | Captive portal | WiFi, name, ZIP, telegram opt-in |
| `crew_config.json` | Captive portal | Crew code, members |
| `.first_run_complete` | `leeloo_boot.py` | Empty flag file |
| `.welcome_sent` | `leeloo_brain.py` | Empty flag file |
| `spotify_tokens.json` | OAuth callback | Access + refresh tokens |
| `current_music.json` | Music polling | Cached Spotify state |

---

## Quick Checklist — "Is it ready to ship?"

- [ ] LCD displays boot splash and main UI
- [ ] WiFi AP broadcasts as `LEELOO-SETUP` on first boot
- [ ] Captive portal opens on phone at `192.168.4.1`
- [ ] After setup, device connects to WiFi and shows weather
- [ ] LEDs pulse in ambient mode
- [ ] Tap device → green flash + voice recording
- [ ] `sudo journalctl -u leeloo.service` shows no errors
- [ ] `/dev/fb0` and `/dev/fb1` both exist
- [ ] `i2cdetect -y 1` shows `53` at 0x53 (ADXL345)
- [ ] No device-specific files present (`device_config.json` etc.)

---

*Last updated: February 2026*
*See also: `docs/LESSONS_LEARNED.md` for hard-won production debugging notes*
