#!/usr/bin/env python3
"""
Test Spotify Scancode Display
Downloads a Spotify track's scancode and album art, then displays on LEELOO screen.

Usage:
    python3 test_spotify_display.py "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"
    python3 test_spotify_display.py "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
    python3 test_spotify_display.py "share sabotage by beastie boys"
    python3 test_spotify_display.py "play mr brightside by the killers"
"""

import sys
import os
import io
import time
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ requests library not installed. Run: pip install requests")
    sys.exit(1)

from PIL import Image
from leeloo_spotify import parse_spotify_uri, download_scancode, get_track_info
from leeloo_config import Config
from gadget_display import LeelooDisplay
from music_request_parser import parse_music_request

# Spotify public API endpoints (no auth needed for basic track info)
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def search_spotify(query):
    """
    Search Spotify for a track.

    Args:
        query: Search query (e.g., "sabotage beastie boys")

    Returns:
        Spotify track URI or None
    """
    url = f"{SPOTIFY_API_BASE}/search"
    params = {
        'q': query,
        'type': 'track',
        'limit': 1
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 401:
            print("⚠️  Spotify search requires authentication")
            return None

        response.raise_for_status()
        data = response.json()

        if data['tracks']['items']:
            track = data['tracks']['items'][0]
            track_uri = track['uri']
            print(f"✅ Found: {track['name']} by {track['artists'][0]['name']}")
            return track_uri
        else:
            print(f"❌ No results found for: {query}")
            return None

    except requests.RequestException as e:
        print(f"⚠️  Search failed: {e}")
        return None


def get_spotify_track_data(track_id):
    """
    Fetch track data from Spotify Web API (public, no auth).

    Note: This uses Spotify's public endpoints which have rate limits.
    For production, we'd need OAuth credentials.

    Args:
        track_id: Spotify track ID (not full URI)

    Returns:
        dict with artist, track, album, album_art_url or None
    """
    url = f"{SPOTIFY_API_BASE}/tracks/{track_id}"

    try:
        # Try without auth first (works for public tracks)
        response = requests.get(url, timeout=10)

        if response.status_code == 401:
            print("⚠️  Spotify API requires authentication for this track")
            print("    Using placeholder data instead")
            return None

        response.raise_for_status()
        data = response.json()

        # Extract what we need
        return {
            'artist': data['artists'][0]['name'] if data.get('artists') else 'Unknown Artist',
            'track': data['name'],
            'album': data['album']['name'],
            'album_art_url': data['album']['images'][0]['url'] if data['album'].get('images') else None,
        }

    except requests.RequestException as e:
        print(f"⚠️  Could not fetch track data from Spotify API: {e}")
        return None


def download_album_art(url, save_path):
    """
    Download album art from URL.

    Args:
        url: Album art URL
        save_path: Path to save image

    Returns:
        True if successful, False otherwise
    """
    if not url:
        return False

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        img = Image.open(io.BytesIO(response.content))

        # Ensure directory exists
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        # Save as JPEG (smaller file size)
        img.convert('RGB').save(save_path, 'JPEG', quality=95)
        print(f"✅ Downloaded album art to {save_path}")
        return True

    except Exception as e:
        print(f"❌ Failed to download album art: {e}")
        return False


def create_square_scancode_image(scancode_path, album_art_path=None, width=243, height=304):
    """
    Create a LEELOO sharing image with scancode (and optional album art background).

    Uses the same 4:5 aspect ratio as the empty scancode placeholder (800x1000).
    When displayed on screen, fits perfectly in the 304px tall frame.

    Args:
        scancode_path: Path to scancode PNG
        album_art_path: Optional path to album art (used as background)
        width: Output width in pixels (default 243 - matches empty scancode ratio)
        height: Output height in pixels (default 304 - frame height)

    Returns:
        Path to sharing image or None
    """
    try:
        # Load scancode and calculate its final size
        scancode = Image.open(scancode_path).convert('RGBA')

        # Calculate scancode size (full width, maintain aspect ratio)
        scancode_width = width  # Use full width (243px)
        scancode_aspect = scancode.size[0] / scancode.size[1]
        scancode_height = int(scancode_width / scancode_aspect)

        # Resize scancode
        scancode = scancode.resize((scancode_width, scancode_height), Image.Resampling.LANCZOS)

        # Calculate album art height (space above scancode)
        album_art_height = height - scancode_height  # e.g., 304 - 61 = 243px

        # Create base image with album art in the top section
        base = Image.new('RGB', (width, height), color='#1A1D2E')

        if album_art_path and os.path.exists(album_art_path):
            # Load and resize album art to fit the space above scancode
            album_art = Image.open(album_art_path).convert('RGB')
            album_art = album_art.resize((width, album_art_height), Image.Resampling.LANCZOS)
            # Paste album art at the top
            base.paste(album_art, (0, 0))
            print(f"✅ Using album art as background ({width}x{album_art_height})")

        # Position scancode at bottom (pinned to bottom edge)
        scancode_x = 0  # Left edge
        scancode_y = height - scancode_height  # Pinned to bottom

        # Paste scancode onto base
        base.paste(scancode, (scancode_x, scancode_y), scancode if scancode.mode == 'RGBA' else None)

        # Save result
        output_path = os.path.join(Config.ALBUM_ART_DIR, "spotify_display.jpg")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        base.save(output_path, 'JPEG', quality=95)
        print(f"✅ Created square scancode image at {output_path}")

        return output_path

    except Exception as e:
        print(f"❌ Failed to create square scancode image: {e}")
        return None


def test_spotify_display(spotify_input):
    """
    Main test function: fetch and display Spotify track.

    Args:
        spotify_input: Spotify URI, URL, or natural language request
    """
    print(f"\n🎵 Testing Spotify Display")
    print(f"   Input: {spotify_input}\n")

    # Try to parse as Spotify URI/URL first
    spotify_uri = parse_spotify_uri(spotify_input)

    # If not a URI/URL, try parsing as natural language
    if not spotify_uri:
        print("🔍 Interpreting as natural language request...")
        parsed = parse_music_request(spotify_input)
        print(f"   Track: {parsed['track']}")
        print(f"   Artist: {parsed['artist']}")
        print(f"   Searching Spotify...\n")

        spotify_uri = search_spotify(parsed['query'])

        if not spotify_uri:
            print(f"❌ Could not find track on Spotify")
            print(f"   Try using a direct Spotify URL instead")
            return

    print(f"✅ Parsed URI: {spotify_uri}")

    # Extract track ID
    if not spotify_uri.startswith('spotify:track:'):
        print(f"❌ Only track URIs are supported (not album/playlist)")
        return

    track_id = spotify_uri.split(':')[-1]
    print(f"   Track ID: {track_id}\n")

    # Ensure directories exist
    Config.ensure_directories()

    # Download scancode
    print("📡 Downloading Spotify scancode...")
    scancode_path = os.path.join(Config.ALBUM_ART_DIR, f"scancode_{track_id}.png")
    scancode_img = download_scancode(spotify_uri, scancode_path)

    if not scancode_img:
        print("❌ Failed to download scancode")
        return

    print(f"✅ Scancode saved to {scancode_path}\n")

    # Try to get track data from Spotify API
    print("📡 Fetching track metadata from Spotify API...")
    track_data = get_spotify_track_data(track_id)

    album_art_path = None
    if track_data and track_data.get('artist') != 'Unknown Artist':
        print(f"✅ Track: {track_data['track']}")
        print(f"   Artist: {track_data['artist']}")
        print(f"   Album: {track_data['album']}\n")

        # Download album art
        if track_data.get('album_art_url'):
            print("📡 Downloading album art...")
            album_art_path = os.path.join(Config.ALBUM_ART_DIR, f"album_{track_id}.jpg")
            if download_album_art(track_data['album_art_url'], album_art_path):
                print()
            else:
                album_art_path = None
    else:
        # Try scraping Spotify page for metadata (workaround for no auth)
        print("⚠️  Spotify API requires auth, trying page scrape...")
        try:
            page_url = f"https://open.spotify.com/track/{track_id}"
            response = requests.get(page_url, timeout=10)
            if response.status_code == 200:
                # Very basic HTML scraping for album art
                import re
                # Look for og:image meta tag (album art)
                art_match = re.search(r'<meta property="og:image" content="([^"]+)"', response.text)
                if art_match:
                    album_art_url = art_match.group(1)
                    print(f"✅ Found album art via page scrape")
                    album_art_path = os.path.join(Config.ALBUM_ART_DIR, f"album_{track_id}.jpg")
                    download_album_art(album_art_url, album_art_path)

                # Look for og:title (track name)
                title_match = re.search(r'<meta property="og:title" content="([^"]+)"', response.text)
                # Look for og:description (usually has artist)
                desc_match = re.search(r'<meta property="og:description" content="([^"]+)"', response.text)

                if title_match:
                    title = title_match.group(1)
                    artist = 'Unknown Artist'

                    # Description usually starts with artist name
                    if desc_match:
                        desc = desc_match.group(1)
                        # Format is usually: "Artist Name · Song Title"
                        if ' · ' in desc:
                            artist = desc.split(' · ')[0].strip()

                    # Try to get artist URI to fetch monthly listeners
                    listeners = None
                    artist_uri_match = re.search(r'spotify:artist:([a-zA-Z0-9]+)', response.text)
                    if artist_uri_match:
                        artist_id = artist_uri_match.group(1)
                        try:
                            # Fetch artist page to get monthly listeners
                            artist_url = f"https://open.spotify.com/artist/{artist_id}"
                            artist_response = requests.get(artist_url, timeout=10)
                            if artist_response.status_code == 200:
                                # Look for monthly listeners pattern in the artist page HTML
                                listeners_match = re.search(r'([\d,]+)\s+monthly listeners', artist_response.text, re.IGNORECASE)
                                if listeners_match:
                                    listeners_count = listeners_match.group(1).replace(',', '')
                                    try:
                                        num = int(listeners_count)
                                        if num >= 1000000:
                                            listeners = f"{num // 1000000}M"
                                        elif num >= 1000:
                                            listeners = f"{num // 1000}K"
                                        else:
                                            listeners = str(num)
                                    except:
                                        listeners = listeners_match.group(1)
                        except Exception as e:
                            print(f"⚠️  Could not fetch artist listeners: {e}")

                    track_data = {
                        'track': title,
                        'artist': artist,
                        'album': 'Unknown Album',
                        'listeners': listeners if listeners else '○○○'  # Placeholder if can't scrape
                    }
                    print(f"✅ Track: {track_data['track']}")
                    print(f"   Artist: {track_data['artist']}")
                    if listeners:
                        print(f"   Listeners: {listeners} monthly listeners\n")
                    else:
                        print()
                else:
                    track_data = {'artist': 'Unknown Artist', 'track': 'Unknown Track', 'album': 'Unknown Album'}
            else:
                track_data = {'artist': 'Unknown Artist', 'track': 'Unknown Track', 'album': 'Unknown Album'}
        except Exception as e:
            print(f"⚠️  Page scrape failed: {e}")
            track_data = {'artist': 'Unknown Artist', 'track': 'Unknown Track', 'album': 'Unknown Album'}

    # Create display image with scancode (243x304 - matches empty scancode ratio)
    print("🎨 Creating display image with scancode...")
    display_image_path = create_square_scancode_image(scancode_path, album_art_path, width=243, height=304)

    # Render on display
    print("\n📺 Rendering to display...")
    display = LeelooDisplay(preview_mode=True)

    # Mock data for the other panels (matching expected format)
    weather_data = {
        'temp_f': 72,
        'sun': 9,   # 0-10 scale
        'rain': 1,  # 0-10 scale
        'uv': 5     # 0-10 scale
    }
    time_data = {
        'time_str': '9:30 PM',
        'date_str': 'Feb 11',
        'seconds': 30
    }
    contacts = ['Amy', 'Ben']  # List of names
    album_data = {
        'artist': track_data['artist'],
        'album': track_data.get('album', ''),
        'track': track_data['track'],
        'bpm': 128,
        'listeners': track_data.get('listeners'),  # From scraped data
        'pushed_by': 'You'
    }

    # Render the full UI with our scancode display image
    display.render(weather_data, time_data, contacts, album_data, album_art_path=display_image_path)

    # Save preview
    preview_path = "spotify_display_preview.png"
    display.image.save(preview_path)
    print(f"✅ Preview saved to {preview_path}")

    # If on Pi, also render to screen
    if os.path.exists('/dev/fb1'):
        print("\n📺 Rendering to framebuffer /dev/fb1...")
        try:
            display_hw = LeelooDisplay(preview_mode=False)
            display_hw.render(weather_data, time_data, contacts, album_data, album_art_path=display_image_path)
            display_hw.show()
            print("✅ Displayed on screen!")
        except Exception as e:
            print(f"⚠️  Could not render to hardware display: {e}")

    # Save to current_music.json so main loop picks it up
    print("\n💾 Saving to current music state...")
    current_music = {
        'artist': track_data['artist'],
        'track': track_data['track'],
        'album': track_data.get('album', ''),
        'spotify_uri': spotify_uri,
        'pushed_by': 'Test',
        'album_art_cached': display_image_path,
        'timestamp': time.time()
    }

    music_state_path = os.path.join(Config.LEELOO_HOME, "current_music.json")
    os.makedirs(os.path.dirname(music_state_path), exist_ok=True)

    import json
    with open(music_state_path, 'w') as f:
        json.dump(current_music, f, indent=2)

    print(f"✅ Saved to {music_state_path}")
    print(f"   Main loop will display this track now!")

    print("\n✨ Done!")
    print(f"\nFiles created:")
    print(f"  - Scancode: {scancode_path}")
    if album_art_path:
        print(f"  - Album art: {album_art_path}")
    if display_image_path:
        print(f"  - Display image: {display_image_path}")
    print(f"  - Preview: {preview_path}")
    print(f"  - Music state: {music_state_path}")


if __name__ == '__main__':
    # Default test track if none provided
    default_track = "share sabotage by beastie boys"

    if len(sys.argv) > 1:
        # Join all arguments to support spaces without quotes
        spotify_input = ' '.join(sys.argv[1:])
    else:
        print(f"ℹ️  No track provided, using default: \"{default_track}\"")
        print(f"   Usage:")
        print(f"     python3 test_spotify_display.py <spotify-url>")
        print(f"     python3 test_spotify_display.py share [track] by [artist]")
        print(f"   Examples:")
        print(f"     python3 test_spotify_display.py share sabotage by beastie boys")
        print(f"     python3 test_spotify_display.py play mr brightside by the killers\n")
        spotify_input = default_track

    test_spotify_display(spotify_input)
