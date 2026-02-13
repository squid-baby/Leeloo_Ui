# LEELOO Bluetooth Music Playback Integration

**Status**: Future Feature - Planned Design
**Date**: 2026-02-12
**Type**: Option C - Full Integration

---

## Vision

Transform LEELOO from a **display-only music sharing device** into a **fully interactive music player** with Bluetooth audio output. Users can tap album art on the touchscreen to play tracks through a paired Bluetooth speaker, building a collaborative playlist over time.

---

## Complete User Experience Flow

### Phase 1: First Boot & Setup

```
User Powers On LEELOO
  ↓
Splash Screen
  ↓
WiFi Setup (existing captive portal)
  ├─ Connects to "LEELOO-XXXX" network
  ├─ Selects home WiFi + password
  └─ Device connects to internet
  ↓
[NEW] Spotify OAuth Login
  ├─ Screen shows: "Sign in to Spotify"
  ├─ Displays QR code for auth flow
  ├─ User scans with phone → authorizes LEELOO
  ├─ Tokens saved to device_config.json
  └─ Optional: Skip to sign in later
  ↓
[NEW] Bluetooth Speaker Pairing
  ├─ Screen shows: "Pair Bluetooth Speaker"
  ├─ Scans for 10 seconds
  ├─ Displays list of discoverable speakers
  ├─ User TAPS speaker name on touchscreen
  ├─ Pairs and saves to device_config.json
  └─ Optional: Skip to pair later
  ↓
Crew Setup (existing)
  ↓
Main Display (existing UI - no visual changes)
```

### Phase 2: Receiving Music

```
Friend Sends "Regulate" by Warren G
  ↓
Relay Server → WebSocket → leeloo_client.py
  ↓
Downloads Scancode + Album Art
  ↓
Saves to current_music.json + Adds to playlist.json
  {
    "spotify_uri": "spotify:track:7nYvUtkQMx1v80S2FH2s9J",
    "artist": "Warren G, Nate Dogg",
    "track": "Regulate",
    "album": "Regulate... G Funk Era",
    "pushed_by": "Marcus",
    "playback_state": "queued",  // NEW FIELD
    "added_at": 1770907822
  }
  ↓
Main Loop Updates Display (1s refresh)
  ├─ Album art appears (243x304px)
  ├─ Scancode at bottom
  ├─ Text box shows artist/track/album
  └─ [NEW] Touch region active on album frame (218-478, 4-322)
```

### Phase 3: Touch to Play

```
User Taps Album Art (anywhere in album frame region)
  ↓
Touch Event Handler (touch_handler.py)
  ├─ Detects tap in region (x: 218-478, y: 4-322)
  ├─ Calls playback_manager.play_track(spotify_uri)
  └─ Updates current_music.json: "playback_state": "playing"
  ↓
Playback Manager (playback_manager.py)
  ├─ Launches librespot with OAuth tokens
  ├─ Connects to Spotify (device appears as "LEELOO")
  ├─ Streams track through Bluetooth to paired speaker
  └─ Updates playback state in real-time
  ↓
Display Updates (Minimal Visual Feedback)
  ├─ Small "♪" icon appears at (470, 10) - top-right of album frame
  ├─ White color, font_large size
  └─ No layout changes - just icon overlay
```

### Phase 4: Playlist Auto-Play

```
Track Finishes (detected via librespot event)
  ↓
Playback Manager checks playlist.json
  ├─ Finds next queued track (chronological order)
  ├─ Auto-plays next track
  └─ Updates current_music.json to show new track
  ↓
Display automatically updates (1s refresh cycle)
  ├─ New album art appears
  ├─ New scancode
  ├─ New metadata
  └─ "♪" icon remains (still playing)
  ↓
Cycle continues until playlist exhausted
```

---

## Technical Architecture

### New Components Required

#### 1. Touch Input Handler (`touch_handler.py`)

**Purpose**: Listen to touchscreen events from `/dev/input/eventX`

**Dependencies**:
- `evdev` library (standard on Raspberry Pi OS)
- Waveshare touchscreen driver (confirmed working)

**Implementation**:
```python
from evdev import InputDevice, categorize, ecodes
import threading

class TouchHandler:
    def __init__(self, device_path="/dev/input/event0", callback=None):
        self.device = InputDevice(device_path)
        self.callback = callback
        self.touch_regions = {}  # Dict of named regions

    def register_region(self, name, x1, y1, x2, y2, handler):
        """Register a tappable region"""
        self.touch_regions[name] = {
            'bounds': (x1, y1, x2, y2),
            'handler': handler
        }

    def start_listening(self):
        """Background thread to monitor touch events"""
        thread = threading.Thread(target=self._listen_loop, daemon=True)
        thread.start()

    def _listen_loop(self):
        """Main event loop - detects taps and calls handlers"""
        for event in self.device.read_loop():
            if event.type == ecodes.EV_ABS:
                x, y = self._get_touch_position(event)
                self._check_regions(x, y)

    def _check_regions(self, x, y):
        """Check if tap is inside any registered region"""
        for name, region in self.touch_regions.items():
            x1, y1, x2, y2 = region['bounds']
            if x1 <= x <= x2 and y1 <= y <= y2:
                region['handler']()  # Call the handler
```

