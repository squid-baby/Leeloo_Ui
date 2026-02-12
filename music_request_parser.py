#!/usr/bin/env python3
"""
Natural Language Music Request Parser
Handles requests like "share sabotage by beastie boys with my homies"
"""

import re
from typing import Optional, Dict

def parse_music_request(text: str) -> Optional[Dict[str, str]]:
    """
    Parse natural language music requests.

    Supported formats:
    - "share [track] by [artist]"
    - "play [track] by [artist]"
    - "[track] by [artist]"
    - Just a Spotify URL/URI

    Args:
        text: Natural language request

    Returns:
        Dict with 'track', 'artist', and 'query' for search, or None

    Examples:
        >>> parse_music_request("share sabotage by beastie boys")
        {'track': 'sabotage', 'artist': 'beastie boys', 'query': 'sabotage beastie boys'}

        >>> parse_music_request("play mr brightside by the killers")
        {'track': 'mr brightside', 'artist': 'the killers', 'query': 'mr brightside the killers'}
    """

    # Remove common filler words
    text = text.lower().strip()
    text = re.sub(r'\s+with (my )?homies.*$', '', text)
    text = re.sub(r'\s+with (my )?crew.*$', '', text)
    text = re.sub(r'\s+with (my )?friends.*$', '', text)
    text = re.sub(r'^\s*(share|play|put on)\s+', '', text)

    # Pattern: "[track name] by [artist name]"
    match = re.match(r'^(.+?)\s+by\s+(.+)$', text)

    if match:
        track = match.group(1).strip()
        artist = match.group(2).strip()
        return {
            'track': track,
            'artist': artist,
            'query': f'{track} {artist}'
        }

    # No pattern matched, treat as search query
    return {
        'track': text,
        'artist': '',
        'query': text
    }


def search_spotify_track(track: str, artist: str = '') -> Optional[str]:
    """
    Search for a track on Spotify and return the URI.

    Note: This is a placeholder - real implementation would use Spotify Web API.
    For now, it returns None (caller should handle this).

    Args:
        track: Track name
        artist: Artist name (optional)

    Returns:
        Spotify URI or None
    """
    # TODO: Implement Spotify Web API search
    # This requires OAuth credentials
    return None


if __name__ == '__main__':
    # Test cases
    test_requests = [
        "share sabotage by beastie boys with my homies",
        "play mr brightside by the killers",
        "bohemian rhapsody by queen",
        "share song 2 by blur",
    ]

    for request in test_requests:
        result = parse_music_request(request)
        print(f"'{request}' →")
        print(f"  Track: {result['track']}")
        print(f"  Artist: {result['artist']}")
        print(f"  Query: {result['query']}")
        print()
