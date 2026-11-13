"""Tests for leeloo_client.LeelooClient against an in-process relay.

Plain pytest (no pytest-asyncio): each test drives its own asyncio.run().
"""
import asyncio
import json
import os

import pytest
import websockets

import leeloo_client
from leeloo_client import LeelooClient, CrewConfig


# ---------------------------------------------------------------------------
# Minimal relay
# ---------------------------------------------------------------------------

class Relay:
    """Speaks just enough of leeloo_server/server.js for the client."""

    def __init__(self):
        self.received = []          # decoded frames from the client
        self.sockets = []
        self.join_reply = None      # override: dict, or None for default
        self.join_extra = {}        # extra fields for crew_joined
        self.before_join_reply = [] # frames sent before the join reply
        self.after_join_reply = []  # raw frames (str) or dicts sent after
        self.silent = False         # never answer join/create
        self.close_after_join = False
        self.server = None
        self.url = None

    async def __aenter__(self):
        self.server = await websockets.serve(self._handler, "localhost", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @staticmethod
    async def _send(ws, frame):
        await ws.send(frame if isinstance(frame, str) else json.dumps(frame))

    async def push(self, frame):
        """Send a frame to every connected client"""
        for ws in self.sockets:
            await self._send(ws, frame)

    async def _handler(self, ws):
        self.sockets.append(ws)
        try:
            async for raw in ws:
                data = json.loads(raw)
                self.received.append(data)
                kind = data.get("type")
                if kind == "ping":
                    await self._send(ws, {"type": "pong"})
                elif kind in ("join_crew", "create_crew"):
                    if self.silent:
                        continue
                    for frame in self.before_join_reply:
                        await self._send(ws, frame)
                    if self.join_reply is not None:
                        reply = self.join_reply
                    elif kind == "join_crew":
                        reply = {
                            "type": "crew_joined",
                            "device_id": data.get("device_id") or "dev_test",
                            "crew_code": data.get("crew_code"),
                            "crew_members": 2,
                            "member_names": ["Ana"],
                        }
                        reply.update(self.join_extra)
                    else:
                        reply = {
                            "type": "crew_created",
                            "device_id": data.get("device_id") or "dev_test",
                            "crew_code": data.get("crew_code") or "NEWCODE",
                        }
                    await self._send(ws, reply)
                    for frame in self.after_join_reply:
                        await self._send(ws, frame)
                    if self.close_after_join:
                        await ws.close()
                        return
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if ws in self.sockets:
                self.sockets.remove(ws)


def relay_msg(msg_type, payload=None, from_name="Ana", **extra):
    msg = {
        "type": "message",
        "from_device": "dev_other",
        "from_name": from_name,
        "msg_type": msg_type,
        "payload": payload or {},
        "timestamp": 1700000000000,
    }
    msg.update(extra)
    return msg


async def wait_until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "crew_config.json")


class Recorder:
    """Attach list-recording callbacks to every client hook."""

    HOOKS = ["on_message", "on_reaction", "on_song_push", "on_member_joined",
             "on_member_offline", "on_nudge", "on_hang_propose",
             "on_hang_confirm", "on_connected", "on_disconnected",
             "on_crew_joined", "on_spotify_auth", "on_roster"]

    def __init__(self, client, queued=False):
        self.calls = []
        for hook in self.HOOKS:
            setattr(client, hook, self._make(hook))
        if queued:
            client.on_queued_message = self._make("on_queued_message")

    def _make(self, hook):
        def cb(*args):
            self.calls.append((hook, args))
        return cb

    def of(self, hook):
        return [args for name, args in self.calls if name == hook]


async def joined_client(relay, config_path, queued=False):
    client = LeelooClient(relay_url=relay.url, config_path=config_path)
    rec = Recorder(client, queued=queued)
    assert await client.connect() is True
    assert await client.join_crew("ABC123", "Nate") is True
    return client, rec


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_save_config_keeps_unknown_keys(config_path):
    portal = {
        "name": "Nate", "invite_code": "ABC123", "is_creator": True,
        "members": ["Ana", "Bo"], "custom": {"x": 1},
    }
    with open(config_path, "w") as f:
        json.dump(portal, f)

    client = LeelooClient(config_path=config_path)
    assert client.config.crew_code == "ABC123"
    assert client.config.display_name == "Nate"
    assert client.is_configured()

    client.config.device_id = "dev_1"
    client.config.crew_id = "ABC123"
    client._save_config()

    with open(config_path) as f:
        saved = json.load(f)
    for key, value in portal.items():
        assert saved[key] == value
    assert saved["device_id"] == "dev_1"
    assert saved["crew_id"] == "ABC123"
    assert saved["crew_code"] == "ABC123"
    assert saved["display_name"] == "Nate"
    # no temp files left behind
    assert os.listdir(os.path.dirname(config_path)) == ["crew_config.json"]

    # Old-format file with merged keys still loads
    again = LeelooClient(config_path=config_path)
    assert again.config == CrewConfig("dev_1", "ABC123", "ABC123", "Nate")


