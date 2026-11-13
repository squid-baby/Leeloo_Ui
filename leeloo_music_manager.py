#!/usr/bin/env python3
"""
LEELOO Music Manager

- "Now playing" from the user's Spotify account (OAuth tokens)
- Track search with strict validation (Spotify returns fuzzy matches for ANY query)
- Shared-track cache in current_music.json (album art + scancode)
- Monthly listeners scraped from the public artist page (cached 1 hour)

All functions here BLOCK on network I/O. Call them from a worker thread
(loop.run_in_executor), never directly on the asyncio event loop.

Secrets: SPOTIFY_CLIENT_SECRET must be set in /home/pi/leeloo-ui/.env.
It is never stored in the source code.
"""
import base64
import json
import os
import re
import time
from urllib.parse import quote

import requests

# Import centralized album art utility
from leeloo_album_art import download_and_create_album_art

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
TOKENS_FILE = os.path.join(LEELOO_HOME, "spotify_tokens.json")
CURRENT_MUSIC_FILE = os.path.join(LEELOO_HOME, "current_music.json")
ALBUM_ART_DIR = os.path.join(LEELOO_HOME, "album_art")
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

# The client ID is public (it is part of the OAuth URL in the QR code).
DEFAULT_SPOTIFY_CLIENT_ID = "f8c3c0120e694af283d7d7f7c2f67d4c"

HTTP_TIMEOUT = 5  # seconds, for every Spotify API call

# Cache for monthly listeners: key -> (listeners_str or None, timestamp)
_listeners_cache = {}
LISTENERS_CACHE_TTL = 3600  # 1 hour (failed lookups are cached too)

# Cached app (client-credentials) token: (token, expires_at)
_app_token = (None, 0.0)

# Words that carry no information for matching a search result
_IGNORE_WORDS = {'the', 'a', 'an', 'by', 'and', 'or', 'with', 'to', 'from'}


# =============================================================================
# Credentials and tokens
# =============================================================================

def spotify_client_id():
    return os.environ.get("SPOTIFY_CLIENT_ID") or DEFAULT_SPOTIFY_CLIENT_ID


def spotify_client_secret():
    """Read the secret from the environment (.env). Empty string if not set."""
    return os.environ.get("SPOTIFY_CLIENT_SECRET", "")


def _basic_auth_header():
    secret = spotify_client_secret()
    if not secret:
        print("[MUSIC] SPOTIFY_CLIENT_SECRET is not set in .env — Spotify is disabled")
        return None
    auth = base64.b64encode(f"{spotify_client_id()}:{secret}".encode()).decode()
    return {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/x-www-form-urlencoded',
    }


def write_json_atomic(path, data):
    """Write JSON so that a reader never sees a half-written file."""
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_tokens():
    """Load the user's Spotify OAuth tokens"""
    try:
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_tokens(tokens):
    """Save the user's Spotify OAuth tokens"""
    write_json_atomic(TOKENS_FILE, tokens)


def refresh_access_token():
    """Refresh the user's expired access token. Returns the new token or None."""
    tokens = load_tokens()
    if not tokens or 'refresh_token' not in tokens:
        return None
    headers = _basic_auth_header()
    if not headers:
        return None

    try:
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            new_tokens = response.json()
            if 'refresh_token' not in new_tokens:
                new_tokens['refresh_token'] = tokens['refresh_token']
            save_tokens(new_tokens)
            print("[MUSIC] Access token refreshed")
            return new_tokens.get('access_token')
        print(f"[MUSIC] Token refresh failed: HTTP {response.status_code}")
    except Exception as e:
        print(f"[MUSIC] Error refreshing token: {e}")

    return None


