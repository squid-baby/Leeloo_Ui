# Captive Portal Fixes - Round 2

## Issues Fixed

### 1. WiFi Connection After Setup ✅

**Problem:** Portal completed successfully, but Pi didn't connect to WiFi.

**Root Cause:** The background thread that was supposed to connect to WiFi got killed when the Flask server stopped (when you pressed Ctrl+C or closed the portal).

**Solution:**
- Removed unreliable threading code from `captive_portal.py`
- Created `connect_saved_wifi.py` - a separate script that runs AFTER the portal exits
- Updated `test_captive_portal_v2.sh` to automatically run `connect_saved_wifi.py` when you press Ctrl+C

**How it works now:**
1. You complete setup in portal
2. Press Ctrl+C to stop portal server
3. Test script automatically runs `connect_saved_wifi.py`
4. Script reads saved WiFi credentials from config
5. Connects to WiFi
6. Starts main gadget service
7. Done! ✅

### 2. Confusing "Your Crew" Label ✅

**Problem:** Step 2 had a field labeled "YOUR CREW" which was confusing - it sounded like the crew name, but it was actually for friend names.

**Solution:** Changed the label to be clearer:

**Before:**
```
YOUR CREW:
[Amy, Ben, Sarah]
Comma-separated names of friends with LEELOOs
```

**After:**
```
FRIEND NAMES (optional):
[Amy, Ben, Sarah]
Names of friends you want to share music with
```

This makes it clear that:
- This is NOT the crew name
- It's for individual friend names
- It's optional
- The actual crew name comes later in step 3

### Regarding Your Question About Crew Flow

You asked: *"What happens if I put in names, then click 'already have a ID', and that person used nicknames?"*

Good news - **this is already handled correctly!** The flow is:

**Step 2: User Info**
- Enter YOUR name
- Enter FRIEND NAMES (optional - just for display on your device)
- Enter ZIP code

**Step 3: Crew Selection**
- Choose: Create new OR Join existing

**Step 3a: If Creating**
- Enter crew name
- Get invite code to share

**Step 3b: If Joining**
- Paste invite code
- Server validates code
- Server sends back the ACTUAL crew name and member list
- Your config gets updated with the correct crew info

So if you typed "Amy, Ben" on step 2, but then join a crew where they're listed as "AmyJ, Benjamin", that's fine! The crew data from the server overwrites your local friend list.

## Testing the Fixes

Run the test again:

```bash
ssh pi@leeloo.local
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh
```

Complete the setup flow, then **press Ctrl+C**.

You should see:
```
Captive portal stopped.

Checking if WiFi credentials were saved...
Connecting to Newtons House...
✅ Connected to Newtons House
Waiting for connection to stabilize...
Restarting main service...

✅ Setup complete! Device is ready.
```

## What Got Updated

1. ✅ `captive_portal.py` - Removed threading, fixed label
2. ✅ `connect_saved_wifi.py` - NEW: Separate WiFi connection script
3. ✅ `test_captive_portal_v2.sh` - Auto-run WiFi connection after portal exits

## Expected Flow Now

1. Run test script
2. Connect to `LEE-XXXX` on phone
3. Portal auto-opens ✅
4. Complete all steps (WiFi → Info → Crew)
5. See "Setup Complete" message
6. Press Ctrl+C in SSH terminal
7. **Script automatically connects Pi to WiFi** ✅
8. Display starts working with real data ✅

Perfect flow! 🎉
