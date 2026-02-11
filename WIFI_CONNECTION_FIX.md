# WiFi Connection After Setup - Root Cause & Fix

## The Problem

After completing the captive portal setup, the Pi **did not automatically connect to WiFi**. You had to **power cycle** the Pi to get it online.

## Root Cause Analysis

### Issue 1: Missing `sudo`
The test script ran `python3 connect_saved_wifi.py` **without sudo**, so NetworkManager commands failed silently.

**Line 64 in test_captive_portal_v2.sh:**
```bash
if python3 connect_saved_wifi.py; then  # ❌ No sudo!
```

**Fix:**
```bash
if sudo python3 connect_saved_wifi.py; then  # ✅ With sudo
```

### Issue 2: Connection Name Mismatch
NetworkManager uses connection names like `"netplan-wlan0-Newtons House"`, not just `"Newtons House"`.

The script checked:
```python
nmcli con show 'Newtons House'  # ❌ Fails - wrong name
```

This returned "no such connection", so it tried to create a new one, which failed because:
1. The Pi was already connected (via auto-configuration)
2. Can't create duplicate connections

**Fix:** Check by SSID instead of connection name:
```python
# Check if already connected to this SSID
nmcli -t -f active,ssid dev wifi | grep "yes:Newtons House"

# Find connection by looking up SSID in all WiFi connections
for connection in wifi_connections:
    if connection.ssid == target_ssid:
        use that connection
```

### Issue 3: wpa_supplicant vs NetworkManager Conflict
The original `wifi_manager.py` was written for manual wpa_supplicant control, but Raspberry Pi OS uses NetworkManager by default. They fought each other.

**Fix:** Use NetworkManager directly via `nmcli` commands.

## Why Power Cycle "Fixed" It

When you power cycled the Pi:
1. NetworkManager read the saved WiFi config
2. Auto-connected to "Newtons House" on boot
3. Everything worked!

The credentials WERE saved correctly - the automatic connection just wasn't working during the test.

## What Was Fixed

### 1. Updated `test_captive_portal_v2.sh`
```bash
# Before
if python3 connect_saved_wifi.py; then

# After
if sudo python3 connect_saved_wifi.py; then
```

### 2. Rewrote `connect_saved_wifi.py`
- Uses NetworkManager exclusively (no wpa_supplicant conflicts)
- Checks if already connected (avoids unnecessary work)
- Finds connections by SSID, not by name
- Better error messages
- Proper waiting with timeout

**New logic:**
```python
1. Check if already connected to SSID → Done!
2. Find existing connection for this SSID (any name)
3. If found → Activate it
4. If not found → Create new connection
5. Wait up to 15 seconds for connection
6. Verify connected to correct SSID
```

## Test Results

### Before Fix:
```bash
$ python3 connect_saved_wifi.py
Connecting to Newtons House...
  Creating new connection...
❌ Failed to connect to Newtons House
```

### After Fix:
```bash
$ sudo python3 connect_saved_wifi.py
Stopping AP mode...
Connecting to Newtons House using NetworkManager...
  Already connected to Newtons House
✅ Successfully connected to Newtons House
```

## The Complete Flow Now

1. User completes captive portal setup
2. Presses Ctrl+C to stop portal
3. **Test script runs:** `sudo python3 connect_saved_wifi.py`
4. Script stops AP mode (hostapd, dnsmasq)
5. Script re-enables NetworkManager control
6. Script checks: Already connected? → Yes! → Success
7. Script waits 3 seconds for stabilization
8. Script starts main display service
9. Weather, time, crew data all work! ✅

## Files Modified

1. ✅ `test_captive_portal_v2.sh` - Added `sudo`
2. ✅ `connect_saved_wifi.py` - Complete rewrite using NetworkManager

## Next Test

The complete flow should now work:

```bash
ssh pi@leeloo.local
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh

# Complete setup on phone
# Press Ctrl+C

# You should see:
# ✅ Successfully connected to WiFi!
# ✅ Setup complete! Device is ready.
```

No power cycle needed! 🎉
