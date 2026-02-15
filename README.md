# LEELOO Display UI

A retro terminal-style music sharing device UI for Raspberry Pi with a Waveshare 3.5" LCD display.

---

## Current Version: Python/Pillow + Frame Animations (Active)

The production Python version running on Raspberry Pi with smooth frame expansion animations.

### Quick Start (Raspberry Pi)

```bash
# Copy files to Pi
sshpass -p 'gadget' scp *.py pi@leeloo.local:/home/pi/leeloo-ui/

# SSH into Pi
sshpass -p 'gadget' ssh pi@leeloo.local

# The brain runs as a systemd service
sudo systemctl status leeloo.service

# Or run manually
sudo python3 leeloo_brain.py
```

### Display Layout (480x320px)

```
┌─────────────────────┬─────────────────┐
│  ┌─────────────────┐│                 │
│  │ weather         ││                 │
│  │ 72°F  ☀9  🌧1   ││                 │
│  └─────────────────┘│                 │
│  ┌─────────────────┐│   Album Art     │
│  │ time            ││   (240x240px)   │
│  │ 2:47 PM · Feb 4 ││                 │
│  └─────────────────┘│                 │
│  ┌─────────────────┐│                 │
│  │ messages        ││                 │
│  │ Amy, Ben        ││                 │
│  └─────────────────┘│                 │
│  ┌─────────────────┐│                 │
│  │ album           ││                 │
│  │ Cinnamon Chasers││                 │
│  │ "Doorways"      ││                 │
│  │ pushed by Amy   ││                 │
│  └─────────────────┘│                 │
└─────────────────────┴─────────────────┘
       ~215px                ~265px
```

### Frame Expansion Animations

Each info panel can expand to full height with smooth animations:

- **Weather**: Expands to show detailed forecast with typewriter effect
- **Time**: Expands to show calendar/schedule
- **Messages**: Expands to show full message threads
- **Album**: Expands to show track details and artist info

#### Animation System

The frame animator uses:
- **Row-by-row file I/O** to avoid screen tearing (critical fix!)
- **24fps** pre-processed frames with cubic ease-in-out
- **1.5 second** expand/collapse duration
- **Typewriter effect** for content reveal

### Architecture

```
leeloo_brain.py         # Asyncio orchestrator — MAIN ENTRY POINT
├── UIState enum        # NORMAL, EXPANDING, EXPANDED, COLLAPSING, LISTENING, PROCESSING
├── LeelooBrain         # Coordinates all subsystems
│   ├── _voice_interaction()   # tap → record → transcribe → classify → act
│   ├── expand_frame()         # animate expand → typewriter → scroll → collapse
│   ├── _scroll_content()      # smooth scroll for overflowed text
│   └── _search_and_push_song() # Spotify search + album art + push to crew
├── Subsystems:
│   ├── TapManager       (leeloo_tap.py)     # ADXL345 accelerometer tap detection
│   ├── VoiceManager     (leeloo_voice.py)   # INMP441 mic → Deepgram Nova-2 STT
│   ├── IntentRouter     (leeloo_intent.py)  # Claude 3.5 Haiku intent classification
│   ├── LEDManager       (leeloo_led.py)     # WS2812B LED animations
│   ├── MessageManager   (leeloo_messages.py) # Message storage + unread counts
│   └── LeelooClient     (leeloo_client.py)  # WebSocket relay for crew comms
└── Display:
    ├── gadget_display.py       # LeelooDisplay class (renders PIL images)
    ├── display/frame_animator.py # Frame expand/collapse animations
    └── leeloo_album_art.py     # Centralized album art (243x304)
```

### Voice Interaction Pipeline

```
tap device → LED green → mic record → Deepgram STT → LED pulse →
Claude Haiku classifies → execute action:
  WEATHER_EXPAND  → show detailed weather
  ALBUM_INFO      → tell me about this band
  MESSAGE_SEND    → send a message to crew
  MESSAGE_READOUT → what did I miss
  SONG_PUSH       → share a song (Spotify search)
  NUDGE           → send a nudge to all devices
  HANG_PROPOSE    → let's hang Saturday at 3pm
```

### Key Technical Fixes

#### Screen Tearing Solution

The Waveshare 3.5" LCD (ILI9486) has no vsync. We fixed tearing by:

1. **`fbcon=map:0`** in `/boot/firmware/cmdline.txt` - Stops Linux console from fighting for fb1
2. **Row-by-row file I/O** instead of numpy memmap - Matches working GIF demo approach
3. **Single process architecture** - UI manager owns the framebuffer exclusively

#### Critical Files Changed

```bash
# /boot/firmware/cmdline.txt - Changed fbcon=map:01 to fbcon=map:0
# This keeps the console on fb0 (HDMI) and leaves fb1 (LCD) for our exclusive use
```

### Color Palette

