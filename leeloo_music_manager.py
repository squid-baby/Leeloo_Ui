#!/usr/bin/env python3
"""
LEELOO Music Manager
Uses centralized album art utility for consistent sizing
Fetches real monthly listeners by scraping Spotify artist pages
"""
import json
import os
import re
import time
import requests
import base64

# Import centralized album art utility
from leeloo_album_art import download_and_create_album_art
from leeloo_fileutil import atomic_write_json

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")  # same override as leeloo_brain
TOKENS_FILE = os.path.join(LEELOO_HOME, "spotify_tokens.json")
CURRENT_MUSIC_FILE = os.path.join(LEELOO_HOME, "current_music.json")
ALBUM_ART_DIR = os.path.join(LEELOO_HOME, "album_art")
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
ENV_FILE = os.path.join(LEELOO_HOME, ".env")


def load_env_defaults(env_path=ENV_FILE):
    """Load KEY=VALUE pairs from .env into os.environ, never overriding a key
    that is already set, even to an empty string (an operator can export
    KEY= to disable it). Same format/quote stripping as LeelooBrain._load_env.

    This module can be imported before leeloo_brain loads .env, or run
    standalone, so it reads the file itself. Returns the keys it set.
    """
    loaded = []
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value and key not in os.environ:
                        os.environ[key] = value
                        loaded.append(key)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[MUSIC] Error reading {env_path}: {e}")
    return loaded


# Spotify app credentials come ONLY from the environment / .env (never hardcode them)
load_env_defaults()
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    print(f"[MUSIC] WARNING: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set (add them to {ENV_FILE}). "
          "Token refresh and artist lookups are disabled.")


def _have_spotify_credentials():
    """True when both Spotify app credentials are configured"""
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)


# (Shared music no longer has a fixed timeout — now-playing always overrides it)

# Cache for monthly listeners (artist_id -> (listeners_str, timestamp))
_listeners_cache = {}
LISTENERS_CACHE_TTL = 3600  # Cache for 1 hour


def load_tokens():
    """Load Spotify tokens"""
    try:
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_tokens(tokens):
    """Save Spotify tokens (atomic, owner-only: they are secrets)"""
    atomic_write_json(TOKENS_FILE, tokens, indent=2, mode=0o600)