def test_load_new_format(config_path):
    with open(config_path, "w") as f:
        json.dump({"device_id": "d", "crew_id": "c", "crew_code": "XYZ",
                   "display_name": "Bo", "junk": 1}, f)
    client = LeelooClient(config_path=config_path)
    assert client.config == CrewConfig("d", "c", "XYZ", "Bo")


def test_load_missing_unrecognized_and_corrupt(config_path):
    assert not LeelooClient(config_path=config_path).is_configured()
    with open(config_path, "w") as f:
        json.dump({"something": 1}, f)
    assert not LeelooClient(config_path=config_path).is_configured()
    with open(config_path, "w") as f:
        f.write("{not json")
    client = LeelooClient(config_path=config_path)
    assert not client.is_configured()
    # saving over a corrupt file still works
    client.config.crew_code = "NEW"
    client._save_config()
    assert LeelooClient(config_path=config_path).config.crew_code == "NEW"


# ---------------------------------------------------------------------------
# Connect / join / create
# ---------------------------------------------------------------------------

def test_connect_failure_returns_false(config_path):
    async def run():
        client = LeelooClient(relay_url="ws://localhost:1", config_path=config_path)
        assert await client.connect() is False
        assert client.connected is False
        assert await client.send_message("x") is False
        assert await client.join_crew("ABC") is False
        assert await client.create_crew("Nate") is None
    asyncio.run(run())


def test_connect_without_websockets_module(config_path, monkeypatch):
    monkeypatch.setattr(leeloo_client, "websockets", None)

    async def run():
        client = LeelooClient(config_path=config_path)
        assert await client.connect() is False
        assert client.connected is False
    asyncio.run(run())