**Touch Region for Album Frame**:
- Region name: `"album_art"`
- Bounds: `(218, 4, 478, 322)` - right side of screen
- Handler: `on_album_tap()` function

**Integration**:
```python
# In gadget_main.py
touch_handler = TouchHandler()
touch_handler.register_region(
    "album_art", 218, 4, 478, 322,
    handler=lambda: playback_manager.play_current_track()
)
touch_handler.start_listening()
```

---

#### 2. Playback Manager (`playback_manager.py`)

**Purpose**: Control Spotify playback via `librespot` Spotify Connect client

**Approach**: Use `librespot` (Rust-based, lightweight)
- Appears as Spotify Connect device named "LEELOO"
- Supports OAuth authentication
- ~10MB memory footprint
- Can be controlled programmatically via D-Bus or command-line

**State Management**:
```python
import subprocess
import json
import time

class PlaybackManager:
    def __init__(self, bluetooth_sink=None, oauth_tokens=None):
        self.playlist = []  # List of track dicts
        self.current_index = 0
        self.bluetooth_sink = bluetooth_sink  # MAC address
        self.oauth_tokens = oauth_tokens
        self.librespot_process = None
        self.is_playing = False

    def load_playlist(self):
        """Load from playlist.json"""
        with open('/home/pi/leeloo-ui/playlist.json', 'r') as f:
            data = json.load(f)
            self.playlist = data.get('tracks', [])
            self.current_index = data.get('current_index', 0)

    def play_track(self, spotify_uri):
        """Start playback of specific track"""
        # Launch librespot with OAuth tokens and Bluetooth output
        cmd = [
            'librespot',
            '--name', 'LEELOO',
            '--backend', 'alsa',
            '--device', self.bluetooth_sink,
            '--username', self.oauth_tokens['username'],
            '--token', self.oauth_tokens['access_token'],
            '--initial-track', spotify_uri
        ]
        self.librespot_process = subprocess.Popen(cmd)
        self.is_playing = True
        self.update_state('playing', spotify_uri)

    def play_current_track(self):
        """Play track currently shown on display"""
        with open('/home/pi/leeloo-ui/current_music.json', 'r') as f:
            music_data = json.load(f)
            self.play_track(music_data['spotify_uri'])

    def play_next(self):
        """Auto-advance to next track in playlist"""
        self.current_index += 1
        if self.current_index < len(self.playlist):
            next_track = self.playlist[self.current_index]
            self.play_track(next_track['spotify_uri'])
        else:
            self.is_playing = False
            self.update_state('idle', None)

    def on_track_end(self):
        """Called when track finishes (detected via event loop)"""
        # Mark current track as played
        self.playlist[self.current_index]['played'] = True
        self.save_playlist()
        # Play next
        self.play_next()

    def update_state(self, state, spotify_uri):
        """Update current_music.json with playback state"""
        with open('/home/pi/leeloo-ui/current_music.json', 'r+') as f:
            data = json.load(f)
            data['playback_state'] = state
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

    def save_playlist(self):
        """Persist playlist state"""
        with open('/home/pi/leeloo-ui/playlist.json', 'w') as f:
            json.dump({
                'tracks': self.playlist,
                'current_index': self.current_index
            }, f, indent=2)
```

**D-Bus Integration** (for track-end detection):
```python
import dbus
from dbus.mainloop.glib import DBusGMainLoop

def monitor_librespot():
    """Monitor librespot via D-Bus for track end events"""
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    player = bus.get_object('org.mpris.MediaPlayer2.librespot', '/org/mpris/MediaPlayer2')

    def on_property_changed(interface, changed, invalidated):
        if 'PlaybackStatus' in changed:
            if changed['PlaybackStatus'] == 'Stopped':
                playback_manager.on_track_end()

    player.connect_to_signal('PropertiesChanged', on_property_changed)
```

---

#### 3. Bluetooth Audio Manager (`bluetooth_audio.py`)

**Purpose**: Configure Pi to output audio to Bluetooth speaker (A2DP profile)

