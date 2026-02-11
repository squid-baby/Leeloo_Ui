# Important Lessons: LEELOO Captive Portal WiFi Setup

## Project Context
LEELOO Pi Zero - A cyberpunk-styled gadget with LCD display, weather, messaging, and WiFi captive portal setup flow.

**Goal:** Get the captive portal WiFi setup working end-to-end so users can configure their LEELOO without SSH/terminal access.

---

## Critical Issues & Solutions

### 1. **Portal Never Launched (BLOCKER)**

**Problem:** WiFi network `LEE-ABAD` appeared, but captive portal didn't open on phone.

**Root Cause:** Missing system packages - `hostapd` and `dnsmasq` were **NOT INSTALLED**.

**Why we didn't catch this earlier:**
- Assumed base system had these packages
- Code didn't check for package existence before trying to use them
- No error messages indicating missing dependencies

**The Fix:**
```bash
sudo apt-get install -y hostapd dnsmasq
```

**Files Modified:**
- None (system-level fix)

**Lesson Learned:**
- ✅ **ALWAYS verify system dependencies are installed** before blaming code
- ✅ Add dependency checks to setup scripts
- ✅ Document required system packages in README

---

### 2. **Portal Didn't Auto-Open on Phone**

**Problem:** Even with packages installed, phones didn't automatically show the captive portal popup.

**Root Cause:** Missing captive portal detection URLs in `dnsmasq` configuration.

**Why we didn't look here first:**
- Focused on code-level issues before network-level configuration
- Didn't understand that iOS/Android have specific detection URLs they query

**How Captive Portal Detection Works:**
Modern devices detect captive portals by making HTTP requests to known URLs:
- **iOS/macOS:** `captive.apple.com`
- **Android:** `clients3.google.com`, `connectivitycheck.gstatic.com`
- **Windows:** `msftconnecttest.com`
- **Firefox:** `detectportal.firefox.com`

When these requests get redirected (instead of returning expected content), the OS knows it's behind a captive portal and auto-opens it.

**The Fix:**
Updated `wifi_manager.py` dnsmasq config to redirect ALL these detection URLs:

```python
# In wifi_manager.py - dnsmasq config
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
```

**Files Modified:**
- `wifi_manager.py` - Enhanced dnsmasq configuration

**Also Added Flask Routes:**
```python
# In captive_portal.py
@app.route('/hotspot-detect.html')
@app.route('/generate_204')
@app.route('/gen_204')
@app.route('/ncsi.txt')
@app.route('/connecttest.txt')
def captive_portal_detection():
    """Handle captive portal detection from various OS"""
    return redirect('/setup/wifi')
```

**Lesson Learned:**
- ✅ **Captive portals require OS-specific detection URLs** - not just "any redirect"
- ✅ Research platform requirements BEFORE building features
- ✅ Test on actual devices (iOS/Android), not just assumptions

---

### 3. **Portal Closed After WiFi Step 1**

**Problem:** User selected WiFi network, entered password, clicked submit → portal closed immediately, never moved to step 2.

**Root Cause:** `/api/wifi` endpoint was calling `connect_to_wifi()` **immediately**, which:
1. Stopped AP mode
2. Tried to connect to home WiFi
3. Disconnected the phone from the portal
4. Portal closed because connection lost

**Why we didn't look here first:**
- Seemed logical to connect to WiFi as soon as credentials were entered
- Didn't think about the timing issue: phone needs to stay connected to finish setup

**The Fix:**
Changed `/api/wifi` to **SAVE credentials only**, don't connect yet. WiFi connection happens at the **END** of setup flow.

```python
# BEFORE (broken):
@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    # ... save credentials ...
    connect_to_wifi(ssid, password)  # ❌ Kills portal immediately!
    return jsonify({'success': True})

# AFTER (working):
@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    # ... save credentials ...
    # Don't connect yet - wait until setup is complete!
    return jsonify({'success': True})
```

**Files Modified:**
- `captive_portal.py` - Removed immediate WiFi connection from `/api/wifi`

**Lesson Learned:**
- ✅ **Think about the USER'S connection state** during network operations
- ✅ Don't switch networks mid-flow - breaks active connections
- ✅ Save state first, execute network changes last

---

