"""Spotify search validation, token cache, secrets, shared-track cache."""
import json
import os

import pytest

import leeloo_music_manager as mm


def track(artist, name, uri="spotify:track:x"):
    return {"artists": [{"name": artist, "id": "a1"}], "name": name, "uri": uri,
            "album": {"name": "Alb", "images": [{"url": "http://img/x.jpg"}]}}


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text
    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(mm, "_app_token", (None, 0.0))
    mm._listeners_cache.clear()
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "s3cret")


def test_no_secret_in_source():
    src = open(mm.__file__).read()
    assert "SPOTIFY_CLIENT_SECRET = \"" not in src
    assert "9d6018d8" not in src


def test_missing_secret_disables_spotify_without_network(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET")
    monkeypatch.setattr(mm.requests, "post", lambda *a, **k: pytest.fail("no network call expected"))
    assert mm.get_app_token() is None
    assert mm.find_track("anything") is None


def test_pick_best_track_rejects_fuzzy_matches():
    results = [track("Michael Perry", "Long Beds and Jackson"), track("Other", "Song")]
    assert mm.pick_best_track("bed michael jackson", results) is None
    assert mm.pick_best_track("smoke and intolerance", results) is None
    assert mm.pick_best_track("anything", []) is None


def test_pick_best_track_accepts_real_matches():
    results = [track("Someone", "Blonde on Blonde"), track("Frank Ocean", "Nikes"),
               track("Frank Ocean", "Blonde")]
    assert mm.pick_best_track("Frank Ocean Blonde", results)['name'] in ("Nikes", "Blonde")
    assert mm.pick_best_track("frank ocean", results)['artists'][0]['name'] == "Frank Ocean"
    assert mm.pick_best_track("nikes", results)['name'] == "Nikes"
    # punctuation must not break word-pair matching
    assert mm.pick_best_track("fred again feisty", [track("Fred again..", "Feisty")]) is not None
    # a part of a word is not a match
    assert mm.pick_best_track("bed", [track("X", "Bedroom Beds")]) is None
    # only stop words: accept the top result
    assert mm.pick_best_track("the the", results) is results[0]


def test_app_token_is_cached(monkeypatch):
    calls = []
    def post(url, data=None, headers=None, timeout=None):
        calls.append(headers['Authorization'])
        return Resp(200, {"access_token": "tok", "expires_in": 3600})
    monkeypatch.setattr(mm.requests, "post", post)
    assert mm.get_app_token() == "tok" and mm.get_app_token() == "tok"
    assert len(calls) == 1 and calls[0].startswith("Basic ")


def test_find_track_by_uri_and_by_search(monkeypatch):
    monkeypatch.setattr(mm, "get_app_token", lambda: "tok")
    urls = []
    def get(url, params=None, headers=None, timeout=None):
        urls.append(url)
        if url.endswith("/tracks/abc"):
            return Resp(200, track("A", "T", "spotify:track:abc"))
        return Resp(200, {"tracks": {"items": [track("Tame Impala", "Borderline")]}})
    monkeypatch.setattr(mm.requests, "get", get)
    assert mm.find_track("spotify:track:abc")['uri'] == "spotify:track:abc"
    assert mm.find_track("tame impala borderline")['name'] == "Borderline"
    assert mm.find_track("completely different words") is None
    assert mm.find_track("") is None


def test_find_track_survives_errors(monkeypatch):
    monkeypatch.setattr(mm, "get_app_token", lambda: "tok")
    monkeypatch.setattr(mm.requests, "get", lambda *a, **k: Resp(500))
    assert mm.find_track("tame impala") is None
    def boom(*a, **k): raise OSError("net")
    monkeypatch.setattr(mm.requests, "get", boom)
    assert mm.find_track("tame impala") is None


def test_search_and_save_shared_writes_file(monkeypatch):
    monkeypatch.setattr(mm, "find_track", lambda q: track("A", "T", "spotify:track:abc"))
    seen = {}
    def fake_art(url, uri, art_dir, source='currently_playing', scancode_url=None):
        seen.update(source=source, scancode=scancode_url)
        return "/tmp/art.jpg"
    monkeypatch.setattr(mm, "download_and_create_album_art", fake_art)
    music = mm.search_and_save_shared("a t", "Amy")
    assert music['pushed_by'] == "Amy" and music['source'] == "shared"
    assert seen['source'] == 'shared' and "spotify:track:abc" in seen['scancode']
    assert json.load(open(mm.CURRENT_MUSIC_FILE))['track'] == "T"
    assert not os.path.exists(mm.CURRENT_MUSIC_FILE + ".tmp")
    monkeypatch.setattr(mm, "find_track", lambda q: None)
    assert mm.search_and_save_shared("zzz", "Amy") is None
    os.remove(mm.CURRENT_MUSIC_FILE)


def test_parse_monthly_listeners():
    assert mm.parse_monthly_listeners('<meta property="og:description" content="Artist · 3.6M monthly listeners."/>') == "3.6M"
    assert mm.parse_monthly_listeners('<div>1,234,567 monthly listeners</div>') == "1.2M"
    assert mm.parse_monthly_listeners('<html></html>') is None


def test_listener_lookup_failures_are_cached(monkeypatch):
    calls = []
    def get(*a, **k):
        calls.append(1)
        return Resp(503)
    monkeypatch.setattr(mm.requests, "get", get)
    assert mm.scrape_monthly_listeners("id1") is None
    assert mm.scrape_monthly_listeners("id1") is None
    assert len(calls) == 1


def test_update_music_display_priority(monkeypatch):
    shared = {'artist': 'S', 'track': 'shared', 'source': 'shared', 'listeners': '1K'}
    playing = {'artist': 'P', 'track': 'live', 'is_playing': True, 'source': 'currently_playing'}
    monkeypatch.setattr(mm, "load_current_music", lambda: shared)
    monkeypatch.setattr(mm, "get_currently_playing", lambda: playing)
    assert mm.update_music_display()['track'] == 'live'
    playing['is_playing'] = False
    assert mm.update_music_display()['track'] == 'shared'
    monkeypatch.setattr(mm, "load_current_music", lambda: None)
    assert mm.update_music_display()['track'] == 'live'
    monkeypatch.setattr(mm, "get_currently_playing", lambda: None)
    assert mm.update_music_display() is None


def test_currently_playing_handles_204_and_missing_tokens(monkeypatch):
    monkeypatch.setattr(mm, "load_tokens", lambda: None)
    assert mm.get_currently_playing() is None
    monkeypatch.setattr(mm, "load_tokens", lambda: {"access_token": "t"})
    monkeypatch.setattr(mm.requests, "get", lambda *a, **k: Resp(204))
    assert mm.get_currently_playing() is None