**Steps**:
1. Enable Bluetooth controller
2. Scan for nearby A2DP-capable devices
3. Display list on screen for touch selection
4. Pair, trust, and connect to selected speaker
5. Set speaker as default ALSA/PulseAudio sink
6. Save pairing info to `device_config.json`

**Implementation**:
```python
import subprocess
import re
import json

class BluetoothAudioManager:
    def enable_bluetooth(self):
        """Power on Bluetooth controller"""
        subprocess.run(['bluetoothctl', 'power', 'on'])
        subprocess.run(['bluetoothctl', 'agent', 'on'])
        subprocess.run(['bluetoothctl', 'default-agent'])

    def scan_speakers(self, timeout=10):
        """Scan for nearby Bluetooth speakers (A2DP devices)"""
        subprocess.run(['bluetoothctl', 'scan', 'on'])
        time.sleep(timeout)
        subprocess.run(['bluetoothctl', 'scan', 'off'])

        # Parse device list
        result = subprocess.run(['bluetoothctl', 'devices'],
                               capture_output=True, text=True)
        devices = []
        for line in result.stdout.splitlines():
            # Format: "Device AA:BB:CC:DD:EE:FF Speaker Name"
            match = re.match(r'Device ([\w:]+) (.+)', line)
            if match:
                mac, name = match.groups()
                devices.append({'mac': mac, 'name': name})

        return devices

    def pair_speaker(self, mac_address):
        """Pair with specific Bluetooth speaker"""
        subprocess.run(['bluetoothctl', 'pair', mac_address])
        subprocess.run(['bluetoothctl', 'trust', mac_address])
        subprocess.run(['bluetoothctl', 'connect', mac_address])

    def set_audio_sink(self, mac_address):
        """Configure PulseAudio to use Bluetooth speaker as default sink"""
        # Get PulseAudio sink ID for this Bluetooth device
        result = subprocess.run(['pactl', 'list', 'short', 'sinks'],
                               capture_output=True, text=True)

        # Find sink matching MAC address
        for line in result.stdout.splitlines():
            if mac_address.replace(':', '_') in line:
                sink_id = line.split()[0]
                subprocess.run(['pactl', 'set-default-sink', sink_id])
                break

    def save_pairing(self, mac_address, name):
        """Save speaker info to device_config.json"""
        with open('/home/pi/leeloo-ui/device_config.json', 'r+') as f:
            config = json.load(f)
            config['bluetooth_speaker'] = {
                'mac_address': mac_address,
                'name': name,
                'paired_at': time.time()
            }
            f.seek(0)
            json.dump(config, f, indent=2)
            f.truncate()
```

**Auto-Reconnect on Boot**:
```python
def reconnect_speaker():
    """Reconnect to saved Bluetooth speaker on boot"""
    with open('/home/pi/leeloo-ui/device_config.json', 'r') as f:
        config = json.load(f)
        speaker = config.get('bluetooth_speaker')
        if speaker:
            mac = speaker['mac_address']
            subprocess.run(['bluetoothctl', 'connect', mac])
            set_audio_sink(mac)
```

---

#### 4. Playlist Manager (`playlist_manager.py`)

**Purpose**: Build and persist collaborative playlist from shared tracks

**Data Structure** (`playlist.json`):
```json
{
  "tracks": [
    {
      "spotify_uri": "spotify:track:7nYvUtkQMx1v80S2FH2s9J",
      "artist": "Warren G, Nate Dogg",
      "track": "Regulate",
      "album": "Regulate... G Funk Era",
      "pushed_by": "Marcus",
      "added_at": 1770907822,
      "played": false
    },
    {
      "spotify_uri": "spotify:track:4LIpeIN0AxFMRhnm5tR0HJ",
      "artist": "Rawayana",
      "track": "Si Te Pica Es Porque Eres Tú",
      "album": "¿Dónde Es El After?",
      "pushed_by": "Sofia",
      "added_at": 1770908000,
      "played": false
    }
  ],
  "current_index": 0
}
```

**Operations**:
```python
class PlaylistManager:
    def __init__(self, playlist_path='/home/pi/leeloo-ui/playlist.json'):
        self.playlist_path = playlist_path
        self.tracks = []
        self.load()

    def load(self):
        """Load playlist from disk"""
        try:
            with open(self.playlist_path, 'r') as f:
                data = json.load(f)
                self.tracks = data.get('tracks', [])
        except FileNotFoundError:
            self.tracks = []

    def add_track(self, music_data):
        """Add newly shared track to playlist"""
        # Check if already in playlist
        if any(t['spotify_uri'] == music_data['spotify_uri'] for t in self.tracks):
            return  # Don't add duplicates

        # Append to playlist
        self.tracks.append({
            'spotify_uri': music_data['spotify_uri'],
            'artist': music_data['artist'],
            'track': music_data['track'],
            'album': music_data.get('album', 'Unknown Album'),
            'pushed_by': music_data.get('pushed_by', 'Unknown'),
            'added_at': time.time(),
            'played': False
        })
        self.save()

    def get_next_unplayed(self):
        """Return next track that hasn't been played"""
        for track in self.tracks:
            if not track['played']:
                return track
        return None

    def mark_played(self, spotify_uri):
        """Mark track as played"""
        for track in self.tracks:
            if track['spotify_uri'] == spotify_uri:
                track['played'] = True
                self.save()
                break

    def save(self):
        """Persist playlist to disk"""
        with open(self.playlist_path, 'w') as f:
            json.dump({'tracks': self.tracks}, f, indent=2)
```

