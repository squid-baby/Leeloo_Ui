# Spotify Scancode Display Test

Successfully implemented Spotify scancode display on LEELOO!

## What Works

✅ Downloads Spotify scancodes from Spotify's public API
✅ Creates square display images with scancode overlay
✅ Renders on LEELOO display with proper layout
✅ Supports any Spotify track URL or URI

## Files Created

- `test_spotify_display.py` - Main test script
- `leeloo_spotify.py` - Spotify integration utilities (already existed)
- `leeloo_config.py` - Updated to support local testing

## Quick Test on Pi

```bash
# SSH into your Pi
ssh pi@gadget.local

# Navigate to project
cd /home/pi/leeloo-ui  # or wherever your project is

# Make sure dependencies are installed
pip3 install -r requirements.txt

# Run the test with any Spotify track!
python3 test_spotify_display.py "https://open.spotify.com/track/TRACK_ID"

# Or use the default test track
python3 test_spotify_display.py
```

## Example URLs to Try

```bash
# Song 2 - Blur (default)
python3 test_spotify_display.py "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"

# Mr. Brightside - The Killers
python3 test_spotify_display.py "https://open.spotify.com/track/003vvx7Niy0yvhvHt4a68B"

# Bohemian Rhapsody - Queen
python3 test_spotify_display.py "https://open.spotify.com/track/7tFiyTwD0nx5a1eklYtX2J"
```

## How It Works

1. **Parse URI** - Converts Spotify URLs to URIs
2. **Download Scancode** - Fetches scancode from `scannables.scdn.co`
3. **Fetch Metadata** - Tries to get track info (requires auth, falls back to placeholder)
4. **Create Display Image** - Makes a 304x304 square with scancode at bottom
5. **Render** - Shows on the LEELOO display

## Display Layout

```
┌─────────────────┬─────────────┐
│  Weather        │             │
│  Time           │  [Square    │
│  Messages       │   with      │
│  Album Info     │   Spotify   │
│                 │   Scancode] │
└─────────────────┴─────────────┘
```

The scancode appears at the bottom center of the right panel.

## Next Steps

To integrate this into the main LEELOO system:

1. **Add to `leeloo_client.py`** - Handle incoming track shares
2. **Download album art** - Use Spotify API with OAuth for full metadata
3. **Cache scancodes** - Store downloaded scancodes locally
4. **Add to UI manager** - Integrate with the frame expansion animations

## Notes

- Spotify API requires authentication for full metadata (artist, track name, album art)
- For now, the script uses placeholder data if auth fails
- Scancodes work without auth (public endpoint)
- Album art can be added later with Spotify credentials

## Cached Files

Files are saved to `album_art/` directory:
- `scancode_TRACKID.png` - Downloaded scancode
- `spotify_display.jpg` - Final square display image
- `album_TRACKID.jpg` - Album art (if available)
