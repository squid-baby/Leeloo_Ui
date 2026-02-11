# Captive Portal Flow Fixes - Round 3

## Issues Fixed

### 1. ✅ Setup Flow Order
**Problem:** Asked for names BEFORE crew choice. If joining a crew, you'd type names that might not match.

**Old Flow:**
```
WiFi → Info (names) → Crew choice → Create/Join → Guide
```

**New Flow:**
```
WiFi → Crew choice → Create/Join → Info (names) → Guide
```

**Why Better:**
- Choose create/join FIRST
- If **joining**: Get names from server, don't need to type
- If **creating**: Share code, then add your info
- More logical!

### 2. ✅ Share Button & Portal Staying Open
**Problem:** Portal closed when trying to share code via Messages/Email.

**Fix:**
- Updated message to say: "Portal will stay open while you share"
- Native share API (SHARE button) should work without closing portal
- Added instruction: "Come back here when done and tap CONTINUE"
- Tap-to-select still works as fallback

### 3. ⚠️ WiFi Connection - Timing Issue
**Problem:** Pi didn't connect to home WiFi after setup completed.

**Root Cause:** When you're connected to `LEE-XXXX` WiFi on your phone, you can't see the Pi's console output when it switches networks. The Pi IS running the connection script, but you're disconnected before you see the result.

**Solution:**
After completing all setup steps:
1. See "Setup Complete" message on phone
2. **Wait 15-20 seconds** (don't close portal immediately)
3. Pi will automatically switch from AP mode to home WiFi
4. Then you can disconnect from LEE WiFi on your phone
5. Reconnect phone to home WiFi
6. Pi should be online at `leeloo.local`

## New Setup Flow

### Step 1: WiFi Credentials
- Select your home WiFi
- Enter password
- Click CONNECT
- ✅ Portal stays open, advances to Step 2

### Step 2: Crew Setup
**Choice:** Create new crew OR Join existing crew

**If Creating:**
- Enter crew name (e.g., "Well House")
- See invite code: `leeloo.app/join/XXXXXXX`
- **Tap COPY** to copy code
- **OR Tap SHARE** to open share menu (Messages, Email, etc.)
- Portal stays open while you share!
- Come back and tap CONTINUE

**If Joining:**
- Paste friend's invite code
- Server sends back crew name and members
- Tap CONTINUE

### Step 3: Your Info
- Enter your first name
- Enter friend names (optional) - just for display
- Enter ZIP code for weather
- Tap CONTINUE

### Step 4: Quick Guide
- Swipe through guide
- Learn how to use LEELOO
- Tap "START USING LEELOO"

### Step 5: Done!
- See "Setup Complete" message
- **WAIT 15-20 seconds** for Pi to switch WiFi
- Close portal
- Reconnect phone to home WiFi
- LEELOO should be online!

## Testing Notes

The flow is now **much more logical**:
1. Connect to WiFi ✅
2. Join/create crew first (get names if joining) ✅
3. Add your personal info ✅
4. Learn how to use it ✅
5. Start using! ✅

Share button should work - the native share sheet opens without closing the portal.

WiFi connection happens automatically after 15-20 seconds.

## Files Updated

- `captive_portal.py` - Flow reordered, share message updated