---

#### 5. Spotify OAuth Flow (`spotify_oauth.py`)

**Purpose**: Handle Spotify user authentication via OAuth 2.0

**Flow**:
1. Generate authorization URL with PKCE
2. Display QR code on screen for user to scan
3. User authorizes LEELOO on their phone
4. Relay server receives callback with auth code
5. Exchange code for access + refresh tokens
6. Save tokens to `device_config.json`

**Implementation**:
```python
import requests
import secrets
import hashlib
import base64
import json
import qrcode
from PIL import Image

class SpotifyOAuth:
    CLIENT_ID = "your-client-id"  # Register LEELOO app at developer.spotify.com
    REDIRECT_URI = "https://relay.leeloo.fm/spotify/callback"
    SCOPES = "user-read-playback-state user-modify-playback-state streaming"

    def generate_auth_url(self):
        """Generate OAuth URL with PKCE challenge"""
        # Generate code verifier and challenge
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip('=')

        # Build authorization URL
        params = {
            'client_id': self.CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': self.REDIRECT_URI,
            'scope': self.SCOPES,
            'code_challenge_method': 'S256',
            'code_challenge': challenge
        }
        url = 'https://accounts.spotify.com/authorize?' + urlencode(params)

        return url, verifier

    def generate_qr_code(self, url):
        """Generate QR code image for OAuth URL"""
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")

    def exchange_code(self, auth_code, code_verifier):
        """Exchange authorization code for tokens"""
        data = {
            'client_id': self.CLIENT_ID,
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.REDIRECT_URI,
            'code_verifier': code_verifier
        }
        response = requests.post('https://accounts.spotify.com/api/token', data=data)
        tokens = response.json()

        return {
            'access_token': tokens['access_token'],
            'refresh_token': tokens['refresh_token'],
            'expires_at': time.time() + tokens['expires_in']
        }

    def save_tokens(self, tokens):
        """Save OAuth tokens to device_config.json"""
        with open('/home/pi/leeloo-ui/device_config.json', 'r+') as f:
            config = json.load(f)
            config['spotify_oauth'] = tokens
            f.seek(0)
            json.dump(config, f, indent=2)
            f.truncate()

    def refresh_access_token(self, refresh_token):
        """Refresh expired access token"""
        data = {
            'client_id': self.CLIENT_ID,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        response = requests.post('https://accounts.spotify.com/api/token', data=data)
        tokens = response.json()

        return {
            'access_token': tokens['access_token'],
            'expires_at': time.time() + tokens['expires_in']
        }
```

**OAuth Screen Rendering** (during first boot):
```python
def show_spotify_login_screen(display):
    """Render Spotify OAuth QR code on display"""
    oauth = SpotifyOAuth()
    url, verifier = oauth.generate_auth_url()

    # Generate QR code
    qr_image = oauth.generate_qr_code(url)

    # Render on display
    display.clear()
    display.draw_text("Sign in to Spotify", (20, 20), font_size=24)
    display.draw_text("Scan QR code with phone:", (20, 60), font_size=14)
    display.draw_image(qr_image.resize((200, 200)), (140, 100))
    display.update()

    # Wait for callback from relay server (via WebSocket)
    # Relay sends: {"type": "spotify_auth_complete", "code": "..."}
    auth_code = wait_for_spotify_callback()

    # Exchange code for tokens
    tokens = oauth.exchange_code(auth_code, verifier)
    oauth.save_tokens(tokens)
```

---

### Modified Components

#### 1. `leeloo_client.py` - WebSocket Handler

**Changes**:
- When receiving `on_song_push()`, also call `playlist_manager.add_track()`
- This builds the playlist as songs are shared

