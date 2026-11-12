"""Tests for leeloo_voice.VoiceManager — no microphone, no network.

arecord is replaced by a fake process whose stdout yields scripted PCM
chunks, and the Deepgram WebSocket by a fake socket that records what was
sent and yields scripted results.
"""
import asyncio
import json
import struct
import time
import warnings

import pytest

import leeloo_voice as lv

CHUNK = lv.CHUNK_BYTES            # 100 ms of audio
READ_DELAY = 0.002                # real seconds per fake 100 ms chunk


def loud(marker=1000):
    """One chunk of 'speech'; marker makes each chunk distinguishable."""
    return struct.pack("<h", marker) * (CHUNK // 2)


def silent():
    return b"\x00" * CHUNK


def result(text, is_final=True):
    return json.dumps({
        "type": "Results",
        "is_final": is_final,
        "channel": {"alternatives": [{"transcript": text}]},
    })


class FakeStdout:
    def __init__(self, chunks, then="eof", delay=READ_DELAY):
        self.chunks = list(chunks)
        self.then = then              # "eof" | "hang" | "silence"
        self.delay = delay
        self.reads = 0
        self.eof = False

    async def read(self, n):
        await asyncio.sleep(self.delay)
        if self.eof:
            return b""
        if self.chunks:
            self.reads += 1
            return self.chunks.pop(0)
        if self.then == "silence":
            self.reads += 1
            return silent()
        if self.then == "hang":
            await asyncio.sleep(3600)
        return b""


class FakeProcess:
    def __init__(self, chunks, then="eof"):
        self.stdout = FakeStdout(chunks, then)
        self.returncode = None
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.eof = True

    async def wait(self):
        self.waited = True
        return self.returncode


class FakeWS:
    """Records sends; yields `results` once CloseStream arrives."""
    _END = object()

    def __init__(self, results=(), early=()):
        self.sent = []
        self.results = list(results)
        self.closed = False
        self._inbox = asyncio.Queue()
        for msg in early:
            self._inbox.put_nowait(msg)

    @property
    def audio(self):
        return [m for m in self.sent if isinstance(m, (bytes, bytearray))]

    @property
    def close_stream_count(self):
        return sum(1 for m in self.sent
                   if isinstance(m, str) and json.loads(m).get("type") == "CloseStream")

    async def send(self, data):
        if self.closed:
            raise ConnectionError("socket closed")
        await asyncio.sleep(0)
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "CloseStream":
            for msg in self.results:
                self._inbox.put_nowait(msg)
            self._inbox.put_nowait(self._END)

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self._inbox.get()
        if msg is self._END:
            raise StopAsyncIteration
        return msg

    async def close(self):
        self.closed = True
        self._inbox.put_nowait(self._END)


class Harness:
    def __init__(self, monkeypatch, chunks, then="eof", results=(), early=(),
                 connect_delay=0.0, connect_error=None, spawn_error=None):
        self.process = FakeProcess(chunks, then)
        self.ws = FakeWS(results, early)
        self.spawn_args = None
        self.connect_args = None
        self.reads_at_connect = None

        async def fake_exec(*args, **kwargs):
            if spawn_error is not None:
                raise spawn_error
            self.spawn_args = args
            return self.process

        async def fake_connect(url, headers):
            self.connect_args = (url, headers)
            await asyncio.sleep(connect_delay)
            if connect_error is not None:
                raise connect_error
            self.reads_at_connect = self.process.stdout.reads
            return self.ws

        monkeypatch.setattr(lv.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(lv, "_ws_connect", fake_connect)
        monkeypatch.setattr(lv, "READ_TIMEOUT", 0.2)
        monkeypatch.setattr(lv, "CONNECT_TIMEOUT", 0.5)
        monkeypatch.setattr(lv, "SEND_DRAIN_TIMEOUT", 0.5)
        monkeypatch.setattr(lv, "FINAL_RESULT_WAIT", 0.3)
        monkeypatch.setattr(lv, "CLOSE_TIMEOUT", 0.3)
        self.voice = lv.VoiceManager("test-key")

    def run(self):
        return asyncio.run(self.voice.record_and_transcribe())


SPEECH_THEN_SILENCE = [loud()] * 10 + [silent()] * 15


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_audio_during_slow_connect_is_sent_in_order(monkeypatch):
    chunks = [loud(1000 + i) for i in range(30)] + [silent()] * 15
    h = Harness(monkeypatch, list(chunks), results=[result("hello")],
                connect_delay=0.05)

    assert h.run() == "hello"
    assert h.reads_at_connect > 3          # audio was captured while connecting
    assert h.ws.audio == chunks            # nothing lost, original order
    assert json.loads(h.ws.sent[-1]) == {"type": "CloseStream"}
    assert h.voice.last_error is None


def test_arecord_command_and_deepgram_auth_header(monkeypatch):
    h = Harness(monkeypatch, list(SPEECH_THEN_SILENCE), results=[result("hi")])
    h.run()

    assert h.spawn_args[0] == "arecord"
    assert lv.DEVICE in h.spawn_args and str(lv.SAMPLE_RATE) in h.spawn_args
    url, headers = h.connect_args
    assert url.startswith(lv.DEEPGRAM_WS_URL + "?")
    assert headers == {"Authorization": "Token test-key"}


def test_transcript_joined_from_finals(monkeypatch):
    h = Harness(
        monkeypatch, list(SPEECH_THEN_SILENCE),
        early=[result("share", is_final=False), result("share the song")],
        results=[result("", is_final=True), result("by radiohead")],
    )
    assert h.run() == "share the song by radiohead"
    assert h.voice.last_error is None


def test_interim_fallback_when_no_final(monkeypatch):
    h = Harness(
        monkeypatch, list(SPEECH_THEN_SILENCE),
        results=[result("what", is_final=False),
                 result("what time is it ", is_final=False)],
    )
    assert h.run() == "what time is it"
    assert h.voice.last_error is None


def test_stops_on_silence_after_speech(monkeypatch):
    # 1.0 s of speech, then endless silence: stop after SILENCE_DURATION (1.5 s)
    h = Harness(monkeypatch, [loud()] * 10, then="silence",
                results=[result("ok")])
    assert h.run() == "ok"
    assert len(h.ws.audio) == 25
    assert h.process.killed and h.process.waited
    assert h.ws.closed
    assert h.ws.close_stream_count == 1


def test_stops_on_no_speech(monkeypatch):
    h = Harness(monkeypatch, [], then="silence")
    assert h.run() == ""
    assert len(h.ws.audio) == int(lv.NO_SPEECH_TIMEOUT * 10)   # 5.0 s
    assert h.voice.last_error is None      # no speech is not an error
    assert h.process.killed
    assert h.ws.closed


def test_stops_at_max_duration(monkeypatch):
    monkeypatch.setattr(lv, "MAX_RECORD_SECONDS", 3)
    h = Harness(monkeypatch, [loud()] * 100, results=[result("long")])
    assert h.run() == "long"
    assert len(h.ws.audio) == 30
    assert h.process.killed


def test_cancel_method_stops_recording_and_returns_transcript(monkeypatch):
    h = Harness(monkeypatch, [loud()] * 1000, results=[result("so far")])

    async def scenario():
        task = asyncio.create_task(h.voice.record_and_transcribe())
        await asyncio.sleep(0.05)
        h.voice.cancel()
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) == "so far"
    assert 0 < len(h.ws.audio) < 1000
    assert h.process.killed
    assert h.voice.last_error is None


def test_last_error_reset_between_calls(monkeypatch):
    h = Harness(monkeypatch, [], connect_error=OSError("down"), then="silence")
    assert h.run() == ""
    assert h.voice.last_error == "network"

    h2 = Harness(monkeypatch, list(SPEECH_THEN_SILENCE), results=[result("back")])
    h2.voice = h.voice
    assert h2.run() == "back"
    assert h.voice.last_error is None


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

def test_connect_failure_is_network_and_kills_arecord(monkeypatch):
    h = Harness(monkeypatch, [], then="silence",
                connect_error=OSError("Network is unreachable"))
    start = time.monotonic()
    assert h.run() == ""
    assert time.monotonic() - start < 1.0      # stops quickly, not after 5 s
    assert h.voice.last_error == "network"
    assert h.process.killed and h.process.waited
    assert h.ws.sent == []


def test_connect_timeout_is_network(monkeypatch):
    h = Harness(monkeypatch, [loud()] * 2000, connect_delay=60)
    start = time.monotonic()
    assert h.run() == ""
    assert time.monotonic() - start < 2.0
    assert h.voice.last_error == "network"
    assert h.process.killed


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class NewStyleRejection(Exception):          # websockets >= 14: InvalidStatus
    def __init__(self, status):
        super().__init__(f"server rejected WebSocket connection: HTTP {status}")
        self.response = _Resp(status)


class LegacyRejection(Exception):            # websockets <= 13: InvalidStatusCode
    def __init__(self, status):
        super().__init__(f"server rejected WebSocket connection: HTTP {status}")
        self.status_code = status


@pytest.mark.parametrize("exc, expected", [
    (NewStyleRejection(401), "auth"),
    (LegacyRejection(401), "auth"),
    (NewStyleRejection(403), "auth"),
    (NewStyleRejection(503), "network"),
    (LegacyRejection(502), "network"),
])
def test_rejected_connection_classified(monkeypatch, exc, expected):
    h = Harness(monkeypatch, [], then="silence", connect_error=exc)
    assert h.run() == ""
    assert h.voice.last_error == expected
    assert h.process.killed


def test_real_websockets_exception_classes():
    exceptions = pytest.importorskip("websockets.exceptions")
    try:
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        new = exceptions.InvalidStatus(Response(401, "Unauthorized", Headers(), b""))
    except Exception:
        new = None
    if new is not None:
        assert lv._classify_connect_error(new) == "auth"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            legacy = exceptions.InvalidStatusCode(401, None)
    except Exception:
        legacy = None
    if legacy is not None:
        assert lv._classify_connect_error(legacy) == "auth"
    assert lv._classify_connect_error(OSError("dns")) == "network"
    assert lv._header_kwarg() in ("additional_headers", "extra_headers")


def test_missing_api_key_is_auth(monkeypatch):
    h = Harness(monkeypatch, [])
    h.voice = lv.VoiceManager("")
    assert h.run() == ""
    assert h.voice.last_error == "auth"
    assert h.spawn_args is None                # mic never opened


def test_send_failure_mid_stream_is_network(monkeypatch):
    h = Harness(monkeypatch, [loud()] * 2000)

    async def scenario():
        task = asyncio.create_task(h.voice.record_and_transcribe())
        await asyncio.sleep(0.05)
        h.ws.closed = True                      # Deepgram drops the socket
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) == ""
    assert h.voice.last_error == "network"
    assert h.process.killed