def refresh_access_token():
    """Refresh expired access token"""
    tokens = load_tokens()
    if not tokens or 'refresh_token' not in tokens:
        return None
    if not _have_spotify_credentials():
        print("   ⚠️  Can't refresh token: Spotify client credentials missing from .env")
        return None

    try:
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']},
            headers={'Authorization': f'Basic {auth_b64}', 'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )

        if response.status_code == 200:
            new_tokens = response.json()
            if 'refresh_token' not in new_tokens:
                new_tokens['refresh_token'] = tokens['refresh_token']
            save_tokens(new_tokens)
            print("   ✅ Access token refreshed")
            return new_tokens['access_token']
    except Exception as e:
        print(f"   ❌ Error refreshing token: {e}")

    return None


def format_listeners(count):
    """Format listener count (e.g. 1234567 -> '1.2M')"""
    if count >= 1000000:
        return f"{count / 1000000:.1f}M"
    elif count >= 1000:
        return f"{count / 1000:.0f}K"
    else:
        return str(count)


def scrape_monthly_listeners(artist_id):
    """
    Scrape monthly listeners from Spotify artist page.
    The og:description meta tag contains "Artist · X.XM monthly listeners."
    Falls back to parsing the page body for exact numbers.
    """
    # Check cache first
    if artist_id in _listeners_cache:
        cached_str, cached_time = _listeners_cache[artist_id]
        if time.time() - cached_time < LISTENERS_CACHE_TTL:
            return cached_str

    url = f"https://open.spotify.com/artist/{artist_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"   ⚠️  Spotify page returned {resp.status_code}")
            return None

        text = resp.text

        # Method 1: Parse og:description meta tag
        # Format: "Artist · 3.6M monthly listeners."
        og_match = re.search(
            r'<meta\s+property="og:description"\s+content="[^"]*?(\d[\d,.]*[MKB]?)\s*monthly\s*listener',
            text, re.IGNORECASE
        )
        if og_match:
            listeners_str = og_match.group(1)
            _listeners_cache[artist_id] = (listeners_str, time.time())
            print(f"   🎧 Monthly listeners (og:desc): {listeners_str}")
            return listeners_str

        # Method 2: Find exact number in page body
        num_match = re.search(r'([\d,]+)\s*monthly\s*listener', text, re.IGNORECASE)
        if num_match:
            raw_number = num_match.group(1).replace(',', '')
            try:
                count = int(raw_number)
                listeners_str = format_listeners(count)
                _listeners_cache[artist_id] = (listeners_str, time.time())
                print(f"   🎧 Monthly listeners (body): {listeners_str}")
                return listeners_str
            except ValueError:
                pass

        # Method 3: meta description tag
        meta_match = re.search(
            r'<meta\s+name="description"\s+content="[^"]*?(\d[\d,.]*[MKB]?)\s*monthly\s*listener',
            text, re.IGNORECASE
        )
        if meta_match:
            listeners_str = meta_match.group(1)
            _listeners_cache[artist_id] = (listeners_str, time.time())
            print(f"   🎧 Monthly listeners (meta): {listeners_str}")
            return listeners_str

        print("   ⚠️  Could not find monthly listeners on page")

    except Exception as e:
        print(f"   ⚠️  Error scraping monthly listeners: {e}")

    return None


def get_currently_playing():
    """Fetch currently playing track"""
    tokens = load_tokens()
    if not tokens:
        return None

    headers = {"Authorization": f"Bearer {tokens.get('access_token')}"}

    try:
        response = requests.get(f"{SPOTIFY_API_BASE}/me/player/currently-playing", headers=headers, timeout=5)

        # Handle expired token
        if response.status_code == 401:
            print("   Access token expired, refreshing...")
            new_token = refresh_access_token()
            if new_token:
                headers = {"Authorization": f"Bearer {new_token}"}
                response = requests.get(f"{SPOTIFY_API_BASE}/me/player/currently-playing", headers=headers, timeout=5)

        if response.status_code == 200:
            data = response.json()
            if data and data.get('item'):
                item = data['item']
                album_art_url = item.get('album', {}).get('images', [{}])[0].get('url')
                spotify_uri = item['uri']

                # Get artist ID and scrape monthly listeners
                artist_id = item.get('artists', [{}])[0].get('id')
                listeners = None
                if artist_id:
                    listeners = scrape_monthly_listeners(artist_id)

                # Use centralized utility to create album art
                album_art_cached = download_and_create_album_art(
                    album_art_url,
                    spotify_uri,
                    ALBUM_ART_DIR,
                    source='currently_playing'
                )

                result = {
                    "artist": item['artists'][0]['name'] if item.get('artists') else "Unknown",
                    "track": item['name'],
                    "album": item.get('album', {}).get('name', ''),
                    "spotify_uri": spotify_uri,
                    "album_art_url": album_art_url,
                    "album_art_cached": album_art_cached,
                    "is_playing": data.get('is_playing', False),
                    "source": "currently_playing",
                    "pushed_by": "You",
                    "timestamp": time.time(),
                    "listeners": listeners
                }

                return result
    except Exception as e:
        print(f"Error fetching currently playing: {e}")

    return None


def get_listeners_for_artist_name(artist_name):
    """
    Look up monthly listeners for an artist by name (for shared music).
    Searches Spotify, gets artist ID, then scrapes.
    """
    if not _have_spotify_credentials():
        return None

    try:
        # Use client credentials to search (no user token needed)
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        token_resp = requests.post(
            SPOTIFY_TOKEN_URL,
            data={'grant_type': 'client_credentials'},
            headers={'Authorization': f'Basic {auth_b64}', 'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=5
        )

        if token_resp.status_code != 200:
            return None

        cc_token = token_resp.json()['access_token']
        headers = {"Authorization": f"Bearer {cc_token}"}

        # Search for artist
        search_resp = requests.get(
            f"{SPOTIFY_API_BASE}/search",
            params={"q": artist_name, "type": "artist", "limit": 1},
            headers=headers,
            timeout=5
        )

        if search_resp.status_code == 200:
            artists = search_resp.json().get('artists', {}).get('items', [])
            if artists:
                artist_id = artists[0]['id']
                return scrape_monthly_listeners(artist_id)

    except Exception as e:
        print(f"   ⚠️  Error looking up artist listeners: {e}")

    return None


def load_current_music():
    """Load current music state"""
    try:
        with open(CURRENT_MUSIC_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def update_music_display():
    """Update music display with priority logic:
    - Now playing on Spotify ALWAYS takes the display (real-time wins)
    - When Spotify stops, fall back to the most recently shared song
    - current_music.json is ONLY written by shared/pushed songs, not by now-playing,
      so it always holds the last shared track for fallback
    """
    shared_music = load_current_music()   # shared/pushed song written by voice or Telegram
    currently_playing = get_currently_playing()

    # Priority 1: Spotify is actively playing → always show it, don't overwrite shared music
    if currently_playing and currently_playing.get('is_playing'):
        listeners_info = f" ({currently_playing.get('listeners')} listeners)" if currently_playing.get('listeners') else ""
        print(f"▶️ Now playing: {currently_playing['artist']} - {currently_playing['track']}{listeners_info}")
        return currently_playing

    # Priority 2: Spotify paused/stopped → show most recently shared song
    if shared_music and shared_music.get('source') == 'shared':
        # Fetch listeners if not yet populated
        if not shared_music.get('listeners'):
            artist = shared_music.get('artist')
            if artist:
                listeners = get_listeners_for_artist_name(artist)
                if listeners:
                    shared_music['listeners'] = listeners
                    atomic_write_json(CURRENT_MUSIC_FILE, shared_music, indent=2)
                    print(f"   📤 Updated shared music listeners: {listeners}")
        age = time.time() - shared_music.get('timestamp', 0)
        print(f"📤 Nothing playing → showing shared: {shared_music.get('artist')} - {shared_music.get('track')} ({int(age/60)} min old)")
        return shared_music

    # Priority 3: Spotify paused, nothing shared yet — show paused track
    if currently_playing:
        listeners_info = f" ({currently_playing.get('listeners')} listeners)" if currently_playing.get('listeners') else ""
        print(f"⏸️ Paused: {currently_playing['artist']} - {currently_playing['track']}{listeners_info}")
        return currently_playing

    # Priority 4: Fallback to whatever was last stored (old shared or old now-playing)
    if shared_music:
        print(f"🔇 Nothing playing → showing last known: {shared_music.get('artist')} - {shared_music.get('track')}")
        return shared_music

    print("🔇 Nothing currently playing, no shared music")
    return None


if __name__ == "__main__":
    print("Testing music manager...")
    result = update_music_display()

    if result:
        print(f"\n  Source: {result.get('source')}")
        print(f"  Artist: {result.get('artist')}")
        print(f"  Track: {result.get('track')}")
        print(f"  Listeners: {result.get('listeners', 'Not available')}")
        print(f"  Album art: {result.get('album_art_cached', 'Not cached')}")