### 4. **Weather Not Showing**

**Problem:** After setup, LEELOO display showed time/date but no weather data.

**Root Cause #1:** Config had `zip_code: "27510"` but `gadget_main.py` expects `latitude` and `longitude`.

**Why we didn't look here first:**
- Assumed ZIP code geocoding was working
- Didn't verify the actual config structure saved by portal

**The Fix (Temporary):**
Manually added lat/lon to config:
```bash
config["location"]["latitude"] = 35.9101
config["location"]["longitude"] = -79.0753
```

**Root Cause #2:** ZIP to lat/lon geocoding in `/api/info` using Nominatim API failed silently.

**Files Modified:**
- `captive_portal.py` - Added lat/lon geocoding (but it failed silently)

**Lesson Learned:**
- ✅ **Log API failures** - don't fail silently
- ✅ Verify external API calls work BEFORE relying on them
- ✅ Add fallback for geocoding failures (show error to user)

---

### 5. **Weather and Messages Not Showing**

**Problem:** Config had data correctly saved, but display showed empty weather and messages.

**Root Cause #1:** **Config file mismatch!**
- `captive_portal.py` (Opus refactor) writes to `/home/pi/leeloo-ui/device_config.json` and `crew_config.json`
- `gadget_main.py` reads from same location
- BUT: ZIP code geocoding failed silently during portal setup

**Root Cause #2:** **No Internet During Portal Setup**
- Portal runs in AP mode (no internet connection)
- Can't call geocoding APIs (Nominatim) without internet
- ZIP code saved but never converted to lat/lon for weather

**Why we didn't look here first:**
- Assumed both systems used the same config file
- Didn't verify the actual file paths in both codebases
- Config was being saved successfully, just to the WRONG place

**The Discovery:**
```python
# captive_portal.py (line 22):
CONFIG_PATH = "/home/pi/leeloo_config.json"  # ❌ Portal writes here

# gadget_main.py (lines 19-20):
DEVICE_CONFIG_PATH = "/home/pi/leeloo-ui/device_config.json"  # ✅ Gadget reads here
CREW_CONFIG_PATH = "/home/pi/leeloo-ui/crew_config.json"      # ✅ Gadget reads here
```

**The Fix (Immediate - Manual):**
Created a sync script to convert portal config → gadget config:
```python
# Read captive portal config
with open("/home/pi/leeloo_config.json", "r") as f:
    portal_config = json.load(f)

# Create device_config.json
device_config = {
    "latitude": portal_config["location"].get("latitude"),
    "longitude": portal_config["location"].get("longitude"),
    "zip_code": portal_config["location"].get("zip_code"),
    "user_name": portal_config.get("user_name")
}
with open("/home/pi/leeloo-ui/device_config.json", "w") as f:
    json.dump(device_config, f, indent=2)

# Create crew_config.json
crew_config = {
    "name": portal_config["crew"]["name"],
    "invite_code": portal_config["crew"]["invite_code"],
    "is_creator": portal_config["crew"]["is_creator"],
    "members": portal_config.get("contacts", [])
}
with open("/home/pi/leeloo-ui/crew_config.json", "w") as f:
    json.dump(crew_config, f, indent=2)
```

**The Fix (Permanent - Automated):**
Added `sync_config_to_gadget()` function to `captive_portal.py` and called it after every config save (4 locations):
1. `/api/wifi` - Line 1030
2. `/api/info` - Line 1097
3. `/api/crew/create` - Line 1189
4. `/api/crew/join` - Line 1243

**Files Modified:**
- `captive_portal.py` - Added sync function, called after all saves

**Lesson Learned:**
- ✅ **VERIFY CONFIG FILE PATHS MATCH** across all systems
- ✅ Use environment variables for shared config paths
- ✅ Grep for config file usage across entire codebase FIRST
- ✅ Single source of truth > multiple config files

**Future Improvement:**
Refactor to use gadget_main format directly (Option B) - eliminates duplication entirely.

---

### 6. **Pi Stayed in AP Mode After Setup**

**Problem:** After completing portal, Pi continued broadcasting `LEE-ABAD` WiFi instead of connecting to home network.

**Root Cause:** WiFi connection script never ran because **SSH pipe broke** when Pi tried to switch networks.

