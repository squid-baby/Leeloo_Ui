# Gadget Display UI

A retro terminal-style music sharing device UI for Raspberry Pi with a Waveshare 3.5" LCD display.

---

## Current Version: React/TypeScript (Active)

The interactive React version with expandable boxes, animations, and persistent state.

### Quick Start

```bash
cd Retro-Music-Panel
npm install
npm run dev
# Open http://localhost:3000
```

### Display Layout (200x400px)

```
┌─────────────────────┬─────────────────┐
│  ┌─────────────────┐│                 │
│  │ weather         ││                 │
│  │ 72°F  ☀9  🌧1   ││                 │
│  └─────────────────┘│                 │
│  ┌─────────────────┐│   Album Art     │
│  │ time            ││   (200x200px)   │
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
       200px                200px
```

- **Scale**: 1.37x for desktop preview, native on Pi
- **Rendering**: `imageRendering: pixelated` for retro aesthetic

### Expandable Boxes

Each box can be tapped to expand with unique interactive content:

#### Weather (tan border)
- **Collapsed**: Temperature, sun index (0-10), rain index (0-10) with dot sliders
- **Expanded**:
  - Typewriter weather report ("Today in Brooklyn: 72°F, bright and sunny...")
  - ASCII weather animation (sunny/cloudy/rainy) cycling every 500ms
  ```
  Sunny:       Cloudy:      Rainy:
     \  |  /     .-~~~-.     .-~~~-.
      \ | /     (       )   (       )
    ---( )---    `-----'     ' ' ' '
      / | \                   ' ' '
  ```

#### Time (purple border)
- **Collapsed**: Current time, date, seconds progress slider
- **Expanded**: "Life in Weeks" visualization
  - **First visit**: Asks for birthday (saved to localStorage)
  - **Return visits**: Shows life grid immediately
  - **Grid**: 26 columns × 40 rows (each cell = 2 weeks, 80-year lifespan)
  - **Visual**: ■ = weeks lived (purple), □ = future weeks (faded)
  - **Stats**: "1,456 weeks · 28 years"

#### Messages (lavender border)
- **Collapsed**: Contact names (Amy, Ben)
- **Expanded**:
  - Sender name in bold
  - Full message with typewriter effect
  - Cursor animation (|) during typing

#### Album (green border)
- **Collapsed**: Artist name, track title, BPM, "pushed by [friend]"
- **Expanded** (two-phase animation):
  1. **Phase 1** (0-3 seconds): Genre tags displayed full-screen centered
     - Electronica, Nu-Disco, Synthwave, Indietronica
  2. **Phase 2** (3+ seconds): Genres fade out, artist details fade in
     - Artist name (12px bold)
     - Location (London, UK)
     - Bio with typewriter effect
     - Similar artists
     - Auto-scrolls as content fills the box

### Animation Timings

| Effect | Duration |
|--------|----------|
| Box expansion/collapse | 720ms ease |
| Typewriter text | 100ms per character |
| Weather ASCII frames | 500ms cycle |
| Genre display phase | 3 seconds |
| Fade transitions | 500ms |
| Auto-collapse | 30 seconds after typing completes |

### Color Palette

| Color | CSS Class | Usage |
|-------|-----------|-------|
| Navy | `gadget-navy` | Background |
| Green | `gadget-green` | Album box border |
| Purple | `gadget-purple` | Time box border |
| Lavender | `gadget-lavender` | Messages box border |
| Tan | `gadget-tan` | Weather box border |
| Rose | `gadget-rose` | "pushed by" accent |

### Tech Stack

- React 18 + TypeScript
- Vite dev server
- Tailwind CSS
- localStorage for birthday persistence

### File Structure

```
Retro-Music-Panel/
├── client/
│   ├── src/
│   │   ├── components/
│   │   │   └── Gadget.tsx       # Main UI component
│   │   └── lib/
│   │       └── utils.ts         # Utility functions
│   └── public/
│       └── doorways-album.jpg   # Album artwork
├── package.json
├── tailwind.config.js
└── vite.config.ts
```

### State Management

```typescript
// Expansion state (only one box can expand at a time)
type ExpandedBox = 'weather' | 'time' | 'messages' | 'album' | null;