| Color | Hex | Usage |
|-------|-----|-------|
| Navy | `#1A1D2E` | Background |
| Green | `#719253` | Album box border |
| Purple | `#9C93DD` | Time box border |
| Lavender | `#A7AFD4` | Messages box border |
| Tan | `#C2995E` | Weather box border |
| Rose | `#D6697F` | "pushed by" accent |
| White | `#FFFFFF` | Text |

### Animation Timings

| Effect | Duration |
|--------|----------|
| Frame expand/collapse | 1.5s (36 frames @ 24fps) |
| Typewriter text | 30ms per character |
| Line pause | 100ms between lines |
| Content display | 10 seconds default |

### Running Demos

```bash
# Stop the main service first
sudo systemctl stop gadget.service

# Run the weather expansion demo
python3 demo_weather_typewriter.py

# Run the UI manager demo
python3 leeloo_ui_manager.py

# Run the reaction GIF demo
python3 demo_message_expand.py

# Re-enable the main service
sudo systemctl start gadget.service
```

### Spotify Integration

Share music with scannable Spotify codes! The display supports:
- **Spotify scancodes** - Downloadable QR codes from `scannables.scdn.co`
- **Album art** - Scraped from Spotify web pages (no OAuth required)
- **Track metadata** - Artist, album, track name, monthly listeners
- **Persistent display** - Saves to `current_music.json` for main loop

#### Sharing Music

```bash
# Share a track by URL
python3 test_spotify_display.py "https://open.spotify.com/track/4LIpeIN0AxFMRhnm5tR0HJ"

# Natural language (requires Spotify API auth)
python3 test_spotify_display.py "share sabotage by beastie boys"
```

The display will show:
- Album art (243x244px) filling the space above the scancode
- Spotify scancode (243x60px) pinned to the bottom
- Album info box with artist, album, track, listeners (210px-wide text box)

#### Technical Details

- **Display dimensions**: 243x304px (4:5 aspect ratio, matches Spotify scancode placeholder)
- **Text constraint**: 210px-wide centered text box with truncation
- **Scraping**: Uses `og:image` and `og:title` meta tags from Spotify pages
- **No authentication**: Works without Spotify OAuth (but limited to URL-based sharing)

See `SPOTIFY_SCANCODE_TEST.md` for full testing guide.

### File Structure

```
TipTop UI/
├── leeloo_brain.py          # Main entry point — asyncio orchestrator
├── leeloo_tap.py            # ADXL345 tap detection (single/double/triple)
├── leeloo_voice.py          # INMP441 mic → Deepgram Nova-2 STT
├── leeloo_intent.py         # Claude 3.5 Haiku intent classification
├── leeloo_led.py            # WS2812B LED animations (3 LEDs)
├── leeloo_messages.py       # Message storage + unread counts
├── leeloo_client.py         # WebSocket relay client
├── leeloo_music_manager.py  # Spotify currently playing + listeners
├── leeloo_album_art.py      # Centralized album art (243x304)
├── leeloo_spotify.py        # Spotify scancode generation
├── gadget_main.py           # Legacy main loop (replaced by brain)
├── gadget_display.py        # LeelooDisplay renderer
├── gadget_weather.py        # Open-Meteo weather API
├── text_scroller.py         # Text truncation utilities
├── display/
│   ├── __init__.py
│   ├── frame_animator.py    # Expand/collapse animations
│   └── fast_fb.py           # Fast framebuffer (numpy)
├── album_art/               # Downloaded scancodes & album art
├── lessons_*.md             # Debugging lessons learned
└── README.md
```

---

## React Version (Development Preview)

The React version is used for desktop prototyping only.

### Quick Start

```bash
cd Retro-Music-Panel
npm install
npm run dev
# Open http://localhost:3000
```

---

## Deployment to Raspberry Pi

### 1. Configure Display Driver

The Waveshare 3.5" LCD uses the `fb_ili9486` driver via `dtoverlay=waveshare35a`.

### 2. Fix Console Mapping

Edit `/boot/firmware/cmdline.txt` and change:
```
fbcon=map:01  →  fbcon=map:0
```

This prevents the Linux console from writing to the LCD (fb1).

### 3. Install as Service

```bash
sudo systemctl enable leeloo.service
sudo systemctl start leeloo.service

# View logs
sudo journalctl -u leeloo.service -f
```

### 4. Troubleshooting

**Screen tearing during animations?**
- Make sure `fbcon=map:0` is set
- Stop any other processes writing to fb1
- Use `sudo systemctl stop gadget.service` before running demos

**Boot screen showing through UI?**
- This is fbcon fighting for the display
- The `fbcon=map:0` fix should resolve this

**Animation too slow?**
- Pre-processing should complete in ~280ms
- Playback should hit 24fps
- Check for other CPU-intensive processes

---

## Questions?

Refer to the design decisions document for the full spec.