**The Original Test Flow:**
```bash
ssh pi@leeloo.local
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh  # Starts AP mode
# ... user completes portal on phone ...
# Press Ctrl+C  # ❌ This never happens because SSH disconnects!
python3 connect_saved_wifi.py  # Never runs!
```

**Why Ctrl+C Approach Failed:**
1. Test script starts AP mode via SSH
2. Pi switches from home WiFi to AP mode
3. SSH connection **BREAKS** (Pi no longer on same network)
4. User can't press Ctrl+C because terminal lost connection
5. Script keeps running forever in AP mode

**Why we didn't look here first:**
- Assumed Ctrl+C would work like a normal script
- Didn't think about the network switch breaking the SSH pipe
- User said "NO more guessing" after we tried multiple failed approaches

**The Fix:**
Added `/api/finish` endpoint that triggers WiFi connection via **background subprocess** when user reaches final page:

```python
# In captive_portal.py - NEW endpoint
@app.route('/api/finish', methods=['POST'])
def api_finish():
    """Finish setup and connect to WiFi"""
    import subprocess
    config = load_config()
    if not config.get('setup_complete'):
        return jsonify({'success': False, 'error': 'Setup not complete'})

    try:
        # Launch WiFi connection in background (detached from Flask)
        subprocess.Popen(
            ['sudo', 'python3', '/home/pi/leeloo-ui/connect_saved_wifi.py'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # ✅ Critical! Detaches from parent process
        )
        return jsonify({'success': True, 'message': 'Connecting to WiFi...'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
```

Updated `/done` page to **automatically call** `/api/finish`:
```javascript
// In /done page template
fetch('/api/finish', {method: 'POST'})
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            document.getElementById('status').textContent = 'Your LEELOO is ready!';
        } else {
            document.getElementById('status').textContent = 'Setup saved!';
            document.getElementById('instructions').textContent =
                'Power cycle your LEELOO to connect to WiFi.';
        }
    })
```

**How `start_new_session=True` Works:**
- Creates new process group (not child of Flask)
- Script continues running even if Flask/SSH dies
- WiFi connection executes successfully in background

**Files Modified:**
- `captive_portal.py` - Added `/api/finish` endpoint, updated `/done` page

**Lesson Learned:**
- ✅ **Think about process lifecycle** - what happens when parent dies?
- ✅ Network operations that kill connections need background execution
- ✅ `subprocess.Popen` with `start_new_session=True` for detached processes
- ✅ Don't rely on manual user actions (Ctrl+C) for critical steps

---

### 7. **Share Button Did Nothing**

**Problem:** Friend code "SHARE" button on `/setup/crew/created` page did nothing when clicked.

**Root Cause:** JavaScript `shareNative()` function failed silently due to catch block swallowing errors.

**Why we didn't look here first:**
- Assumed native share API would work
- Didn't realize portal closing is a platform limitation, not a bug

**User's Key Insight:**
"It's not possible to leave the portal to send a text. It closes the portal."

**The Fix:**
Removed broken SHARE button, kept only COPY button, updated messaging:

```html
<!-- BEFORE -->
<button onclick="copyCode()">COPY</button>
<button onclick="shareNative()">SHARE</button>
<p>Portal will stay open while you share.</p>  <!-- ❌ Not true! -->

<!-- AFTER -->
<button onclick="copyCode()">COPY TO CLIPBOARD</button>
<p>💡 This code is saved on your device!<br>
   Send to homies when you're done with setup.</p>  <!-- ✅ Honest! -->
```

**Files Modified:**
- `captive_portal.py` - Lines 619-627

**Lesson Learned:**
- ✅ **Test features on actual devices** before assuming they work
- ✅ Captive portals are sandboxed - can't rely on leaving/returning
- ✅ Set user expectations correctly (copy now, share after setup)
- ✅ Remove broken features instead of keeping them "just in case"

---

### 8. **Time Not Updating to Local Timezone**

