"""Regression tests for the last low-severity review findings."""
import asyncio
import json
import os
import warnings

from leeloo_client import LeelooClient
from leeloo_fileutil import atomic_write_json
from leeloo_led import LEDManager
from test_client_robustness import FakeSocket


async def test_deeply_nested_frame_does_not_drop_connection(tmp_path):
    """RecursionError from json.loads (valid but very deep JSON) is one bad
    frame, not a reason to end listen()."""
    deep = "[" * 100000 + "]" * 100000
    good = json.dumps({"type": "message", "msg_type": "text", "from_name": "Cy",
                       "payload": {"text": "still here"}})
    client = LeelooClient(relay_url="ws://unused", config_path=str(tmp_path / "crew.json"))
    client.websocket = FakeSocket([deep, good])
    client.connected = True
    got = []
    client.on_message = lambda name, text: got.append((name, text))

    await asyncio.wait_for(client.listen(), 5)

    assert ("Cy", "still here") in got


async def test_cancelled_run_animation_closes_its_coroutine():
    """If the caller is cancelled while the old animation is being cancelled,
    the new animation coroutine is closed (no 'never awaited' warning) and
    the cancellation propagates."""
    led = LEDManager(num_leds=3)
    led.off_sync()

    async def old_anim():
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0.05)  # slow cleanup keeps the caller waiting

    first = asyncio.ensure_future(led._run_animation("old", old_anim()))
    await asyncio.sleep(0.01)

    started = []

    async def new_anim():
        started.append(True)

    coro = new_anim()
    second = asyncio.ensure_future(led._run_animation("new", coro))
    await asyncio.sleep(0.01)  # second is now waiting for old's cleanup
    second.cancel()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            await second
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("caller's cancellation was swallowed")
    assert coro.cr_frame is None  # closed, never left dangling
    assert not started
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


def test_non_durable_write_skips_fsync(tmp_path, monkeypatch):
    calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd)))
    p = tmp_path / "m.json"
    atomic_write_json(str(p), [1], durable=False)
    assert json.loads(p.read_text()) == [1]
    assert calls == []
    atomic_write_json(str(p), [2])
    assert len(calls) >= 1


def test_message_manager_saves_without_fsync(tmp_path, monkeypatch):
    from leeloo_messages import MessageManager
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    mgr = MessageManager(storage_path=str(tmp_path / "messages.json"))
    mgr.add_message("amy", "hi")
    assert calls == []
    assert json.loads((tmp_path / "messages.json").read_text())


def test_client_config_saved_atomically(tmp_path, monkeypatch):
    import leeloo_client
    calls = []
    real = leeloo_client.atomic_write_json
    monkeypatch.setattr(leeloo_client, "atomic_write_json",
                        lambda path, data, **kw: (calls.append(path), real(path, data, **kw)))
    path = tmp_path / "sub" / "crew_config.json"
    client = LeelooClient(relay_url="ws://unused", config_path=str(path))
    client.config.crew_code = "LEELOO-AB12"
    client._save_config()
    assert calls == [str(path)]
    assert json.loads(path.read_text())["crew_code"] == "LEELOO-AB12"


def test_geocode_writes_device_config_atomically():
    import ast
    import inspect
    import geocode_zip
    src = inspect.getsource(geocode_zip)
    tree = ast.parse(src)
    writes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
              and len(n.args) > 1 and getattr(n.args[1], "value", "") == "w"]
    assert writes == [], "geocode_zip must not write device_config.json in place"
    assert "atomic_write_json(DEVICE_CONFIG_PATH" in src