def test_arecord_produces_nothing_is_mic(monkeypatch):
    h = Harness(monkeypatch, [], then="eof")
    assert h.run() == ""
    assert h.voice.last_error == "mic"
    assert h.process.waited
    assert h.ws.audio == []
    assert h.ws.closed                          # socket not leaked


def test_arecord_hangs_without_audio_is_mic(monkeypatch):
    h = Harness(monkeypatch, [], then="hang")
    assert h.run() == ""
    assert h.voice.last_error == "mic"
    assert h.process.killed


def test_arecord_cannot_start_is_mic(monkeypatch):
    h = Harness(monkeypatch, [], spawn_error=FileNotFoundError("arecord"))
    assert h.run() == ""
    assert h.voice.last_error == "mic"
    assert h.connect_args is None               # never dialled Deepgram


# ---------------------------------------------------------------------------
# Task cancellation
# ---------------------------------------------------------------------------

def _cancel_after(h, delay):
    async def scenario():
        task = asyncio.create_task(h.voice.record_and_transcribe())
        await asyncio.sleep(delay)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.01)
        # no helper task left behind
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    return asyncio.run(scenario())


def test_task_cancel_while_streaming_kills_arecord_and_reraises(monkeypatch):
    h = Harness(monkeypatch, [loud()] * 2000)
    leftovers = _cancel_after(h, 0.05)

    assert leftovers == []
    assert h.process.killed and h.process.waited
    assert h.ws.closed
    assert h.ws.close_stream_count == 1
    assert h.voice._recording is False


