# WiFi Setup Flow Fix

## The Problem

You could connect to the WiFi AP and the captive portal opened successfully, but when you entered WiFi credentials and clicked "Connect", the portal closed immediately and didn't advance to step 2.

## Root Cause

The `/api/wifi` endpoint was **immediately** calling `connect_to_wifi()`, which:
1. Stopped AP mode
2. Disconnected your phone from the `LEE-XXXX` WiFi
3. Tried to connect the Pi to your home WiFi
4. Your phone lost connection before it could see the response
5. Portal closed ❌

## The Fix

Changed the setup flow to be **non-blocking**:

### Old Flow (Broken):
```
Step 1: Phone submits WiFi credentials
        ↓
Step 2: Server IMMEDIATELY stops AP and connects to WiFi
        ↓
Step 3: Phone disconnects (AP is gone)
        ↓
Step 4: Portal closes ❌
```

### New Flow (Fixed):
```
Step 1: Phone submits WiFi credentials
        ↓
Step 2: Server SAVES credentials (stays in AP mode)
        ↓
Step 3: Server responds with success
        ↓
Step 4: Phone advances to step 2 (user info) ✅
        ↓
Step 5: Phone completes crew setup
        ↓
Step 6: Server connects to WiFi in background thread
        ↓
Step 7: After 3 seconds, Pi switches to home WiFi
```

## Code Changes

### 1. Modified `/api/wifi` endpoint

**Before** (line 971-996):
```python
@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    """Connect to WiFi network"""
    # ... validation ...

    # Try to connect IMMEDIATELY
    if connect_to_wifi(ssid, password):
        setup_state['connected'] = True
        return jsonify({'success': True})
    else:
        # Restart AP mode
        start_ap_mode()
        return jsonify({'success': False, 'error': 'Failed to connect'})
```

**After**:
```python
@app.route('/api/wifi', methods=['POST'])
def api_wifi():
    """Save WiFi credentials (don't connect yet)"""
    # ... validation ...

    # Save credentials to config file
    config = load_config()
    config['wifi_ssid'] = ssid
    config['wifi_password'] = password

    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

    # Success! Phone can proceed to next step
    return jsonify({'success': True})
```

### 2. Added WiFi connection at END of setup

Modified both `/api/crew/create` and `/api/crew/join` to connect after setup completes:

```python
setup_state['step'] = 'done'

# Setup complete! Connect to WiFi in background
import threading

def connect_wifi_and_exit():
    time.sleep(3)  # Give phone time to see "done" page
    wifi_ssid = config.get('wifi_ssid')
    wifi_password = config.get('wifi_password')
    if wifi_ssid and wifi_password:
        print(f"Setup complete - connecting to {wifi_ssid}...")
        connect_to_wifi(wifi_ssid, wifi_password)

threading.Thread(target=connect_wifi_and_exit, daemon=True).start()

return jsonify({'success': True, ...})
```

## Testing the Fix

Run the test script:

```bash
ssh pi@leeloo.local  # password: gadget
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh
```

Then on your phone:
1. Connect to `LEE-XXXX` WiFi
2. Portal auto-opens ✅
3. Select your WiFi and enter password
4. Click "Connect"
5. **Portal stays open and advances to step 2** ✅
6. Complete user info (name, contacts, zip)
7. Create or join crew
8. See "Setup Complete" message
9. After 3 seconds, Pi connects to your WiFi automatically
10. Portal can be closed

## What Happens After Setup

1. Phone shows "Setup Complete" page
2. After 3 seconds, Pi switches from AP mode to WiFi client mode
3. Pi connects to your home WiFi
4. `gadget_main.py` service starts showing the display
5. Weather, time, and other data start working

## Files Modified

- `captive_portal.py` - Fixed WiFi flow and added delayed connection
- `wifi_manager.py` - Already had the improved dnsmasq config from previous fix

## Next Time You Test

The full flow should now work:
1. ✅ Portal opens automatically
2. ✅ WiFi credentials saved
3. ✅ Advances through all setup steps
4. ✅ Connects to WiFi at the end
5. ✅ Device ready to use!