def get_app_token():
    """Client-credentials token for search and track lookup.
    Cached until shortly before it expires (Spotify gives 1 hour)."""
    global _app_token
    token, expires_at = _app_token
    if token and time.time() < expires_at:
        return token

    headers = _basic_auth_header()
    if not headers:
        return None
    try:
        resp = requests.post(
            SPOTIFY_TOKEN_URL,
            data={'grant_type': 'client_credentials'},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[MUSIC] Spotify token error: HTTP {resp.status_code}")
            return None
        body = resp.json()
        token = body.get('access_token')
        _app_token = (token, time.time() + max(60, int(body.get('expires_in', 3600)) - 60))
        return token
    except Exception as e:
        print(f"[MUSIC] Spotify token error: {e}")
        return None


def _api_get(path, params=None):
    """GET from the Spotify API with the app token. Returns JSON dict or None."""
    global _app_token
    for attempt in (1, 2):
        token = get_app_token()
        if not token:
            return None
        resp = requests.get(
            f"{SPOTIFY_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 401 and attempt == 1:
            _app_token = (None, 0.0)   # token was revoked early: get a new one
            continue
        if resp.status_code != 200:
            print(f"[MUSIC] Spotify API {path}: HTTP {resp.status_code}")
            return None
        return resp.json()
    return None


# =============================================================================
# Search + validation
# =============================================================================

def query_words(query):
    """Significant lowercase words of a search query."""
    return [
        w for w in re.findall(r"\w+", (query or "").lower())
        if w not in _IGNORE_WORDS and len(w) > 2
    ]


def _track_text(track):
    artists = track.get('artists') or []
    artist = (artists[0].get('name', '') if artists else '').lower()
    name = (track.get('name') or '').lower()
    # Normalise to single-space separated words so bigrams match across punctuation
    return " ".join(re.findall(r"\w+", f"{artist} {name}"))


def score_track(words, track):
    """Score a Spotify result against query words.
    Returns (bigram_matches, word_matches). Higher is better."""
    combined = _track_text(track)
    result_words = set(combined.split())
    word_hits = sum(1 for w in words if w in result_words)
    padded = f" {combined} "
    bigram_hits = sum(
        1 for i in range(len(words) - 1)
        if f" {words[i]} {words[i + 1]} " in padded
    )
    return (bigram_hits, word_hits)


def pick_best_track(query, tracks):
    """Pick the result that really matches the query, or None.

    Spotify returns fuzzy matches for ANY query, so:
    - 2+ significant words: at least one contiguous word pair must appear in
      "artist title" (stops "bed michael jackson" matching "Michael Perry").
    - 1 significant word: that word must appear.
    - no significant words (e.g. "the the"): accept the top result.
    """
    if not tracks:
        return None
    words = query_words(query)
    if not words:
        return tracks[0]

    best, best_score = None, (0, 0)
    for t in tracks:
        s = score_track(words, t)
        if s > best_score:
            best, best_score = t, s

    if len(words) >= 2 and best_score[0] == 0:
        return None
    if len(words) == 1 and best_score[1] == 0:
        return None
    return best


def find_track(query):
    """Find one track. `query` is free text or a 'spotify:track:<id>' URI.
    Returns the Spotify track object, or None."""
    if not query:
        return None
    try:
        if query.startswith('spotify:track:'):
            return _api_get(f"/tracks/{quote(query.split(':')[-1], safe='')}")

        data = _api_get("/search", {"q": query, "type": "track", "limit": 5})
        tracks = ((data or {}).get('tracks') or {}).get('items') or []
        if not tracks:
            print(f"[MUSIC] No tracks found for: {query}")
            return None
        best = pick_best_track(query, tracks)
        if best is None:
            print(f"[MUSIC] No good match for '{query}' in the top {len(tracks)} results")
        return best
    except Exception as e:
        print(f"[MUSIC] Search error: {e}")
        return None


def track_to_music(track, pushed_by):
    """Convert a Spotify track object to the LEELOO music dict (no downloads)."""
    artists = track.get('artists') or []
    images = (track.get('album') or {}).get('images') or []
    return {
        'artist': artists[0].get('name', 'Unknown') if artists else 'Unknown',
        'track': track.get('name', ''),
        'album': (track.get('album') or {}).get('name', ''),
        'spotify_uri': track.get('uri', ''),
        'album_art_url': images[0].get('url', '') if images else '',
        'album_art_cached': None,
        'source': 'shared',
        'pushed_by': pushed_by,
        'timestamp': time.time(),
    }


def save_shared_track(track, pushed_by):
    """Download art + scancode for a track and make it the shared track
    (writes current_music.json). Returns the music dict."""
    music = track_to_music(track, pushed_by)
    uri = music['spotify_uri']
    scancode_url = (
        "https://scannables.scdn.co/uri/plain/png/1A1D2E/white/280/"
        f"{quote(uri, safe=':')}"
    )
    music['album_art_cached'] = download_and_create_album_art(
        music['album_art_url'], uri, ALBUM_ART_DIR,
        source='shared', scancode_url=scancode_url,
    )
    write_json_atomic(CURRENT_MUSIC_FILE, music)
    print(f"[MUSIC] Shared track saved: {music['artist']} - {music['track']}")
    return music


def search_and_save_shared(query, pushed_by):
    """find_track() + save_shared_track(). Returns the music dict or None."""
    track = find_track(query)
    if not track or not track.get('uri'):
        return None
    try:
        return save_shared_track(track, pushed_by)
    except Exception as e:
        print(f"[MUSIC] Could not save shared track: {e}")
        return None


# =============================================================================
# Monthly listeners (scraped; Spotify has no API for it)
# =============================================================================

def format_listeners(count):
    """Format listener count (e.g. 1234567 -> '1.2M')"""
    if count >= 1000000:
        return f"{count / 1000000:.1f}M"
    elif count >= 1000:
        return f"{count / 1000:.0f}K"
    else:
        return str(count)


def parse_monthly_listeners(html):
    """Extract the monthly listeners text from an artist page. None if absent."""
    # og:description / description: "Artist · 3.6M monthly listeners."
    meta = re.search(
        r'<meta\s+(?:property="og:description"|name="description")\s+content="[^"]*?'
        r'(\d[\d,.]*[MKB]?)\s*monthly\s*listener',
        html, re.IGNORECASE
    )
    if meta:
        return meta.group(1)
    # Exact number in the page body
    num = re.search(r'([\d,]+)\s*monthly\s*listener', html, re.IGNORECASE)
    if num:
        try:
            return format_listeners(int(num.group(1).replace(',', '')))
        except ValueError:
            pass
    return None


def _cached_listeners(key):
    """Returns (hit, value)."""
    entry = _listeners_cache.get(key)
    if entry and time.time() - entry[1] < LISTENERS_CACHE_TTL:
        return True, entry[0]
    return False, None


def scrape_monthly_listeners(artist_id):
    """Monthly listeners for an artist ID. Cached 1 hour, failures included."""
    hit, value = _cached_listeners(artist_id)
    if hit:
        return value

    listeners = None
    try:
        resp = requests.get(
            f"https://open.spotify.com/artist/{artist_id}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            listeners = parse_monthly_listeners(resp.text)
        else:
            print(f"[MUSIC] Artist page returned {resp.status_code}")
    except Exception as e:
        print(f"[MUSIC] Error scraping monthly listeners: {e}")

    _listeners_cache[artist_id] = (listeners, time.time())
    return listeners


def get_listeners_for_artist_name(artist_name):
    """Monthly listeners by artist name (for shared music). Cached 1 hour."""
    key = f"name:{artist_name.lower()}"
    hit, value = _cached_listeners(key)
    if hit:
        return value

    listeners = None
    try:
        data = _api_get("/search", {"q": artist_name, "type": "artist", "limit": 1})
        artists = ((data or {}).get('artists') or {}).get('items') or []
        if artists and artists[0].get('id'):
            listeners = scrape_monthly_listeners(artists[0]['id'])
    except Exception as e:
        print(f"[MUSIC] Error looking up artist listeners: {e}")

    _listeners_cache[key] = (listeners, time.time())
    return listeners


# =============================================================================
# Now playing
# =============================================================================

def _fetch_currently_playing(access_token):
    return requests.get(
        f"{SPOTIFY_API_BASE}/me/player/currently-playing",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=HTTP_TIMEOUT,
    )


def get_currently_playing():
    """Fetch the user's currently playing track. None if nothing / not connected."""
    tokens = load_tokens()
    if not tokens or not tokens.get('access_token'):
        return None

    try:
        response = _fetch_currently_playing(tokens['access_token'])

        if response.status_code == 401:
            new_token = refresh_access_token()
            if not new_token:
                return None
            response = _fetch_currently_playing(new_token)

        if response.status_code != 200:   # 204 = nothing playing
            return None

        data = response.json() or {}
        item = data.get('item')
        if not item or not item.get('uri'):
            return None

        artists = item.get('artists') or []
        images = (item.get('album') or {}).get('images') or []
        album_art_url = images[0].get('url') if images else None
        spotify_uri = item['uri']

        listeners = None
        artist_id = artists[0].get('id') if artists else None
        if artist_id:
            listeners = scrape_monthly_listeners(artist_id)

        album_art_cached = download_and_create_album_art(
            album_art_url, spotify_uri, ALBUM_ART_DIR, source='currently_playing'
        )

        return {
            "artist": artists[0].get('name', 'Unknown') if artists else "Unknown",
            "track": item.get('name', ''),
            "album": (item.get('album') or {}).get('name', ''),
            "spotify_uri": spotify_uri,
            "album_art_url": album_art_url,
            "album_art_cached": album_art_cached,
            "is_playing": bool(data.get('is_playing', False)),
            "source": "currently_playing",
            "pushed_by": "You",
            "timestamp": time.time(),
            "listeners": listeners,
        }
    except Exception as e:
        print(f"[MUSIC] Error fetching currently playing: {e}")

    return None


def load_current_music():
    """Load the last shared track (current_music.json)"""
    try:
        with open(CURRENT_MUSIC_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def update_music_display():
    """What should the display show? Priority:
    1. Spotify is actively playing            -> now playing (real time wins)
    2. Not playing, a shared song exists      -> most recently shared song
    3. Paused track, nothing shared           -> the paused track
    4. Whatever is in current_music.json
    current_music.json is ONLY written by shared/pushed songs.
    """
    shared_music = load_current_music()
    currently_playing = get_currently_playing()

    if currently_playing and currently_playing.get('is_playing'):
        return currently_playing

    if shared_music and shared_music.get('source') == 'shared':
        if not shared_music.get('listeners') and shared_music.get('artist'):
            listeners = get_listeners_for_artist_name(shared_music['artist'])
            if listeners:
                shared_music['listeners'] = listeners
                try:
                    write_json_atomic(CURRENT_MUSIC_FILE, shared_music)
                except OSError as e:
                    print(f"[MUSIC] Could not update shared music file: {e}")
        return shared_music

    if currently_playing:
        return currently_playing

    return shared_music


if __name__ == "__main__":
    print("Testing music manager...")
    result = update_music_display()
    if result:
        print(f"  Source: {result.get('source')}")
        print(f"  Artist: {result.get('artist')}")
        print(f"  Track: {result.get('track')}")
        print(f"  Listeners: {result.get('listeners', 'Not available')}")
        print(f"  Album art: {result.get('album_art_cached', 'Not cached')}")
    else:
        print("  Nothing to show")