```python
# Add at top
from playlist_manager import PlaylistManager
playlist_manager = PlaylistManager()

async def on_song_push(self, data):
    """Called when friend shares a song"""
    music_data = {
        'spotify_uri': data['spotify_uri'],
        'artist': data['artist'],
        'track': data['track'],
        'album': data.get('album', 'Unknown Album'),
        'pushed_by': data.get('pushed_by', 'Unknown'),
        'playback_state': 'queued',  # NEW
        'timestamp': time.time()
    }

    # Existing: Save to current_music.json (for display)
    save_current_music(music_data)

    # NEW: Add to playlist
    playlist_manager.add_track(music_data)
```

#### 2. `gadget_main.py` - Main Loop

**Changes**:
- Initialize TouchHandler and PlaybackManager
- Register album art touch region
- Monitor playback state
- Update "♪" icon display

```python
from touch_handler import TouchHandler
from playback_manager import PlaybackManager

def main_loop():
    display = LeelooDisplay(preview_mode=False)

    # Load Bluetooth speaker and OAuth tokens
    device_config = load_device_config()
    bluetooth_sink = device_config.get('bluetooth_speaker', {}).get('mac_address')
    oauth_tokens = device_config.get('spotify_oauth')

    # NEW: Initialize touch and playback
    playback_manager = PlaybackManager(bluetooth_sink, oauth_tokens)
    playback_manager.load_playlist()

    touch_handler = TouchHandler()
    touch_handler.register_region(
        "album_art", 218, 4, 478, 322,
        handler=lambda: playback_manager.play_current_track()
    )
    touch_handler.start_listening()

    while True:
        # Existing weather, time, contacts, music loading...

        # NEW: Check playback state for icon
        music_data = load_current_music()
        is_playing = music_data.get('playback_state') == 'playing' if music_data else False

        # Existing render call, but add is_playing parameter
        img = display.render(
            weather_data, time_data, contacts, album_data,
            album_art_path=album_art_path,
            is_playing=is_playing  # NEW
        )

        write_to_framebuffer(img, fb_path="/dev/fb1")

        # NEW: Check if track ended
        if playback_manager.is_track_finished():
            playback_manager.play_next()

        time.sleep(1)
```

#### 3. `gadget_display.py` - Display Renderer

**Changes**:
- Add `is_playing` parameter to `render()` and `draw_album_art()`
- Draw "♪" icon when playing

```python
def render(self, weather_data, time_data, contacts, album_data,
           album_art_path=None, is_playing=False):
    """Render full display - NEW is_playing parameter"""
    # ... existing code ...

    # Draw album art with playing indicator
    album_x = self.draw_album_art(
        album_art_path=album_art_path,
        show_empty_scancode=(not album_data),
        is_playing=is_playing  # NEW
    )

    # ... rest of rendering ...

def draw_album_art(self, album_art_path=None, show_empty_scancode=False, is_playing=False):
    """Draw album art on right side - NEW is_playing parameter"""
    # ... existing album art rendering ...

    # NEW: Draw playing indicator
    if is_playing:
        # Small music note icon in top-right corner of album frame
        # Position: (470, 10) - 8px from right edge, 6px from top
        self.draw.text((470, 10), "♪", font=self.font_large, fill=COLORS['white'])

    return album_x
```

#### 4. `leeloo_boot.py` - Boot Sequence

**Changes**:
- Add Spotify OAuth screen after WiFi setup
- Add Bluetooth pairing screen after OAuth
- Use touch input for speaker selection

```python
def first_boot_sequence():
    """Complete first-boot setup flow"""
    display = LeelooDisplay(preview_mode=False)

    # 1. Splash screen (existing)
    show_splash()

    # 2. WiFi setup (existing)
    if not is_wifi_connected():
        run_captive_portal()

    # 3. NEW: Spotify OAuth
    if not has_spotify_tokens():
        show_spotify_login_screen(display)

    # 4. NEW: Bluetooth speaker pairing
    if not has_bluetooth_speaker():
        show_bluetooth_pairing_screen(display)

    # 5. Crew setup (existing)
    if Config.crew_code_required() and not has_crew():
        show_crew_setup()

    # 6. Mark first boot complete
    Config.mark_first_run_complete()

def show_bluetooth_pairing_screen(display):
    """Interactive Bluetooth speaker pairing with touchscreen"""
    bt_manager = BluetoothAudioManager()
    bt_manager.enable_bluetooth()

    # Show scanning screen
    display.clear()
    display.draw_text("Pairing Bluetooth Speaker", (20, 20), font_size=24)
    display.draw_text("Scanning...", (20, 60), font_size=14)
    display.update()

    # Scan for speakers
    speakers = bt_manager.scan_speakers(timeout=10)

    # Render speaker list with touch regions
    display.clear()
    display.draw_text("Tap your speaker:", (20, 20), font_size=18)

    touch_handler = TouchHandler()
    y_pos = 60
    for i, speaker in enumerate(speakers):
        # Draw speaker name
        display.draw_text(speaker['name'], (40, y_pos), font_size=14)

        # Register touch region for this speaker
        touch_handler.register_region(
            f"speaker_{i}", 20, y_pos, 460, y_pos + 30,
            handler=lambda s=speaker: select_speaker(bt_manager, s)
        )
        y_pos += 40

    display.update()
    touch_handler.start_listening()

    # Wait for selection
    wait_for_speaker_selection()

def select_speaker(bt_manager, speaker):
    """Handle speaker selection"""
    bt_manager.pair_speaker(speaker['mac'])
    bt_manager.set_audio_sink(speaker['mac'])
    bt_manager.save_pairing(speaker['mac'], speaker['name'])

    # Show confirmation
    display.draw_text(f"Paired: {speaker['name']}", (20, 280), font_size=14)
    display.update()
    time.sleep(2)
```