// Persisted in localStorage
'gadget-birthday' → ISO date string
```

---

## Legacy Version: Python/Pillow (Deprecated)

> ⚠️ **This version is no longer actively developed.** Use the React version above.

The original static image renderer using Python and Pillow.

### Quick Start (Preview on Your Computer)

#### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 2. Run the Demo
```bash
python gadget_display.py
```

This will generate a preview image and attempt to open it. If it doesn't open automatically, check `/tmp/gadget_preview.png`

### Display Layout

```
┌────────────────────┬──────────────────────────────┐
│                    │                              │
│  Weather (temp,    │     Album Art                │
│  sun, rain)        │     (240x240px)              │
│                    │                              │
│  Time & Date       │                              │
│                    │                              │
│  Messages          │                              │
│  (3 previews)      │                              │
│                    │                              │
│  Album Info        │     Spotify Scan Code        │
│  (BPM, duration,   │     (orange bar)             │
│   artist, pushed)  │                              │
│                    │                              │
└────────────────────┴──────────────────────────────┘
   160px wide              320px wide
```

### Customizing the Display

#### Changing Data

Edit the `demo()` function in `gadget_display.py`:

```python
weather_data = {
    'temp': 72,      # Temperature (0-100°F)
    'sun': 60,       # Sun level (0=cloudy, 100=sunny)
    'rain': 0,       # Rain level (0=none, 100=downpour)
}

time_data = {
    'time_str': '2:47 PM',
    'hour': 2,       # Hour for slider (0-12)
    'date_str': 'Feb 4',
}

messages = [
    {'name': 'Amy', 'preview': 'Dinner tonight?'},
    {'name': 'Sarah', 'preview': 'See you later!'},
    # Add more messages...
]

album_data = {
    'bpm': 120,
    'duration': '2:42 s',
    'artist_1': 'Cinnamon',
    'artist_2': 'Chasers',
    'track': 'Doorways',
    'pushed_by': 'Amy',
    'current_time': '1:30',
    'current_seconds': 90,
    'total_seconds': 162,
    'plays': 73,
}
```

#### Changing Colors

Colors are defined at the top of `gadget_display.py`:

```python
COLORS = {
    'bg': '#1a1d2e',           # Background
    'green': '#719253',         # Album box
    'purple': '#9c93dd',        # Time box
    'pink': '#d6697f',          # "pushed by" text
    'tan': '#c2995e',           # Weather box
    'border_gray': '#a7afd4',   # Borders
    'white': '#ffffff',
    'orange': '#ff8800',        # Spotify code
}
```

### Deploying to Raspberry Pi

#### 1. On Your Pi, Install Waveshare Libraries

```bash
# Install BCM2835 library
wget http://www.airspayce.com/mikem/bcm2835/bcm2835-1.71.tar.gz
tar zxvf bcm2835-1.71.tar.gz
cd bcm2835-1.71/
sudo ./configure && sudo make && sudo make check && sudo make install

# Install Python libraries
pip install pillow spidev RPi.GPIO
```

#### 2. Uncomment Hardware Dependencies

In `requirements.txt`:
```
spidev>=3.5
RPi.GPIO>=0.7.1
```

#### 3. Add Waveshare Display Driver

Download the Waveshare 3.5" LCD driver from:
https://www.waveshare.com/wiki/3.5inch_RPi_LCD_(G)

Follow their setup guide to enable the display.

#### 4. Modify Display Code

In `gadget_display.py`, uncomment and configure the hardware display section:

```python
# Import Waveshare driver
from waveshare_lcd import LCD_3inch5

# In __init__:
self.display_device = LCD_3inch5.LCD_3inch5()
self.display_device.Init()

# In show():
self.display_device.ShowImage(self.image)
```

### Troubleshooting

**Problem:** Fonts not found
**Solution:** The code will fall back to default font. For better results, install DejaVu fonts:
```bash
# Ubuntu/Debian
sudo apt-get install fonts-dejavu

# macOS
brew install font-dejavu
```

**Problem:** Image doesn't open
**Solution:** Check `/tmp/gadget_preview.png` manually

**Problem:** Display looks wrong on Pi
**Solution:** Verify display resolution matches (480x320). Adjust in GadgetDisplay init if needed.

---

## Questions?

Refer back to the design decisions document (GADGET_DESIGN_DECISIONS.md) for the full spec.