def test_task_cancel_while_connecting_kills_arecord_and_reraises(monkeypatch):
    h = Harness(monkeypatch, [loud()] * 2000, connect_delay=60)
    leftovers = _cancel_after(h, 0.05)

    assert leftovers == []
    assert h.process.killed and h.process.waited
    assert h.ws.sent == []


def test_task_cancel_while_waiting_for_final_results(monkeypatch):
    h = Harness(monkeypatch, list(SPEECH_THEN_SILENCE))
    monkeypatch.setattr(lv, "FINAL_RESULT_WAIT", 30)

    async def never_answer(data):               # Deepgram goes quiet
        h.ws.sent.append(data)

    h.ws.send = never_answer
    leftovers = _cancel_after(h, 0.3)

    assert leftovers == []
    assert h.process.killed
    assert h.ws.closed


# ---------------------------------------------------------------------------
# RMS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", ["_compute_rms", "_compute_rms_array"])
def test_compute_rms(fn_name):
    fn = getattr(lv, fn_name)
    assert fn(b"") == 0
    assert fn(b"\x01") == 0
    assert fn(silent()) == 0
    assert fn(loud(1000)) == pytest.approx(1000, rel=1e-4)
    assert fn(loud(-32768)) == pytest.approx(32768, rel=1e-4)
    mixed = struct.pack("<4h", 3, -4, 3, -4)
    assert fn(mixed + b"\x7f") == pytest.approx((12.5) ** 0.5, rel=1e-4)  # odd tail ignored