#### 5. `device_config.json` - Configuration File

**New fields**:
```json
{
  "location": {
    "latitude": 35.7796,
    "longitude": -78.6382,
    "timezone": "America/New_York"
  },
  "bluetooth_speaker": {
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "name": "JBL Flip 5",
    "paired_at": 1770907822
  },
  "spotify_oauth": {
    "access_token": "BQA...",
    "refresh_token": "AQB...",
    "expires_at": 1770911422
  },
  "crew": {...}
}
```

---

## Installation & Dependencies

### System Packages

```bash
# Bluetooth audio support
sudo apt-get update
sudo apt-get install bluetooth bluez pulseaudio pulseaudio-module-bluetooth

# Spotify playback (librespot)
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh

# Touch input (likely already installed)
sudo apt-get install python3-evdev

# QR code generation
sudo apt-get install python3-qrcode
```

### Python Dependencies

```bash
# Touch input
pip3 install evdev

# QR codes for OAuth
pip3 install qrcode[pil]

# D-Bus for librespot control
pip3 install dbus-python

# No new packages needed for Bluetooth - use subprocess
```

### PulseAudio Configuration

**Enable Bluetooth A2DP**:
```bash
# Edit /etc/pulse/default.pa
sudo nano /etc/pulse/default.pa

# Add these lines:
load-module module-bluetooth-policy
load-module module-bluetooth-discover

# Restart PulseAudio
pulseaudio -k
pulseaudio --start
```

### Service Configuration

**New systemd service**: `leeloo-playback.service`
```ini
[Unit]
Description=LEELOO Playback Manager
After=bluetooth.service pulseaudio.service network.target

[Service]
Type=simple
User=pi
Environment="PULSE_SERVER=/run/user/1000/pulse/native"
ExecStart=/usr/bin/python3 /home/pi/leeloo-ui/playback_manager.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Install service**:
```bash
sudo cp leeloo-playback.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable leeloo-playback
sudo systemctl start leeloo-playback
```

---

## Critical Files Summary

### New Files (7 total):
1. `/home/pi/leeloo-ui/touch_handler.py` - Touch event monitoring (evdev)
2. `/home/pi/leeloo-ui/playback_manager.py` - Spotify playback control (librespot)
3. `/home/pi/leeloo-ui/bluetooth_audio.py` - Bluetooth speaker pairing (bluetoothctl)
4. `/home/pi/leeloo-ui/playlist_manager.py` - Playlist persistence (JSON)
5. `/home/pi/leeloo-ui/spotify_oauth.py` - OAuth authentication flow
6. `/home/pi/leeloo-ui/playlist.json` - Track queue (auto-created)
7. `/etc/systemd/system/leeloo-playback.service` - Service definition

### Modified Files (5 total):
1. `/home/pi/leeloo-ui/gadget_main.py` - Add touch handler + playback manager initialization
2. `/home/pi/leeloo-ui/gadget_display.py` - Add "♪" icon rendering when playing
3. `/home/pi/leeloo-ui/leeloo_client.py` - Add tracks to playlist on WebSocket push
4. `/home/pi/leeloo-ui/leeloo_boot.py` - Add Spotify OAuth + Bluetooth pairing screens
5. `/home/pi/leeloo-ui/device_config.json` - Store Bluetooth speaker + OAuth tokens

---

## User Decisions (Confirmed)

### 1. Spotify Authentication: **User OAuth Flow**
- User signs in with their own Spotify account during first boot
- QR code displayed on screen for phone-based authorization
- OAuth tokens saved to `device_config.json`
- Enables personal playlists and recommendations

### 2. Touchscreen: **Confirmed Working**
- Waveshare 3.5" LCD has touch capability
- Use evdev to detect taps on album art region (218-478, 4-322)
- Touch calibration may be needed (test on actual hardware)

### 3. Visual Feedback: **Small ♪ Icon**
- Minimal change: white music note appears at (470, 10) when playing
- Icon disappears when track ends
- No animation or layout changes - UI stays exactly the same

### 4. Speaker Pairing: **Touch Selection**
- During first boot, display list of discovered Bluetooth speakers
- User taps speaker name on touchscreen to select
- Pair automatically and save to `device_config.json`

### 5. Playlist Behavior: **Unlimited FIFO Queue**
- All shared tracks stay queued (no expiration)
- Play in chronological order (first shared = first played)
- Persist across reboots via `playlist.json`
- No manual removal for MVP (can add later)

---

## Verification & Testing

### End-to-End Test Flow:

#### 1. First Boot Test
```bash
# Boot fresh LEELOO (reset device_config.json)
sudo rm /home/pi/leeloo-ui/.first_run_complete
sudo reboot

