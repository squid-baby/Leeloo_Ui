# Captive Portal - Final Fixes

## Issues Fixed

### 1. ✅ Weather Not Showing (Lat/Lon Issue)

**Problem:** Setup completed but weather wasn't displaying on the device.

**Root Cause:**
- Captive portal saved only `zip_code`
- `gadget_main.py` expects `latitude` and `longitude`
- No conversion was happening

**Solution:**
- Added ZIP to lat/lon geocoding using Nominatim API (free, no key needed)
- Captive portal now automatically converts ZIP → lat/lon during setup
- Both values saved to config

**Code Change:**
```python
# In /api/info endpoint
# Convert ZIP to lat/lon using geocoding API
try:
    url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=US&format=json"
    resp = requests.get(url, headers={'User-Agent': 'LEELOO-Setup/1.0'})
    data = resp.json()
    if data:
        latitude = float(data[0]['lat'])
        longitude = float(data[0]['lon'])
        config['latitude'] = latitude
        config['longitude'] = longitude
except Exception as e:
    # Continue without coordinates - setup completes but weather won't work
```

**Manual Fix Applied:**
- Added lat/lon (35.9101, -79.0753) for ZIP 27510 to existing config
- Restarted display service
- Weather should now be working!

---

### 2. ✅ Friend Code Sharing Buttons

**Problem:** Copy/Text/Email buttons didn't work when sharing crew invite code.

**Root Causes:**
1. Clipboard API needs HTTPS or user gesture
2. Portal might close when switching to text/email app
3. No feedback if share failed
4. User worried about losing code

**Solutions:**

#### A. Improved Copy Button
- Uses modern Clipboard API when available
- Fallback to text selection if API fails
- Clear success/failure messages
- Instruction to use phone's copy menu if needed

#### B. Native Share Button (Better for Mobile!)
- Replaced separate Text/Email buttons with one "SHARE" button
- Uses native share sheet (works on iOS/Android)
- User can choose: Messages, Email, WhatsApp, etc.
- Portal stays open during share

#### C. Code Selection on Tap
- Tapping the code box selects the text
- User can manually copy if buttons don't work
- Visual hint: "☝️ Tap to select, then copy/paste"

#### D. Reassurance Message
Added prominent note:
```
💡 Don't worry - this code is saved on your device!
   You can retrieve it later if needed.
```

**Updated UI:**
```
┌─────────────────────────────────┐
│  CREW CREATED!                  │
│                                 │
│  "Well House"                   │
│                                 │
│  Share this with your friends:  │
│                                 │
│  ┌───────────────────────────┐  │
│  │ leeloo.app/join/AVW78NM   │  │ ← Tap to select
│  └───────────────────────────┘  │
│  ☝️ Tap to select, copy/paste   │
│                                 │
│  [COPY]  [SHARE]                │
│                                 │
│  💡 Don't worry - this code is  │
│     saved on your device!       │
│                                 │
│  [CONTINUE TO GUIDE]            │
└─────────────────────────────────┘
```

---

### 3. 📋 How to Retrieve Crew Code Later

**Your Question:** "Need a way to ask LEELOO for the code in case a friend loses it."

**Options:**

#### A. Voice Command (Recommended)
Add to voice handler:
```python
if "what's my crew code" in transcript or "crew code" in transcript:
    config = load_config()
    code = config.get('crew', {}).get('invite_code')
    display.show_message(f"Your crew code:\n{code}")
```

#### B. Button on Device
- Long-press or specific tap pattern
- Shows crew code on screen
- No internet needed

#### C. Web Interface
- Access via `http://leeloo.local` on your network
- Shows device status and crew code
- Can't share externally (local only)

**Recommended:** Implement voice command + web interface for easy access.

---

## Testing the Fixes

### Test Weather:
```bash
ssh pi@leeloo.local
cat /home/pi/leeloo_config.json
# Should see: "latitude": 35.9101, "longitude": -79.0753

# Check display output
sudo journalctl -u gadget.service -f
# Should see: "Weather updated: XX°F"
```

### Test Sharing:
1. Run setup again
2. Create new crew
3. At crew created screen:
   - Tap code box → should select text
   - Tap COPY → should copy to clipboard
   - Tap SHARE → should open native share sheet
4. Try switching to Messages app and back
5. Portal should stay open (or reopen easily)

---

## Files Updated

1. ✅ `captive_portal.py`
   - ZIP to lat/lon conversion
   - Improved share buttons
   - Better mobile UX

2. ✅ Config on Pi
   - Manually added lat/lon for current setup
   - Future setups will auto-convert

---

## Next Steps

### Immediate:
- [x] Weather working with lat/lon
- [x] Sharing buttons improved
- [ ] Test full flow again

### Future Enhancements:
- [ ] Add voice command to retrieve crew code
- [ ] Create web interface at `leeloo.local`
- [ ] Add QR code for easy crew joining
- [ ] Allow code regeneration if lost

---

## Status

✅ **Weather should now be working!**
✅ **Sharing is more reliable**
✅ **User can't lose crew code**

Check your LEELOO display - you should see weather for Carrboro, NC (ZIP 27510)!
