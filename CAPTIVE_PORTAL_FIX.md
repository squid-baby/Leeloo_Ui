# Captive Portal Detection Fix

## The Problem

You could see and connect to the `LEE-XXXX` WiFi network, but the captive portal didn't auto-open on your device.

## Root Cause

Modern devices (iOS, Android, Windows) use **captive portal detection** by trying to access specific URLs when joining a network. If those URLs don't respond correctly, the portal won't auto-open.

### How Captive Portal Detection Works:

1. **iOS/macOS** tries: `http://captive.apple.com/hotspot-detect.html`
2. **Android** tries: `http://clients3.google.com/generate_204`
3. **Windows** tries: `http://www.msftconnecttest.com/connecttest.txt`
4. **Firefox** tries: `http://detectportal.firefox.com/success.txt`

If these URLs fail or return the wrong response, the device assumes there's internet and **doesn't open the captive portal**.

## What I Fixed

### 1. Enhanced dnsmasq Configuration (`wifi_manager.py`)

**Before:**
```conf
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.254,255.255.255.0,24h
address=/#/192.168.4.1
```

**After:**
```conf
interface=wlan0
bind-interfaces

# DHCP Server
dhcp-range=192.168.4.2,192.168.4.254,255.255.255.0,24h
dhcp-option=3,192.168.4.1  # Gateway
dhcp-option=6,192.168.4.1  # DNS Server

# Don't use upstream DNS (no internet)
no-resolv
no-poll

# Redirect ALL DNS queries
address=/#/192.168.4.1

# Specific captive portal detection URLs
address=/captive.apple.com/192.168.4.1
address=/clients3.google.com/192.168.4.1
address=/connectivitycheck.gstatic.com/192.168.4.1
address=/msftconnecttest.com/192.168.4.1
address=/detectportal.firefox.com/192.168.4.1
```

**Key improvements:**
- ✅ `no-resolv` - Don't try to use upstream DNS servers (we have no internet)
- ✅ Explicit DHCP options for gateway and DNS
- ✅ Specific DNS entries for all major captive portal detection URLs

### 2. Added Captive Portal Detection Routes (`captive_portal.py`)

Added Flask routes to handle captive portal detection requests:

```python
# iOS/macOS
@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
def apple_captive_portal():
    return redirect('/setup/wifi')

# Android
@app.route('/generate_204')
@app.route('/gen_204')
def android_captive_portal():
    return redirect('/setup/wifi')

# Windows
@app.route('/connecttest.txt')
@app.route('/redirect')
def windows_captive_portal():
    return redirect('/setup/wifi')

# Firefox
@app.route('/success.txt')
@app.route('/canonical.html')
def firefox_captive_portal():
    return redirect('/setup/wifi')
```

**How it works:**
1. Device joins `LEE-XXXX` WiFi
2. Device tries to access `http://captive.apple.com/hotspot-detect.html`
3. dnsmasq redirects DNS query to `192.168.4.1`
4. Flask receives request at `/hotspot-detect.html`
5. Flask redirects to `/setup/wifi` (the portal page)
6. Device detects redirect and auto-opens captive portal! 🎉

## Testing

Run the improved test script:

```bash
ssh pi@leeloo.local  # password: gadget
cd /home/pi/leeloo-ui
./test_captive_portal_v2.sh
```

Then connect your phone/laptop to the `LEE-XXXX` WiFi network. The portal should now auto-open!

## References

Based on research from Raspberry Pi forums and GitHub discussions:
- [Captive portal with hostapd / dnsmasq broken](https://forums.raspberrypi.com/viewtopic.php?t=279047)
- [Captive Portal using DNSMasq](https://www.raspberrypi.org/forums/viewtopic.php?t=107071)
- [Rogue Captive Portal Guide](https://jerryryle.github.io/rogueportal/)

## What's Next

If the portal still doesn't auto-open:
1. Try manually navigating to `http://192.168.4.1` after connecting
2. Check if your device is set to "Private WiFi Address" mode (iOS) - this can interfere
3. Some corporate devices have captive portal detection disabled