# Expected flow:
✓ Splash screen appears
✓ WiFi captive portal (if not connected)
✓ Spotify OAuth QR code screen
  - Scan QR with phone
  - Authorize LEELOO app
  - Tokens saved
✓ Bluetooth pairing screen
  - List of speakers appears
  - Tap "JBL Flip 5"
  - "Paired: JBL Flip 5" confirmation
✓ Crew setup (optional)
✓ Main UI appears
```

#### 2. Music Sharing Test
```bash
# Send "Regulate" via relay server or test script
python3 test_spotify_display.py "https://open.spotify.com/track/7nYvUtkQMx1v80S2FH2s9J"

# Expected results:
✓ Album art + scancode appear on display
✓ current_music.json updated with track data
✓ playlist.json shows track in queue (playback_state: "queued")
✓ Touch region is active on album frame
```

#### 3. Touch Playback Test
```bash
# Tap anywhere on album art region (right side of screen)

# Expected results:
✓ Touch event detected by touch_handler.py
✓ playback_manager.play_current_track() called
✓ librespot launches with OAuth tokens
✓ Audio streams to Bluetooth speaker (hear "Regulate" playing)
✓ current_music.json updated: playback_state: "playing"
✓ "♪" icon appears at (470, 10) on display
```

#### 4. Playlist Auto-Play Test
```bash
# Queue 3 tracks:
python3 test_spotify_display.py "spotify:track:7nYvUtkQMx1v80S2FH2s9J"  # Regulate
python3 test_spotify_display.py "spotify:track:4LIpeIN0AxFMRhnm5tR0HJ"  # Si Te Pica
python3 test_spotify_display.py "spotify:track:1z6WtY7X4HQJvzxC4UgkSf"  # Santeria

# Tap first track to start playback
# Wait ~3 minutes for "Regulate" to finish

# Expected results:
✓ "Si Te Pica" auto-plays when "Regulate" ends
✓ Display updates to show new album art
✓ playlist.json marks "Regulate" as played: true
✓ "♪" icon remains (still playing)
✓ After "Si Te Pica" ends, "Santeria" auto-plays
```

#### 5. Bluetooth Reconnection Test
```bash
# Power off Bluetooth speaker
# Wait 10 seconds
# Power on speaker

# Expected results:
✓ Pi auto-reconnects to speaker (bluetoothctl connect)
✓ Audio output resumes through speaker
✓ Playback continues without interruption
```

#### 6. OAuth Token Refresh Test
```bash
# Wait for access token to expire (typically 1 hour)

