#!/usr/bin/env python3
"""
LEELOO Show Discovery — Ticketmaster API for local concert discovery

Fetches upcoming music events near the device's location and caches them
for display when the device has been music-idle for 2+ hours.

Setup: add TICKETMASTER_API_KEY to /home/pi/leeloo-ui/.env
Free tier: 5000 calls/day — we call once per 24h so this is fine.
Sign up at: https://developer.ticketmaster.com/
"""

import json
import math
import os
import time
from typing import Any, Dict, List

import requests

LEELOO_HOME = os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")
SHOWS_FILE = os.path.join(LEELOO_HOME, "local_shows.json")
SHOWS_REFRESH_INTERVAL = 86400  # 24 hours
DEFAULT_RADIUS_MILES = 50
TICKETMASTER_API = "https://app.ticketmaster.com/discovery/v2/events.json"


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two lat/lon coordinates in miles"""
    R = 3959.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def fetch_local_shows(
    lat: float,
    lon: float,
    api_key: str,
    radius_miles: int = DEFAULT_RADIUS_MILES,
) -> List[Dict[str, Any]]:
    """
    Fetch upcoming music events near (lat, lon) via Ticketmaster.

    Returns list of show dicts, sorted by date:
      {artist, venue, city, date, time, url, distance_miles}
    """
    if not api_key or lat is None or lon is None:
        return []

    try:
        resp = requests.get(
            TICKETMASTER_API,
            params={
                "classificationName": "music",
                "latlong": f"{lat},{lon}",
                "radius": radius_miles,
                "unit": "miles",
                "size": 20,
                "sort": "date,asc",
                "apikey": api_key,
            },
            timeout=10,
        )

        if resp.status_code == 429:
            print("[SHOWS] Ticketmaster rate limited")
            return []
        if resp.status_code != 200:
            print(f"[SHOWS] Ticketmaster error: {resp.status_code}")
            return []

        events = resp.json().get("_embedded", {}).get("events", [])
        shows = []

        for event in events:
            try:
                # Prefer headliner name from attractions over event name
                attractions = event.get("_embedded", {}).get("attractions", [])
                artist = (attractions[0].get("name") if attractions
                          else event.get("name", "Unknown Artist"))

                venue_obj = event.get("_embedded", {}).get("venues", [{}])[0]
                venue_name = venue_obj.get("name", "")
                venue_city = venue_obj.get("city", {}).get("name", "")

                start = event.get("dates", {}).get("start", {})
                date_str = start.get("localDate", "")
                time_str = start.get("localTime", "")

                url = event.get("url", "")

                # Distance from device
                loc = venue_obj.get("location", {})
                try:
                    v_lat = float(loc.get("latitude", lat))
                    v_lon = float(loc.get("longitude", lon))
                    distance = round(_haversine_miles(lat, lon, v_lat, v_lon), 1)
                except (TypeError, ValueError):
                    distance = 0.0

                shows.append({
                    "artist": artist,
                    "venue": venue_name,
                    "city": venue_city,
                    "date": date_str,
                    "time": time_str,
                    "url": url,
                    "distance_miles": distance,
                })
            except Exception as e:
                print(f"[SHOWS] Event parse error: {e}")
                continue

        shows.sort(key=lambda s: s.get("date", ""))
        print(f"[SHOWS] Found {len(shows)} shows within {radius_miles} miles")
        return shows[:10]

    except requests.exceptions.Timeout:
        print("[SHOWS] Ticketmaster request timed out")
        return []
    except Exception as e:
        print(f"[SHOWS] Fetch error: {e}")
        return []


def load_cached_shows() -> List[Dict[str, Any]]:
    """
    Load shows from disk cache if it is less than 24 hours old.
    Returns empty list if cache is absent or stale.
    """
    try:
        if os.path.exists(SHOWS_FILE):
            with open(SHOWS_FILE) as f:
                data = json.load(f)
            if time.time() - data.get("fetched_at", 0) < SHOWS_REFRESH_INTERVAL:
                shows = data.get("shows", [])
                print(f"[SHOWS] Loaded {len(shows)} cached shows")
                return shows
    except Exception as e:
        print(f"[SHOWS] Cache load error: {e}")
    return []


def save_shows(shows: List[Dict[str, Any]]) -> None:
    """Persist shows to disk with a timestamp"""
    try:
        with open(SHOWS_FILE, "w") as f:
            json.dump({"shows": shows, "fetched_at": time.time()}, f, indent=2)
    except Exception as e:
        print(f"[SHOWS] Save error: {e}")


# =============================================================================
# Test
# =============================================================================

if __name__ == "__main__":
    import sys

    api_key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not api_key:
        env_path = os.path.join(LEELOO_HOME, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("TICKETMASTER_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not api_key:
        print("Set TICKETMASTER_API_KEY in .env or environment")
        sys.exit(1)

    # Default to Chapel Hill, NC for testing
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 35.913
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else -79.055

    print(f"\nFetching shows near ({lat}, {lon})...\n")
    shows = fetch_local_shows(lat, lon, api_key)

    for i, s in enumerate(shows, 1):
        print(f"{i}. {s['artist']}")
        print(f"   {s['venue']} — {s['city']}")
        print(f"   {s['date']} {s['time']}  ({s['distance_miles']} mi)")
        print(f"   {s['url'][:60]}")
        print()