def test_join_success_populates_config_and_callbacks(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            assert client.connected is True
            assert rec.of("on_connected") == [()]
            assert client.config == CrewConfig("dev_test", "ABC123", "ABC123", "Nate")
            # old server: roster falls back to member_names
            assert rec.of("on_roster") == [(["Ana"],)]
            assert rec.of("on_member_joined") == [("Ana",)]
            assert rec.of("on_crew_joined") == [("ABC123",)]
            assert relay.received[0] == {
                "type": "join_crew", "crew_code": "ABC123",
                "device_id": None, "display_name": "Nate"}
            await client.disconnect()
        with open(config_path) as f:
            assert json.load(f)["device_id"] == "dev_test"
    asyncio.run(run())


def test_join_uses_all_member_names_for_roster(config_path):
    async def run():
        async with Relay() as relay:
            relay.join_extra = {"all_member_names": ["Ana", "Bo", "Cy (tg)"]}
            client, rec = await joined_client(relay, config_path)
            assert rec.of("on_roster") == [(["Ana", "Bo", "Cy (tg)"],)]
            assert rec.of("on_member_joined") == [("Ana",)]
            await client.disconnect()
    asyncio.run(run())


def test_join_dispatches_unrelated_message_first(config_path):
    async def run():
        async with Relay() as relay:
            relay.before_join_reply = [
                "garbage",
                relay_msg("text", {"text": "early"}),
                {"type": "member_offline", "display_name": "Bo"},
            ]
            client, rec = await joined_client(relay, config_path)
            assert rec.of("on_message") == [("Ana", "early")]
            assert rec.of("on_member_offline") == [("Bo",)]
            assert client.config.crew_code == "ABC123"
            await client.disconnect()
    asyncio.run(run())


def test_join_error_reply(config_path):
    async def run():
        async with Relay() as relay:
            relay.join_reply = {"type": "error", "error": "invalid_crew_code",
                                "message": "Crew NOPE not found"}
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            rec = Recorder(client)
            assert await client.connect()
            assert await client.join_crew("NOPE", "Nate") is False
            assert rec.of("on_crew_joined") == []
            assert client.connected is True      # socket is still usable
            assert not os.path.exists(config_path)
            await client.disconnect()
    asyncio.run(run())


def test_join_and_create_timeout(config_path, monkeypatch):
    monkeypatch.setattr(LeelooClient, "REPLY_TIMEOUT", 0.15)

    async def run():
        async with Relay() as relay:
            relay.silent = True
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            assert await client.connect()
            assert await client.join_crew("ABC123") is False       # class default
            assert await client.create_crew("Nate", timeout=0.05) is None
            assert await client.join_crew("ABC123", timeout=0.05) is False
            await client.disconnect()
    asyncio.run(run())


def test_join_when_server_closes_socket(config_path):
    async def run():
        async with Relay() as relay:
            relay.silent = True
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            rec = Recorder(client)
            assert await client.connect()
            task = asyncio.create_task(client.join_crew("ABC123"))
            await wait_until(lambda: relay.received)
            await relay.sockets[0].close()
            assert await asyncio.wait_for(task, 3) is False
            assert client.connected is False
            assert rec.of("on_disconnected") == [()]
    asyncio.run(run())


def test_create_crew(config_path):
    async def run():
        async with Relay() as relay:
            with open(config_path, "w") as f:
                json.dump({"name": "Nate", "invite_code": "PORTAL1",
                           "is_creator": True}, f)
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            rec = Recorder(client)
            assert await client.connect()
            assert await client.create_crew("Nate") == "PORTAL1"
            assert relay.received[0].get("crew_code") == "PORTAL1"
            assert client.config.device_id == "dev_test"
            assert client.config.crew_id == "PORTAL1"
            assert rec.of("on_crew_joined") == [("PORTAL1",)]
            await client.disconnect()
        with open(config_path) as f:
            saved = json.load(f)
        assert saved["is_creator"] is True and saved["crew_code"] == "PORTAL1"
    asyncio.run(run())


def test_create_crew_error(config_path):
    async def run():
        async with Relay() as relay:
            relay.join_reply = {"type": "error", "message": "nope"}
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            assert await client.connect()
            assert await client.create_crew("Nate") is None
            await client.disconnect()
    asyncio.run(run())


def test_reconnect_closes_previous_socket(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            first = client.websocket
            assert await client.connect() is True
            assert client.websocket is not first
            assert client.connected is True
            assert rec.of("on_disconnected") == [()]
            await wait_until(lambda: len(relay.sockets) == 1)
            await client.disconnect()
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Listening
# ---------------------------------------------------------------------------

def test_live_callbacks(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            listen = asyncio.create_task(client.listen())
            song = {"artist": "Low", "track": "Lullaby", "spotify_uri": "spotify:track:1"}
            for frame in [
                relay_msg("text", {"text": "hi"}),
                relay_msg("reaction", {"reaction": "fire"}),
                relay_msg("song_push", song),
                relay_msg("nudge"),
                relay_msg("hang_propose", {"datetime": "2026-01-01T19:00", "description": "pizza"}),
                relay_msg("hang_confirm"),
                {"type": "member_joined", "display_name": "Bo"},
                {"type": "member_offline", "display_name": "Bo"},
                {"type": "pong"},
            ]:
                await relay.push(frame)
            await wait_until(lambda: rec.of("on_member_offline"))

            assert rec.of("on_message") == [("Ana", "hi")]
            assert rec.of("on_reaction") == [("Ana", "fire")]
            assert rec.of("on_song_push") == [("Ana", song)]
            assert rec.of("on_nudge") == [("Ana",)]
            assert rec.of("on_hang_propose") == [("Ana", "2026-01-01T19:00", "pizza")]
            assert rec.of("on_hang_confirm") == [("Ana",)]
            assert rec.of("on_member_joined") == [("Ana",), ("Bo",)]
            assert rec.of("on_member_offline") == [("Bo",)]
            assert client.running is True

            await client.disconnect()
            await asyncio.wait_for(listen, 3)
            assert client.running is False
    asyncio.run(run())


def test_queued_message_goes_to_queued_callback(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path, queued=True)
            listen = asyncio.create_task(client.listen())
            await relay.push(relay_msg("text", {"text": "while you slept"},
                                       queued=True, timestamp=1700000123456))
            await relay.push(relay_msg("nudge", queued=True))
            await relay.push(relay_msg("text", {"text": "live"}))
            await wait_until(lambda: rec.of("on_message"))

            queued = rec.of("on_queued_message")
            assert queued[0][:3] == ("Ana", "text", {"text": "while you slept"})
            assert queued[0][3] == pytest.approx(1700000123.456)
            assert queued[1][:3] == ("Ana", "nudge", {})
            assert rec.of("on_nudge") == []
            assert rec.of("on_message") == [("Ana", "live")]

            await client.disconnect()
            await listen
    asyncio.run(run())


def test_queued_message_falls_back_to_live_path(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)  # no queued hook
            listen = asyncio.create_task(client.listen())
            await relay.push(relay_msg("text", {"text": "old"}, queued=True))
            await wait_until(lambda: rec.of("on_message"))
            assert rec.of("on_message") == [("Ana", "old")]
            await client.disconnect()
            await listen
    asyncio.run(run())


def test_malformed_frames_and_raising_callback_do_not_stop_listen(config_path, capsys):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)

            def boom(name):
                raise RuntimeError("ui exploded")
            client.on_nudge = boom

            listen = asyncio.create_task(client.listen())
            for frame in ["not json at all", "[1, 2, 3]", "42", "null",
                          json.dumps({"type": "message", "payload": "oops",
                                      "msg_type": "song_push"}),
                          relay_msg("nudge"),
                          relay_msg("text", {"text": "still here"})]:
                await relay.push(frame)
            await wait_until(lambda: rec.of("on_message"))

            assert rec.of("on_message") == [("Ana", "still here")]
            assert rec.of("on_song_push") == [("Someone", {})]
            assert not listen.done()
            assert client.connected is True
            await client.disconnect()
            await listen
    asyncio.run(run())
    assert "ui exploded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Disconnects and sends
# ---------------------------------------------------------------------------

def test_server_close_marks_disconnected_once(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            listen = asyncio.create_task(client.listen())
            keepalive = asyncio.create_task(client.keepalive(interval=0.02))
            await wait_until(lambda: any(m.get("type") == "ping" for m in relay.received))

            await relay.sockets[0].close()      # normal close from server
            await asyncio.wait_for(listen, 3)
            await asyncio.wait_for(keepalive, 3)

            assert client.connected is False
            assert client.running is False
            assert rec.of("on_disconnected") == [()]

            # every send path: False, no exception, no extra callback
            assert await client.send_message("x") is False
            assert await client.send_reaction("love") is False
            assert await client.push_song("spotify:track:1", "a", "t") is False
            assert await client.send_nudge() is False
            assert await client.send_hang_propose("tomorrow", "pizza") is False
            assert await client.send_hang_confirm() is False
            await client.disconnect()
            assert rec.of("on_disconnected") == [()]
    asyncio.run(run())


def test_send_on_closed_socket_before_listen_notices(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            await client.websocket.close()      # socket dies, flag not yet updated
            assert client.connected is True
            assert await client.send_message("x") is False
            assert client.connected is False
            assert rec.of("on_disconnected") == [()]
            assert await client.send_nudge() is False
            assert rec.of("on_disconnected") == [()]
    asyncio.run(run())


def test_send_error_from_socket_does_not_raise(config_path):
    class BrokenSocket:
        closed = False

        async def send(self, frame):
            raise OSError("network unreachable")

        async def close(self):
            self.closed = True

    async def run():
        client = LeelooClient(config_path=config_path)
        rec = Recorder(client)
        client.config.crew_id = "ABC"
        client.websocket = sock = BrokenSocket()
        client.connected = True
        assert await client.send_message("x") is False
        assert client.connected is False
        assert sock.closed is True
        assert rec.of("on_disconnected") == [()]
    asyncio.run(run())


def test_sends_reach_server(config_path):
    async def run():
        async with Relay() as relay:
            client, _ = await joined_client(relay, config_path)
            assert await client.send_message("hello") is True
            assert await client.send_reaction("fire") is True
            assert await client.push_song("spotify:track:1", "Low", "Lullaby",
                                          note="listen") is True
            assert await client.send_nudge() is True
            assert await client.send_hang_propose("2026-01-01T19:00", "pizza") is True
            assert await client.send_hang_confirm() is True
            await wait_until(lambda: len(relay.received) == 7)
            sent = relay.received[1:]
            assert [m.get("msg_type") for m in sent] == [
                "text", "reaction", "song_push", "nudge", "hang_propose", "hang_confirm"]
            assert all(m.get("type") == "message" for m in sent)
            assert sent[0]["payload"] == {"text": "hello"}
            assert sent[1]["payload"] == {"reaction": "fire", "to_device": None}
            assert sent[2]["payload"]["spotify_uri"] == "spotify:track:1"
            assert sent[2]["payload"]["note"] == "listen"
            assert sent[4]["payload"] == {"datetime": "2026-01-01T19:00",
                                          "description": "pizza"}
            await client.disconnect()
    asyncio.run(run())


def test_send_without_crew_returns_false(config_path):
    async def run():
        async with Relay() as relay:
            client = LeelooClient(relay_url=relay.url, config_path=config_path)
            assert await client.connect()
            assert await client.send_message("x") is False   # no crew yet
            assert client.connected is True
            await client.disconnect()
    asyncio.run(run())


def test_disconnect_fires_callback_once(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            listen = asyncio.create_task(client.listen())
            await asyncio.sleep(0.05)
            await client.disconnect()
            await asyncio.wait_for(listen, 3)
            await client.disconnect()
            assert client.connected is False
            assert client.websocket is None
            assert rec.of("on_disconnected") == [()]
    asyncio.run(run())


def test_cancelled_listen_marks_disconnected(config_path):
    async def run():
        async with Relay() as relay:
            client, rec = await joined_client(relay, config_path)
            listen = asyncio.create_task(client.listen())
            await asyncio.sleep(0.05)
            listen.cancel()
            with pytest.raises(asyncio.CancelledError):
                await listen
            assert client.connected is False
            assert rec.of("on_disconnected") == [()]
            await client.disconnect()
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Spotify tokens
# ---------------------------------------------------------------------------

def test_spotify_tokens_written(config_path, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LEELOO_HOME", str(home))
    tokens = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    async def run():
        async with Relay() as relay:
            # server delivers pending tokens right after the join reply
            relay.after_join_reply = [
                {"type": "spotify_auth_complete", "tokens": {}},       # ignored
                {"type": "spotify_auth_complete", "tokens": tokens},
            ]
            client, rec = await joined_client(relay, config_path)
            listen = asyncio.create_task(client.listen())
            await wait_until(lambda: rec.of("on_spotify_auth"))
            assert rec.of("on_spotify_auth") == [(tokens,)]
            await client.disconnect()
            await listen
    asyncio.run(run())

    with open(home / "spotify_tokens.json") as f:
        assert json.load(f) == tokens
    assert os.listdir(home) == ["spotify_tokens.json"]


def test_pack_and_agent_messages_reach_their_callbacks():
    """New message types from docs/PACKS.md."""
    import leeloo_client
    c = leeloo_client.LeelooClient(relay_url="ws://unused", config_path=os.path.join(os.environ["LEELOO_HOME"], "crew_packs.json"))
    seen = []
    c.on_pack_offer = lambda who, pack: seen.append(("offer", who, pack["id"]))
    c.on_pack_install = lambda pack: seen.append(("install", pack["id"]))
    c.on_pack_reset = lambda: seen.append(("reset",))
    c.on_pack_data = lambda pid, values: seen.append(("data", pid, values))
    c.on_agent_connect = lambda code, url, exp: seen.append(("connect", code, url, exp))
    c.on_agent_disconnected = lambda n: seen.append(("gone", n))

    async def go():
        for msg in [
            {"type": "message", "msg_type": "pack_offer", "from_name": "Amy", "payload": {"pack": {"id": "p1"}}},
            {"type": "message", "msg_type": "pack_offer", "from_name": "Amy", "payload": {"pack": "not a dict"}},
            {"type": "pack_install", "pack": {"id": "p2"}},
            {"type": "pack_install"},
            {"type": "pack_reset"},
            {"type": "pack_data", "pack_id": "p2", "values": {"a.b": "1"}},
            {"type": "pack_data", "pack_id": "p2", "values": "bad"},
            {"type": "agent_connect", "code": "ABCD2345", "url": "https://x/connect/ABCD2345", "expires_in": 900},
            {"type": "agent_connect", "code": 5},
            {"type": "agent_disconnected", "removed": 2},
        ]:
            await c._handle_message(msg)
    asyncio.run(go())
    assert seen == [("offer", "Amy", "p1"), ("install", "p2"), ("reset",), ("data", "p2", {"a.b": "1"}),
                    ("connect", "ABCD2345", "https://x/connect/ABCD2345", 900), ("gone", 2)]