**Problem:** Display showed correct weather but time was in wrong timezone (not matching user's location).

**Root Cause:** `gadget_main.py` expects a `timezone` field in config (e.g., `"America/Denver"`) but captive portal never set it.

**Why we didn't look here first:**
- Focused on getting weather working first
- Assumed system time was "good enough"
- Didn't realize `gadget_main.py` had timezone support built-in (lines 116-125)

**The Discovery:**
```python
# gadget_main.py (lines 116-125)
tz_name = device_config.get('timezone')  # ❌ This was None!
try:
    local_tz = ZoneInfo(tz_name) if tz_name else None
except (KeyError, Exception):
    local_tz = None

# Later...
now = datetime.now(tz=local_tz) if local_tz else datetime.now()  # Falls back to system time
```

**The Solution Path:**

**Attempt 1: Simple Longitude-Based Logic** ❌
- Used longitude to approximate timezone
- Works ~90% of the time but inaccurate for edge cases
- No external dependencies

**Attempt 2: timezonefinder Package** ❌
- Tried to install `pip3 install timezonefinder`
- Failed due to missing system dependencies (libffi)
- Would have required compiling C extensions

**Attempt 3: GeoNames API** ✅ **SUCCESS**
- Free API with 30k requests/day
- Single API call gets ZIP → lat/lon + timezone
- Accurate, reliable, no dependencies

**The Implementation:**

1. **Created `geocode_zip.py` script**
   - Runs AFTER WiFi connects (when internet is available)
   - Calls GeoNames API twice:
     ```python
     # 1. ZIP → Coordinates
     postalCodeSearchJSON?postalcode={zip}&country=US&username={user}

     # 2. Coordinates → Timezone
     timezoneJSON?lat={lat}&lng={lon}&username={user}
     ```
   - Saves lat, lon, and timezone to device_config.json

2. **Integrated into WiFi connection flow**
   - `connect_saved_wifi.py` calls `geocode_zip.py` after successful WiFi connection
   - Runs automatically in background
   - No manual intervention needed

**Example API Response:**
```json
{
  "postalCodes": [{
    "lat": 39.666,
    "lng": -104.835,
    "placeName": "Aurora",
    "adminName1": "Colorado"
  }]
}

{
  "timezoneId": "America/Denver",
  "gmtOffset": -7,
  "dstOffset": -6
}
```

**Files Modified:**
- Created `geocode_zip.py` - Automatic ZIP→lat/lon→timezone conversion
- `connect_saved_wifi.py` - Added call to `geocode_zip.py` after WiFi connects

**Lesson Learned:**
- ✅ **Check what data the display code actually expects** before assuming what to send
- ✅ **Geocoding requires internet** - do it AFTER WiFi connects, not during AP mode
- ✅ **Free APIs are elegant** - GeoNames is more reliable than homebrew longitude logic
- ✅ **Sign up for accounts early** - 30 seconds to create GeoNames account vs. hours debugging
- ✅ **Read the API docs** - `postalCodeSearchJSON` doesn't include timezone, need `timezoneJSON` too

---

## Development Process Lessons

### "NO More Guessing"

**What Happened:**
After several failed attempts, user said: "NO more guessing lets get this right."

**What We Were Doing Wrong:**
- Making assumptions about why things failed
- Trying fixes without understanding root cause
- Not asking enough questions about actual behavior

**What We Changed:**
1. Asked specific questions about observable behavior:
   - "What happens when you hit share?" → "nothing happens"
   - "What happens to WiFi after portal?" → "still broadcasting lee-abad"
   - "Can you SSH in?" → "The pipe breaks cause it leaves the network"

2. Used facts from those answers to identify root causes:
   - Share button → JavaScript failing silently
   - WiFi not switching → Script never runs because SSH pipe breaks

3. Fixed based on facts, not assumptions

**Lesson Learned:**
- ✅ **Ask user for observable facts** when debugging
- ✅ Don't guess at solutions - understand the problem first
- ✅ "What do you see?" > "What do you think is wrong?"

---

### Why Didn't We Look There First?

**Pattern:** Many issues could have been caught earlier with different debugging approach.

**Better Debugging Flow:**

1. **Start with the System, Not the Code**
   - Check dependencies installed (`apt list --installed | grep hostapd`)
   - Verify services running (`systemctl status`)
   - Check file permissions (`ls -la`)

2. **Verify Config Paths Match**
   - Grep for config file usage across ALL files
   - Don't assume files are in same location
   - Check actual file contents, not just code

3. **Test with Real Devices**
   - Captive portal behavior varies by OS
   - Don't assume laptop = phone behavior
   - Test on iOS AND Android

4. **Check Process Lifecycle**
   - What happens when SSH disconnects?
   - Will this script keep running if parent dies?
   - Do background processes detach properly?

5. **Verify API Calls Actually Work**
   - Don't assume external APIs are reliable
   - Add logging for failures
   - Test with actual data, not mock responses

**Lesson Learned:**
- ✅ **Bottom-up debugging** (system → network → code) often faster than top-down
- ✅ Question assumptions about "simple" things
- ✅ Grep first, code second

---

## Technical Insights

### NetworkManager vs wpa_supplicant

**Issue:** `connect_saved_wifi.py` was using `wpa_supplicant` but NetworkManager was controlling WiFi.

**Why It Matters:**
- Raspberry Pi OS uses **NetworkManager** by default
- wpa_supplicant commands get ignored if NetworkManager is managing the interface
- Need to check which system controls WiFi: `nmcli dev status`

**The Fix:**
Rewrote `connect_saved_wifi.py` to use `nmcli` exclusively:

```python
# BEFORE (broken - wpa_supplicant)
def connect_to_wifi(ssid, password):
    wpa_supplicant_conf = f"""
network={{
    ssid="{ssid}"
    psk="{password}"
}}
"""
    # Write to /etc/wpa_supplicant/wpa_supplicant.conf
    # Run wpa_cli reconfigure
    # ❌ NetworkManager ignores this!

# AFTER (working - NetworkManager)
def connect_with_networkmanager(ssid, password):
    # Check if connection exists by SSID
    success, stdout, _ = run_cmd("nmcli -t -f NAME,TYPE,DEVICE con show")
    for line in stdout.strip().split('\n'):
        if ':wifi' in line:
            name = line.split(':')[0]
            success, details, _ = run_cmd(f"nmcli -t -f 802-11-wireless.ssid con show '{name}'")
            if ssid in details:
                # Found existing connection, bring it up
                run_cmd(f"nmcli con up '{name}'")
                return True

    # Create new connection if doesn't exist
    run_cmd(f"nmcli dev wifi connect '{ssid}' password '{password}'")
```

**Lesson Learned:**
- ✅ **Check which system manages WiFi** before writing WiFi scripts
- ✅ NetworkManager and wpa_supplicant don't play well together
- ✅ Use `nmcli` for modern Raspberry Pi OS

---

### Captive Portal Auto-Open Requires Specific Routes

**Key Discovery:** It's not enough to redirect DNS - you need specific Flask routes.

**Required Routes:**
```python
@app.route('/hotspot-detect.html')      # iOS/macOS
@app.route('/generate_204')              # Android
@app.route('/gen_204')                   # Android (alternative)
@app.route('/ncsi.txt')                  # Windows
@app.route('/connecttest.txt')           # Windows (alternative)
def captive_portal_detection():
    return redirect('/setup/wifi')
```

**Why These Specific URLs:**
- Each OS has hardcoded URLs it checks
- Must return HTTP redirect (302) to trigger portal
- Returning 200 OK makes OS think internet is working

**Lesson Learned:**
- ✅ Captive portals need **both** DNS redirect AND Flask routes
- ✅ Different OS = different detection URLs
- ✅ Return 302 redirect, not 200 OK

---

### Background Subprocess for Network Changes

**Key Pattern:** When your script needs to survive network changes:

```python
subprocess.Popen(
    ['sudo', 'python3', '/path/to/script.py'],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True  # ✅ CRITICAL!
)
```

**What `start_new_session=True` Does:**
- Creates new session ID (not child of current process)
- Script survives even if parent process dies
- Essential for network operations that kill connections

**When to Use:**
- WiFi switching scripts
- Network restart operations
- Any operation that might kill the SSH/parent connection

**Lesson Learned:**
- ✅ Use `start_new_session=True` for network operations
- ✅ Background processes need detachment from parent
- ✅ Don't rely on parent process staying alive

---

## Success Metrics

**What Actually Worked:**

✅ Portal auto-opens on iOS (after captive detection URLs added)
✅ Complete setup flow: WiFi → Crew → Info → Guide → Done
✅ Pi automatically switches to home WiFi (after `/api/finish` added)
✅ Weather displays correctly (after config sync fixed)
✅ Messages show contacts (after config sync fixed)
✅ Friend code copy works reliably
✅ No manual Ctrl+C or power cycle needed

**End-to-End Flow (Current):**
1. Pi boots in AP mode (LEE-ABAD)
2. Phone connects, portal auto-opens
3. User enters WiFi credentials → SAVED (not connected yet)
4. User creates/joins crew
5. User enters name, contacts, ZIP code
6. User reads guide
7. `/done` page auto-calls `/api/finish`
8. Pi connects to home WiFi in background
9. **Automatic geocoding:** `geocode_zip.py` runs, calls GeoNames API
10. Config updated with lat/lon + timezone
11. Display shows weather and **local time**
12. **DONE!** No SSH needed, no manual configuration.

---

## Future Improvements

### Short Term
1. ✅ **ZIP geocoding** - COMPLETE! Using GeoNames API with automatic timezone detection
2. ✅ **Refactor config files** - COMPLETE! Opus refactored to use gadget_main format directly
3. **Add dependency checks** - Verify hostapd/dnsmasq installed before running
4. **Better error messages** - Show user-friendly errors when things fail
5. **Test GeoNames reliability** - Verify it works for edge cases (Alaska, Hawaii, etc.)

### Long Term
1. **Web interface at leeloo.local** - View crew code without re-running setup
2. **Voice command for crew code** - "Hey LEELOO, what's my crew code?"
3. **OTA updates** - Update code without SSH
4. **Setup service** - Auto-start portal on boot if no WiFi configured

---

## Key Takeaways

### 🎯 Debug Smarter
1. **System first, code second** - Check dependencies, services, permissions
2. **Verify assumptions** - Config paths, API calls, network state
3. **Ask for observable facts** - "What do you see?" not "What's wrong?"
4. **Grep before coding** - Find all usage of files/functions first

### 🎯 Build Better
1. **Test on real devices** - iOS/Android behave differently
2. **Think about process lifecycle** - What happens when parent dies?
3. **Single source of truth** - One config format, synced if needed
4. **Fail loudly** - Log errors, don't fail silently
5. **API calls need internet** - Geocoding during AP mode = no internet!
6. **Use existing services** - GeoNames > homebrew longitude approximations

### 🎯 User Experience
1. **Set honest expectations** - Don't promise features that can't work
2. **Automate critical steps** - Don't rely on manual Ctrl+C
3. **Remove broken features** - Better to have less that works than more that doesn't

---

## Files Changed Summary

| File | Changes | Why |
|------|---------|-----|
| `wifi_manager.py` | Added captive portal detection URLs to dnsmasq | Portal auto-open on iOS/Android |
| `captive_portal.py` (Opus refactor) | Refactored to use gadget_main config format directly, added detection routes, removed immediate WiFi connect, added `/api/finish`, simplified share button | Fixed config mismatch, portal closing, auto WiFi switch |
| `connect_saved_wifi.py` | Rewrote to use NetworkManager exclusively, added call to `geocode_zip.py` after WiFi connects | wpa_supplicant didn't work; enables automatic geocoding |
| `geocode_zip.py` | **NEW FILE** - Automatic ZIP→lat/lon→timezone using GeoNames API | Weather + local time without manual config |
| `test_captive_portal_v2.sh` | Added `sudo` to connect script call | Permission denied without sudo |

---

## Commands Reference

```bash
# Check dependencies installed
apt list --installed | grep -E 'hostapd|dnsmasq'

# Install dependencies
sudo apt-get install -y hostapd dnsmasq

# Check WiFi manager
nmcli dev status

# View NetworkManager connections
nmcli con show

# Check config files
cat /home/pi/leeloo_config.json
cat /home/pi/leeloo-ui/device_config.json
cat /home/pi/leeloo-ui/crew_config.json

# Check running processes
ps aux | grep gadget_main
ps aux | grep captive_portal

# Test captive portal
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh
```

---

**Date:** February 10, 2026
**Project:** LEELOO Pi Zero - Captive Portal WiFi Setup
**Status:** ✅ Working end-to-end