# Expected results:
✓ playback_manager detects expired token
✓ Calls spotify_oauth.refresh_access_token()
✓ New access token saved to device_config.json
✓ Playback continues seamlessly
```

---

## Implementation Phases (If We Proceed)

### Phase 1: Touch Input (1-2 hours)
- Install evdev library
- Detect touchscreen device path (`/dev/input/event*`)
- Implement `TouchHandler` class
- Test tap detection with debug prints on actual Pi hardware
- Calibrate touch coordinates if needed

### Phase 2: Bluetooth Audio (1-2 hours)
- Enable Bluetooth on Pi (`bluetoothctl power on`)
- Implement `BluetoothAudioManager` class
- Test speaker scanning and pairing
- Configure PulseAudio for A2DP output
- Test audio playback through speaker (e.g., `aplay test.wav`)

### Phase 3: Spotify OAuth (2-3 hours)
- Register LEELOO app at developer.spotify.com
- Implement `SpotifyOAuth` class
- Add OAuth screen to `leeloo_boot.py`
- Test QR code generation and authorization flow
- Verify token storage and refresh logic

### Phase 4: Spotify Playback (2-3 hours)
- Install librespot on Pi
- Implement `PlaybackManager` class
- Test manual track playback via command-line
- Integrate D-Bus for track-end detection
- Test auto-advance to next track

### Phase 5: Touch → Playback Integration (2-3 hours)
- Connect `TouchHandler` to `PlaybackManager` in `gadget_main.py`
- Implement playlist auto-play logic
- Add "♪" icon rendering in `gadget_display.py`
- Test end-to-end: tap → play → auto-advance
- Debug any timing or state issues

### Phase 6: Boot Flow Polish (1-2 hours)
- Add Spotify OAuth screen to first boot
- Add Bluetooth pairing screen to first boot
- Test complete first-boot experience on fresh Pi
- Polish UI messaging and error handling

**Total Estimated Time**: 9-15 hours (with testing and debugging on actual hardware)

---

## Risks & Considerations

### 1. Touchscreen Hardware
- **Risk**: Waveshare touch controller may need calibration
- **Mitigation**: Test on actual hardware, adjust coordinates if needed
- **Fallback**: Add GPIO button as alternative input method

### 2. Spotify Account Requirements
- **Risk**: Librespot requires Spotify account (even free tier)
- **Mitigation**: OAuth flow allows user to sign in with existing account
- **Consideration**: Spotify may rate-limit device registrations

### 3. Bluetooth Audio Reliability
- **Risk**: Bluetooth streaming can be finicky (dropouts, reconnection issues)
- **Mitigation**: Implement robust reconnection logic, monitor PulseAudio state
- **Consideration**: Range limited to ~10 meters, walls can interfere

### 4. CPU Load on Raspberry Pi
- **Risk**: Spotify decoding + Bluetooth + display rendering may strain Pi Zero
- **Mitigation**: Use librespot (lightweight), optimize display refresh rate
- **Testing**: Monitor CPU usage during playback (`htop`)

### 5. Touch → Audio Playback Latency
- **Risk**: 2-5 second delay between tap and audio (librespot startup time)
- **Mitigation**: Pre-load librespot in background, keep process alive
- **UX**: Add visual feedback ("Loading...") during startup

### 6. Network Dependency
- **Risk**: Spotify streaming requires WiFi connection
- **Mitigation**: Display "No Internet" message, cache next track during playback
- **Consideration**: Offline mode not supported by Spotify Connect

### 7. OAuth Token Management
- **Risk**: Access tokens expire after 1 hour
- **Mitigation**: Implement automatic token refresh using refresh_token
- **Testing**: Verify refresh works correctly before expiration

---

## Future Enhancements (Post-MVP)

### 1. Advanced Playlist Controls
- Skip forward/backward (double-tap left/right sides?)
- Remove tracks from queue
- Reorder playlist
- Shuffle mode

### 2. Volume Control
- Touch slider on left side of screen
- OR use GPIO rotary encoder
- Adjust PulseAudio sink volume

### 3. Now Playing Expanded View
- Long-press album art → frame expands to full height
- Show playback progress bar
- Display lyrics (via Spotify API or Genius)
- Show queue preview

### 4. Multiple Playlists
- "Chill", "Workout", "Party" playlists
- Switch between playlists via touch
- Collaborative editing via relay server

### 5. Smart Features
- Auto-play based on time of day
- Skip explicit tracks during certain hours
- Fade out/in between tracks
- Crossfade support

### 6. External Speaker Support
- Support multiple paired speakers
- Switch output device via touch menu
- Stereo pairing (left/right channels)

### 7. Offline Playback
- Cache downloaded tracks locally
- Play from cache when offline
- Sync when WiFi reconnects

---

## Summary

This plan transforms LEELOO from a passive music display into a **fully interactive collaborative music player**:

1. **First boot** → WiFi setup → Spotify login (QR code) → Bluetooth speaker pairing (touch selection)
2. **Friend shares track** → Appears on display with scancode + album art → Queued in playlist
3. **User taps album art** → Plays through Bluetooth speaker → Small "♪" icon appears
4. **Tracks auto-advance** → Playlist plays sequentially → Display updates with new album art

**Key technical choices**:
- **Touch**: evdev for input detection (native Pi support)
- **Spotify**: librespot with user OAuth (personal accounts)
- **Bluetooth**: PulseAudio + A2DP profile (standard Linux audio)
- **Playlist**: JSON file persistence (FIFO queue)
- **UI**: Minimal changes (just "♪" icon when playing)

**Estimated implementation time**: 9-15 hours across 6 phases

**Hardware requirements**:
- Raspberry Pi with Waveshare 3.5" touchscreen LCD
- Bluetooth speaker (A2DP-compatible)
- WiFi connection for Spotify streaming

The system leverages existing infrastructure (WebSocket relay, frame expansion, config persistence) and adds ~7 new Python modules for touch/audio/playback integration.
