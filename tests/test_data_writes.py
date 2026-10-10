"""Power-safe JSON writes, Spotify result validation and OAuth client ID.

No network: requests and the album-art downloader are replaced with fakes.
"""
import json
import os
import stat
import threading
import types

import pytest
import requests

import leeloo_album_art
import leeloo_fileutil
import leeloo_music_manager as mm
from leeloo_brain import LeelooBrain
from leeloo_messages import MessageManager


def _track(artist, name, uri="spotify:track:abc"):
    return {
        "artists": [{"name": artist}],
        "name": name,
        "uri": uri,
        "album": {"name": "Album", "images": [{"url": "http://img"}]},
    }


class FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def spotify(monkeypatch, tmp_path):
    """Fake Spotify search; returns a dict whose 'tracks' the test sets."""
    state = {"tracks": [], "queries": []}
    music_file = tmp_path / "current_music.json"

    def fake_post(url, **kw):
        return FakeResp(200, {"access_token": "cc"})

    def fake_get(url, params=None, **kw):
        state["queries"].append((params or {}).get("q"))
        return FakeResp(200, {"tracks": {"items": state["tracks"]}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(leeloo_album_art, "download_and_create_album_art",
                        lambda *a, **k: str(tmp_path / "art.png"))
    monkeypatch.setattr(mm, "CURRENT_MUSIC_FILE", str(music_file))
    monkeypatch.setattr(mm, "ALBUM_ART_DIR", str(tmp_path / "album_art"))
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_SECRET", "secret")
    state["music_file"] = music_file
    return state


@pytest.fixture
def brain():
    b = LeelooBrain.__new__(LeelooBrain)
    b._music_lock = threading.Lock()
    b.last_music_fetch = 123
    b.ws_client = types.SimpleNamespace(connected=False,
                                        config=types.SimpleNamespace(device_id="dev1"))
    return b


# -----------------------------------------------------------------------------
# Short-word queries ("U2"): validated fallback instead of a crash
# -----------------------------------------------------------------------------

async def test_display_only_short_query_matches_full_name(brain, spotify):
    spotify["tracks"] = [_track("Some Band", "U2 Tribute Medley", "spotify:track:t1"),
                         _track("U2", "One", "spotify:track:u2")]
    result = await brain._search_spotify_display_only("U2", pushed_by="amy")
    assert result is not None
    assert result["spotify_uri"] == "spotify:track:t1"  # first validated result
    assert json.loads(spotify["music_file"].read_text())["pushed_by"] == "amy"


async def test_display_only_short_query_rejects_unrelated(brain, spotify):
    spotify["tracks"] = [_track("Coldplay", "Yellow"), _track("Adele", "Hello")]
    assert await brain._search_spotify_display_only("U2") is None
    assert not spotify["music_file"].exists()


async def test_push_short_query_works(brain, spotify):
    spotify["tracks"] = [_track("Coldplay", "Yellow", "spotify:track:c"),
                         _track("U2", "With or Without You", "spotify:track:u2")]
    result = await brain._search_and_push_song("U2")
    assert result is not None
    assert result["artist"] == "U2"
    assert json.loads(spotify["music_file"].read_text())["spotify_uri"] == "spotify:track:u2"


def test_pick_track_rules():
    pick = LeelooBrain._pick_spotify_track
    tracks = [_track("Coldplay", "Yellow"), _track("U2", "Beautiful Day")]
    assert pick("u2", tracks)["name"] == "Beautiful Day"
    assert pick("by U2", tracks)["name"] == "Beautiful Day"
    assert pick("u", tracks) is None           # no whole-word match ("u" in "u2")
    assert pick("the", tracks) is None         # only filler words
    assert pick("yellow", tracks)["name"] == "Yellow"
    assert pick("beautiful day", tracks)["name"] == "Beautiful Day"
    assert pick("beautiful yellow", tracks) is None  # no bigram
    assert pick("radiohead", tracks) is None


# -----------------------------------------------------------------------------
# current_music.json: atomic write under the refresh path's lock
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["display_only", "push"])
async def test_current_music_written_atomically_under_music_lock(brain, spotify, monkeypatch, which):
    calls = []
    real = leeloo_fileutil.atomic_write_json

    def recording(path, data, **kw):
        calls.append((path, brain._music_lock.locked()))
        return real(path, data, **kw)
    monkeypatch.setattr(leeloo_fileutil, "atomic_write_json", recording)

    spotify["tracks"] = [_track("Radiohead", "Creep")]
    if which == "display_only":
        result = await brain._search_spotify_display_only("radiohead creep")
    else:
        result = await brain._search_and_push_song("radiohead creep")
    assert result is not None
    assert calls == [(str(spotify["music_file"]), True)]
    assert brain._music_lock.locked() is False
    assert brain.last_music_fetch == 0


# -----------------------------------------------------------------------------
# Music manager and message store writes
# -----------------------------------------------------------------------------

def test_save_tokens_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "spotify_tokens.json"
    monkeypatch.setattr(mm, "TOKENS_FILE", str(path))
    mm.save_tokens({"access_token": "a", "refresh_token": "r"})
    assert json.loads(path.read_text())["refresh_token"] == "r"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def _crash_mid_dump(monkeypatch):
    """json.dump that writes a fragment then dies (power cut stand-in)"""
    def broken_dump(obj, fp, **kw):
        fp.write("[{")
        raise OSError("power lost")
    monkeypatch.setattr(json, "dump", broken_dump)


def test_shared_music_listener_update_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "current_music.json"
    original = {"source": "shared", "artist": "U2", "track": "One", "timestamp": 1}
    path.write_text(json.dumps(original))
    monkeypatch.setattr(mm, "CURRENT_MUSIC_FILE", str(path))
    monkeypatch.setattr(mm, "get_currently_playing", lambda: None)
    monkeypatch.setattr(mm, "get_listeners_for_artist_name", lambda name: "9.9M")

    # Normal path: listeners persisted
    assert mm.update_music_display()["listeners"] == "9.9M"
    assert json.loads(path.read_text())["listeners"] == "9.9M"

    # Interrupted write: the previous file must survive intact
    path.write_text(json.dumps(original))
    _crash_mid_dump(monkeypatch)
    with pytest.raises(OSError):
        mm.update_music_display()
    assert json.loads(path.read_text()) == original


def test_messages_save_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "messages.json"
    mgr = MessageManager(storage_path=str(path))
    mgr.add_message("amy", "first")
    before = json.loads(path.read_text())
    assert [m["text"] for m in before] == ["first"]

    _crash_mid_dump(monkeypatch)
    mgr.add_message("amy", "second")  # _save logs the error
    assert json.loads(path.read_text()) == before
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".tmp_")]


# -----------------------------------------------------------------------------
# OAuth URL uses the same Spotify app as token refresh
# -----------------------------------------------------------------------------

def test_oauth_url_uses_env_client_id(brain, monkeypatch, capsys):
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_ID", "envclient123")
    url = brain._generate_spotify_oauth_url()
    assert "client_id=envclient123&" in url
    assert "state=dev1&" in url
    out = capsys.readouterr().out
    assert "envclient123" not in out  # logs the source, never the value


def test_oauth_url_falls_back_to_builtin_client_id(brain, monkeypatch):
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_ID", "")
    url = brain._generate_spotify_oauth_url()
    assert "client_id=f8c3c0120e694af283d7d7f7c2f67d4c&" in url
