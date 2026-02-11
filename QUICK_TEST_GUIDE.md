# Quick Captive Portal Test Guide

## Run the Test

SSH into the Pi and run:

```bash
ssh pi@leeloo.local
# password: gadget

cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh
```

Press `y` to start.

## Expected Behavior

### What You'll See on Pi:
```
============================================================
LEELOO Captive Portal Test v2
============================================================

[1/5] Stopping main service...
[2/5] Telling NetworkManager to release wlan0...
[3/5] Starting AP mode with improved config...
AP mode started: LEE-XXXX
[4/5] Checking dnsmasq config...
[5/5] Starting captive portal web server...

==========================================
AP MODE IS NOW ACTIVE!
==========================================

WiFi Network: LEE-XXXX
Portal URL: http://192.168.4.1

Connect your phone/laptop to the LEE-XXXX WiFi network.
The captive portal should auto-open.

Press Ctrl+C when done testing.

* Running on all addresses (0.0.0.0)
* Running on http://192.168.4.1:80
```

### What You'll See on Phone/Laptop:

1. **WiFi Settings** - See `LEE-XXXX` network (replace XXXX with actual ID)
2. **Connect** - Join the network (no password needed)
3. **Auto-Open Portal** - Should automatically open setup page
4. **Step 1: WiFi Setup**
   - Select your home WiFi from dropdown
   - Enter password
   - Click "Connect"
   - **Portal stays open** ✅
5. **Step 2: User Info**
   - Enter your first name
   - Enter crew members (optional)
   - Enter ZIP code for weather
   - Click "Continue"
6. **Step 3: Crew Setup**
   - Choose "Create a new crew" or "Join existing crew"
   - If creating: Enter crew name, get invite code
   - If joining: Enter invite code from friend
7. **Step 4: Quick Guide**
   - Swipe through interactive guide
   - Learn how to use LEELOO
8. **Step 5: Done!**
   - See "Setup Complete" message
   - Wait 3 seconds
   - Pi automatically connects to your WiFi
   - You can close the portal

### What Happens After:

- Pi leaves AP mode
- Pi connects to your home WiFi
- Pi starts the main display service
- Display shows weather, time, etc.
- Setup is complete! 🎉

## Troubleshooting

### Portal doesn't auto-open?
- Manually go to `http://192.168.4.1` in your browser

### Can't see LEE-XXXX WiFi?
- Make sure you're within 10-20 feet of the Pi (weak antenna)
- Try restarting the test script

### Portal closes after WiFi step?
- You're probably still running the OLD version
- Make sure `captive_portal.py` was updated (check timestamp)
- Re-copy files: `scp captive_portal.py pi@leeloo.local:/home/pi/leeloo-ui/`

### Pi doesn't reconnect to home WiFi?
- Power cycle it - it should reconnect on boot
- Or manually reconnect: `sudo nmcli dev set wlan0 managed yes`

## Stop the Test

Press `Ctrl+C` in the SSH session to stop the portal.

The script will automatically:
1. Stop AP mode
2. Re-enable NetworkManager control
3. Restart the main gadget service

Your Pi should reconnect to your home WiFi automatically.
