# USB OTG + WiFi on LeeLoo Pi Zero

## One-time setup (while WiFi works)

```bash
# From your laptop:
bash setup_usb_otg.sh
```

This adds USB ethernet gadget mode to the Pi. After rebooting:
- Plug USB-A → micro-USB into the Pi's **center (OTG) port** — NOT the power port
- Pi appears as a USB network adapter on your laptop
- SSH: `ssh pi@192.168.7.2`

## Change WiFi credentials

Once connected via USB:
```bash
leeloo-wifi "NewNetworkName" "YourPassword"
```

Or manually:
```bash
nmcli device wifi connect "SSID" password "password"
```

Check connection:
```bash
nmcli connection show
nmcli device status
```

---

## Path B: WiFi already down (no SSH, edit SD card)

If you can't SSH in at all, pop the SD card and edit from a Mac/PC.

### Change WiFi via SD card

1. Eject SD card from Pi, insert into laptop
2. The card mounts as two volumes: `bootfs` and `rootfs`
3. On `rootfs`, navigate to:
   ```
   /etc/NetworkManager/system-connections/
   ```
4. You'll see a `.nmconnection` file for your existing WiFi network
5. Open it in a text editor and change the `psk=` line:
   ```ini
   [wifi-security]
   psk=YourNewPassword
   ```
6. Save, eject, reinsert SD card into Pi, power on

### Enable USB OTG via SD card (so you never need this again)

1. On `bootfs` (the small FAT partition):

   Edit `config.txt` — add at the bottom:
   ```
   # USB OTG gadget ethernet
   dtoverlay=dwc2
   ```

   Edit `cmdline.txt` — find `rootwait` and append immediately after (same line, space-separated):
   ```
   modules-load=dwc2,g_ether
   ```
   Example full line:
   ```
   console=serial0,115200 console=tty1 root=PARTUUID=xxx rootfstype=ext4 rootwait modules-load=dwc2,g_ether fbcon=map:0
   ```

2. On `rootfs`, create `/etc/network/interfaces.d/usb0`:
   ```
   auto usb0
   iface usb0 inet static
       address 192.168.7.2
       netmask 255.255.255.0
   ```

3. Put card back, boot Pi, plug USB into center port, then `ssh pi@192.168.7.2`

---

## Which port is the OTG port?

Pi Zero has two micro-USB ports:
- **LEFT port** (closer to corner): power only
- **RIGHT/CENTER port** (PWR label is on left one): OTG data port — use this one

On Pi Zero 2 W the OTG port is labeled with a small "USB" or has the data lines.
