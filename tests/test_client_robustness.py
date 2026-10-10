"""Relay client robustness: malformed frames, callback errors, socket cleanup.

No network: the WebSocket is a fake that yields canned frames.
"""
import asyncio
import json
import os
import stat
import types

import pytest

from leeloo_brain import LeelooBrain
from leeloo_client import LeelooClient


class FakeSocket:
    """Async-iterable stand-in for a websockets connection"""

    def __init__(self, frames=(), close_error=None):
        self.frames = list(frames)
        self.close_calls = 0
        self.close_error = close_error
        self.sent = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for frame in self.frames:
            yield frame

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error


def make_client(tmp_path, frames=()):
    client = LeelooClient(relay_url="ws://unused", config_path=str(tmp_path / "crew_config.json"))
    client.websocket = FakeSocket(frames)
    client.connected = True
    return client


def msg(**fields):
    return json.dumps(dict({"type": "message"}, **fields))


# -----------------------------------------------------------------------------
# listen(): one bad frame must not end the loop
# -----------------------------------------------------------------------------

async def test_malformed_frames_do_not_drop_connection(tmp_path):
    frames = [
        msg(msg_type="text", from_name="Amy", payload=None),          # null payload
        "this is not json",                                           # non-JSON
        "[1, 2, 3]",                                                  # JSON, not an object
        msg(msg_type="text", from_name="Amy", payload="just a str"),  # non-dict payload
        msg(msg_type="text", from_name=None, payload={"text": "hi"}),  # null from_name
        msg(msg_type="song_push", from_name="Bo", payload=None),
        msg(msg_type="hang_propose", from_name=None, payload=[1]),
        json.dumps({"type": "member_joined", "display_name": None}),
        json.dumps({"type": "spotify_auth_complete", "tokens": None}),
        msg(msg_type="text", from_name="Cy", payload={"text": "still here"}),
    ]
    client = make_client(tmp_path, frames)
    got = []
    client.on_message = lambda name, text: got.append((name, text))
    client.on_song_push = lambda name, song: got.append((name, song))
    client.on_hang_propose = lambda name, dt, desc: got.append((name, dt, desc))
    client.on_member_joined = lambda name: got.append(("joined", name))

    await asyncio.wait_for(client.listen(), 2)

    # Every frame after the bad ones was still processed
    assert ("Cy", "still here") in got
    # Callbacks only ever see strings
    assert ("Amy", "") in got
    assert ("Someone", "hi") in got
    assert ("Someone", "", "") in got
    assert ("joined", "Someone") in got
    song = [g for g in got if g[0] == "Bo"][0][1]
    assert all(isinstance(song[k], str) for k in ("artist", "track", "note", "spotify_uri"))
    assert client.running is False


async def test_callback_exception_does_not_kill_listen(tmp_path):
    frames = [
        msg(msg_type="text", from_name="Amy", payload={"text": "boom"}),
        msg(msg_type="text", from_name="Amy", payload={"text": "after"}),
    ]
    client = make_client(tmp_path, frames)
    got = []

    def on_message(name, text):
        if text == "boom":
            raise RuntimeError("callback bug")
        got.append(text)
    client.on_message = on_message

    await asyncio.wait_for(client.listen(), 2)
    assert got == ["after"]


async def test_spotify_tokens_from_relay_saved_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LEELOO_HOME", str(tmp_path))
    tokens = {"access_token": "a", "refresh_token": "r"}
    client = make_client(tmp_path, [json.dumps({"type": "spotify_auth_complete", "tokens": tokens})])
    received = []
    client.on_spotify_auth = received.append

    await asyncio.wait_for(client.listen(), 2)

    path = tmp_path / "spotify_tokens.json"
    assert json.loads(path.read_text()) == tokens
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert received == [tokens]
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".tmp_")]


async def test_disconnect_clears_socket_even_if_close_fails(tmp_path):
    client = make_client(tmp_path)
    client.websocket.close_error = RuntimeError("already broken")
    await client.disconnect()
    assert client.websocket is None
    assert client.connected is False


# -----------------------------------------------------------------------------
# Brain callbacks tolerate None / empty / non-string relay data
# -----------------------------------------------------------------------------

class StubLED:
    async def _noop(self, *a, **k):
        return None

    def __getattr__(self, name):
        return self._noop


@pytest.fixture
def bare_brain():
    brain = LeelooBrain.__new__(LeelooBrain)
    brain.led = StubLED()
    brain.expansions = []
    brain._start_expansion = lambda frame, lines, duration=None: brain.expansions.append(lines)
    brain._pending_hang_dt = None
    brain.added = []
    brain.message_mgr = types.SimpleNamespace(add_message=lambda s, t: brain.added.append((s, t)))
    brain.handled = []

    async def handle(sender, text):
        brain.handled.append((sender, text))
    brain._handle_message_with_music = handle

    async def search(query, pushed_by=None):
        return None
    brain._search_spotify_display_only = search
    return brain


async def test_brain_callbacks_tolerate_none_strings(bare_brain):
    brain = bare_brain
    brain._on_ws_message(None, None)
    brain._on_ws_reaction(None, None)
    brain._on_ws_nudge(None)
    brain._on_ws_hang_propose(None, None, None)
    brain._on_ws_hang_confirm("")
    brain._on_ws_song_push(None, {"artist": None, "track": "Song", "note": None, "spotify_uri": None})
    brain._on_ws_song_push("Amy", None)  # no payload at all: ignored
    await asyncio.sleep(0.05)

    assert brain.added == [("someone", "")]
    assert brain.handled == [("someone", "")]
    texts = [line[0] for lines in brain.expansions for line in lines]
    assert "someone wants to" in texts
    assert "someone is in" in texts
    assert "someone shared" in texts
    assert brain._pending_hang_dt is None


# -----------------------------------------------------------------------------
# _ws_connection_loop closes the old socket before reconnecting
# -----------------------------------------------------------------------------

class FakeWSClient:
    def __init__(self):
        self.websocket = None
        self.connected = False
        self.connects = 0
        self.disconnects = 0

    async def connect(self):
        self.connects += 1
        self.websocket = FakeSocket()
        self.connected = True
        return True

    async def rejoin_crew(self):
        return True

    async def listen(self):
        return None  # relay dropped us (or listen ended on an error)

    async def keepalive(self, interval=30):
        await asyncio.sleep(3600)

    async def disconnect(self):
        self.disconnects += 1
        self.websocket = None
        self.connected = False


async def test_ws_connection_loop_closes_old_socket(monkeypatch):
    brain = LeelooBrain.__new__(LeelooBrain)
    brain.ws_client = FakeWSClient()
    brain.device_config = {}
    task = asyncio.ensure_future(brain._ws_connection_loop())
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        while brain.ws_client.disconnects == 0 and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert brain.ws_client.connects == 1
        assert brain.ws_client.disconnects == 1
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
