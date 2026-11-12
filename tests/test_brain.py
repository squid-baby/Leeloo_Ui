"""Integration tests for LeelooBrain with fake hardware and fake services.

They protect against the defects found in the November 2026 review:
frozen UI after some voice commands, overlapping frames, false "sent"
messages, invented weather, blocking refresh, exit code 0 after a crash.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta

import pytest

import leeloo_brain as brain_mod
from leeloo_brain import LeelooBrain, UIState, FrameType
from leeloo_intent import Intent
from display.frame_animator import FrameAnimator


class FakeVoice:
    def __init__(self, transcript="hello", delay=0.05):
        self.transcript, self.delay, self.last_error = transcript, delay, None
        self.calls = 0

    async def record_and_transcribe(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.transcript


class FakeRouter:
    client = None

    def __init__(self, intent):
        self.intent = intent

    async def route(self, transcript):
        return self.intent


class FakeClient:
    """Stands in for LeelooClient."""
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []
        class Cfg: device_id = "dev1"; display_name = "me"
        self.config = Cfg()

    def is_configured(self): return True
    async def _send(self, *a):
        if not self.connected:
            return False
        self.sent.append(a); return True
    async def send_message(self, text): return await self._send('text', text)
    async def send_nudge(self): return await self._send('nudge')
    async def send_reaction(self, r): return await self._send('reaction', r)
    async def send_hang_propose(self, dt, desc=""): return await self._send('hang_propose', dt, desc)
    async def send_hang_confirm(self): return await self._send('hang_confirm')
    async def push_song(self, uri, *a, **k): return await self._send('song', uri)
    async def disconnect(self): self.connected = False


@pytest.fixture
def brain(tmp_path, monkeypatch):
    fb = tmp_path / "fb1"
    fb.write_bytes(b"\0" * (480 * 320 * 2))
    monkeypatch.setattr(brain_mod, "FB_PATH", str(fb))
    monkeypatch.setattr(brain_mod, "TYPE_CHAR_DELAY", 0)
    monkeypatch.setattr(brain_mod, "TYPE_LINE_DELAY", 0)
    monkeypatch.setattr(FrameAnimator, "FPS", 500)
    monkeypatch.setattr(FrameAnimator, "FRAME_COUNT", 2)
    # No network in tests
    monkeypatch.setattr(brain_mod.music_manager, "update_music_display", lambda: None)
    monkeypatch.setattr(brain_mod, "get_weather", lambda *a, **k: None)
    # Fresh data files for every test
    for name in ("messages.json", "crew_config.json", "device_config.json", "leeloo_data.json"):
        try:
            os.remove(os.path.join(os.environ["LEELOO_HOME"], name))
        except FileNotFoundError:
            pass
    b = LeelooBrain()
    b.frame_hold = 0.05          # tests shorten every frame to this hold time
    b.ws_client = FakeClient()
    b.voice = FakeVoice()
    b.intent_router = FakeRouter(Intent("UNKNOWN", {}, "huh"))
    b.frames = []
    real_expand = b.expand_frame
    b.active_frames = 0
    b.max_active_frames = 0

    async def spy(frame_type, lines, duration=20.0):
        b.frames.append((frame_type, [l[0] for l in lines]))
        return await real_expand(frame_type, lines, min(duration, b.frame_hold))

    async def counted_typewriter(lines, box_right, _orig=b._typewriter):
        b.active_frames += 1
        b.max_active_frames = max(b.max_active_frames, b.active_frames)
        try:
            return await _orig(lines, box_right)
        finally:
            b.active_frames -= 1

    b.expand_frame = spy
    b._typewriter = counted_typewriter
    return b


async def settle(b, timeout=5.0):
    """Wait until no frame and no voice session is active."""
    end = time.time() + timeout
    await asyncio.sleep(0.02)
    while time.time() < end:
        if not b._frame_active() and not b._voice_busy and b.ui_state == UIState.NORMAL:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"brain did not settle: state={b.ui_state} busy={b._voice_busy}")


def frame_text(b):
    return " | ".join(" ".join(lines) for _t, lines in b.frames)


def run_voice(b, intent):
    b.intent_router = FakeRouter(intent)

    async def go():
        await b._on_tap('single_tap')
        await settle(b)
    asyncio.run(go())


# --- UI can never stay frozen -------------------------------------------------

@pytest.mark.parametrize("intent", [
    Intent("NUDGE", {}, ""),
    Intent("UNKNOWN", {}, ""),
    Intent("MESSAGE_READOUT", {}, ""),
    Intent("HANG_CONFIRM", {}, ""),
    Intent("SOMETHING_NEW", {}, ""),
    Intent("MESSAGE_SEND", {}, ""),
    Intent("SONG_PUSH", {"current": True}, ""),
    Intent("HANG_PROPOSE", {"datetime": "not a date"}, ""),
])
def test_every_intent_returns_to_normal_and_gives_feedback(brain, intent):
    run_voice(brain, intent)
    assert brain.ui_state == UIState.NORMAL
    assert not brain._voice_busy
    assert brain.frames, "the user must see a result for every command"


def test_empty_transcript_returns_to_normal(brain):
    brain.voice = FakeVoice(transcript="")
    run_voice(brain, Intent("NUDGE"))
    assert brain.ui_state == UIState.NORMAL and not brain.frames


def test_voice_fault_is_explained_on_screen(brain):
    brain.voice = FakeVoice(transcript="")
    brain.voice.last_error = "network"
    run_voice(brain, Intent("NUDGE"))
    assert "can't hear you" in frame_text(brain)


def test_router_exception_returns_to_normal(brain):
    class Boom(FakeRouter):
        async def route(self, t): raise RuntimeError("boom")
    brain.intent_router = Boom(None)

    async def go():
        await brain._on_tap('single_tap')
        await settle(brain)
    asyncio.run(go())
    assert brain.ui_state == UIState.NORMAL


def test_cancelled_voice_session_returns_to_normal(brain):
    brain.voice = FakeVoice(delay=5)

    async def go():
        task = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.3)
        assert brain.ui_state == UIState.LISTENING
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await settle(brain)
    asyncio.run(go())
    assert brain.ui_state == UIState.NORMAL and not brain._voice_busy


def test_second_tap_during_voice_is_ignored(brain):
    brain.voice = FakeVoice(delay=0.4)

    async def go():
        first = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.25)
        await brain._on_tap('single_tap')      # must not start a second session
        await brain._on_tap('double_tap')      # must not send a nudge
        await first
        await settle(brain)
    asyncio.run(go())
    assert brain.voice.calls == 1
    assert ('nudge',) not in brain.ws_client.sent


# --- Frames --------------------------------------------------------------------

def test_new_frame_replaces_old_and_never_overlaps(brain):
    async def go():
        brain._on_ws_message("Amy", "one")
        await asyncio.sleep(0.02)
        brain._on_ws_message("Ben", "two")
        brain._on_ws_message("Cat", "three")
        await settle(brain)
    asyncio.run(go())
    assert brain.max_active_frames == 1
    assert brain.ui_state == UIState.NORMAL
    assert "from cat" in frame_text(brain)
    assert brain.message_mgr.get_unread_counts() == {"Amy": 1, "Ben": 1, "Cat": 1}


def test_tap_dismisses_open_frame_without_starting_voice(brain):
    brain.frame_hold = 30

    async def go():
        brain.show_frame(FrameType.WEATHER, [("hi", "normal", "#fff")], duration=30)
        await asyncio.sleep(0.05)
        await brain._on_tap('single_tap')
        await settle(brain)
    asyncio.run(go())
    assert brain.voice.calls == 0


def test_message_during_voice_is_deferred_not_drawn(brain):
    brain.voice = FakeVoice(transcript="", delay=0.3)

    async def go():
        tap = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.2)
        brain._on_ws_message("Amy", "hello there")
        assert not brain.frames            # nothing drawn while recording
        await tap
        await settle(brain)
    asyncio.run(go())
    assert "from amy" in frame_text(brain)


def test_message_view_reactions(brain):
    brain.frame_hold = 30

    async def go():
        brain.show_frame(FrameType.MESSAGES, [("x", "normal", "#fff")], duration=30)
        await asyncio.sleep(0.1)
        assert brain.message_view_active
        await brain._on_tap('double_tap')
        await brain._on_tap('triple_tap')
        brain.dismiss_frame()
        await settle(brain)
        await brain._on_tap('triple_tap')      # outside message view: nothing
    asyncio.run(go())
    assert brain.ws_client.sent == [('reaction', 'love'), ('reaction', 'fire')]


# --- Honest status ---------------------------------------------------------------

def test_message_send_online(brain):
    run_voice(brain, Intent("MESSAGE_SEND", {"message": "miss you"}, ""))
    assert ('text', 'miss you') in brain.ws_client.sent
    assert "message sent" in frame_text(brain)


@pytest.mark.parametrize("intent,expected", [
    (Intent("MESSAGE_SEND", {"message": "miss you"}, ""), "message not sent"),
    (Intent("NUDGE", {}, ""), "nudge not sent"),
    (Intent("HANG_PROPOSE", {"datetime": "2031-05-01T19:00:00"}, ""), "hang not sent"),
])
def test_offline_actions_say_not_sent(brain, intent, expected):
    brain.ws_client.connected = False
    run_voice(brain, intent)
    text = frame_text(brain)
    assert expected in text
    assert "message sent" not in text and "hang proposed" not in text


def test_song_push_not_found_and_found(brain, monkeypatch):
    monkeypatch.setattr(brain_mod.music_manager, "search_and_save_shared", lambda q, by: None)
    run_voice(brain, Intent("SONG_PUSH", {"query": "zzz"}, ""))
    assert "couldn't find it" in frame_text(brain)

    brain.frames.clear()
    music = {'spotify_uri': 'spotify:track:1', 'artist': 'A', 'track': 'T', 'album': 'L'}
    monkeypatch.setattr(brain_mod.music_manager, "search_and_save_shared", lambda q, by: music)
    run_voice(brain, Intent("SONG_PUSH", {"query": "a t"}, ""))
    assert ('song', 'spotify:track:1') in brain.ws_client.sent
    assert "song shared!" in frame_text(brain)

    brain.frames.clear()
    brain.ws_client.connected = False
    run_voice(brain, Intent("SONG_PUSH", {"query": "a t"}, ""))
    assert "not shared" in frame_text(brain)


# --- Hangs ---------------------------------------------------------------------

def test_hang_propose_then_remote_confirm_saves_hang(brain):
    when = (datetime.now() + timedelta(days=3)).replace(microsecond=0)
    run_voice(brain, Intent("HANG_PROPOSE", {"datetime": when.isoformat(), "description": "pizza"}, "sat?"))
    kind, sent_dt, desc = brain.ws_client.sent[-1]
    assert kind == 'hang_propose' and desc == "pizza"
    assert brain._pending_hang_dt

    async def go():
        brain._on_ws_hang_confirm("Amy")
        await settle(brain)
    asyncio.run(go())
    hang = brain_mod.gadget_data.get_next_hang()
    assert hang and datetime.fromisoformat(hang['datetime']).replace(tzinfo=None) == when
    assert brain._pending_hang_dt is None
    brain_mod.gadget_data.clear_next_hang()


def test_hang_in_the_past_moves_to_the_future(brain):
    run_voice(brain, Intent("HANG_PROPOSE", {"datetime": "2024-03-27T19:00:00"}, ""))
    sent = datetime.fromisoformat(brain.ws_client.sent[-1][1])
    assert sent.replace(tzinfo=None) > datetime.now()


def test_incoming_proposal_then_voice_confirm(brain):
    when = (datetime.now() + timedelta(days=2)).replace(microsecond=0)

    async def go():
        brain._on_ws_hang_propose("Amy", when.isoformat(), "movie")
        await settle(brain)
    asyncio.run(go())
    assert "amy wants to" in frame_text(brain)
    run_voice(brain, Intent("HANG_CONFIRM", {}, "you're in!"))
    assert ('hang_confirm',) in brain.ws_client.sent
    assert brain_mod.gadget_data.get_next_hang()
    brain_mod.gadget_data.clear_next_hang()


# --- Queued (offline) messages ---------------------------------------------------

def test_queued_messages_are_stored_and_summarised_once(brain, monkeypatch):
    async def fast_summary(_orig=brain._show_queued_summary):
        real_sleep = asyncio.sleep
        monkeypatch.setattr(brain_mod.asyncio, "sleep", lambda s: real_sleep(min(s, 0.05)))
        await _orig()

    async def go():
        brain._show_queued_summary = fast_summary
        for i in range(3):
            brain._on_ws_queued("Amy", "text", {"text": f"m{i}"}, time.time() - 60)
        brain._on_ws_queued("Amy", "nudge", {}, time.time())      # ignored
        await asyncio.sleep(0.3)
        await settle(brain)
    asyncio.run(go())
    assert brain.message_mgr.get_unread_counts() == {"Amy": 3}
    assert len(brain.frames) == 1 and "3 new messages" in frame_text(brain)


# --- Contacts --------------------------------------------------------------------

def test_contacts_persist_and_survive_member_offline(brain):
    brain._on_roster(["Amy", "Ben", brain.user_name or "me"])
    brain._on_member_joined("Cat")
    brain._on_member_offline("Amy")
    assert "Amy" in brain.contacts and "Cat" in brain.contacts
    saved = brain_mod.load_json(brain_mod.CREW_CONFIG_PATH)
    assert "Cat" in saved['members']
    # The intent context must work with string contacts (old crash)
    assert "Amy" in brain._get_intent_context()


# --- Rendering and data ------------------------------------------------------------

def test_render_only_when_something_changed(brain):
    assert brain._render_normal() is True
    assert brain._render_normal() is False
    brain.message_mgr.add_message("Amy", "x")
    assert brain._render_normal() is True
    assert brain._render_normal(True) is True      # force


def test_weather_failure_never_shows_invented_values(brain):
    brain.location_configured = True
    brain.latitude, brain.longitude = 1.0, 2.0
    assert brain._weather_for_display() is None            # nothing fetched yet
    brain._update_weather()                                 # fake fetch returns None
    assert brain.weather_data is None
    assert brain._weather_for_display() == {'error': True}
    assert "Weather: not available" in brain._get_intent_context()
    # retry is scheduled soon, not in 10 minutes
    assert brain.last_weather_fetch - time.time() <= brain_mod.WEATHER_RETRY_INTERVAL + 1


def test_stale_weather_is_not_presented_as_current(brain):
    brain.location_configured = True
    brain.weather_data = {'temp_f': 70}
    brain.weather_time = time.time() - brain_mod.WEATHER_MAX_AGE - 1
    brain.last_weather_fetch = time.time()
    assert brain._weather_for_display() == {'error': True}


def test_is_playing_needs_live_spotify(brain, monkeypatch, tmp_path):
    result = {'artist': 'A', 'track': 'T', 'is_playing': True, 'source': 'shared', 'timestamp': 1}
    monkeypatch.setattr(brain_mod.music_manager, "update_music_display", lambda: result)
    brain._update_music()
    assert brain.music_data['is_playing'] is False
    result.update(source='currently_playing')
    brain.last_music_fetch = 0
    monkeypatch.setattr(brain, "_has_live_spotify", lambda: True)
    brain._update_music()
    assert brain.music_data['is_playing'] is True


def test_ambient_led_untouched_during_voice(brain):
    async def go():
        brain.music_data = {'is_playing': True}
        brain._voice_busy = True
        brain._sync_ambient_led()
        assert brain._led_ambient_state is None
        brain._voice_busy = False
        brain._sync_ambient_led()
        await asyncio.sleep(0.05)
        assert brain._led_ambient_state == "playing"
        await brain.led.off_all()
    asyncio.run(go())


def test_refresh_runs_from_a_worker_thread(brain):
    """The old code called asyncio from the display thread and crashed."""
    async def go():
        await brain._in_thread(brain._refresh_data)
        brain._sync_ambient_led()
        await brain.led.off_all()
    asyncio.run(go())


def test_slow_network_does_not_block_the_event_loop(brain, monkeypatch):
    monkeypatch.setattr(brain_mod.music_manager, "update_music_display",
                        lambda: time.sleep(0.5))

    async def go():
        ticks = 0
        refresh = asyncio.ensure_future(brain._in_thread(brain._refresh_data))
        while not refresh.done():
            await asyncio.sleep(0.01)
            ticks += 1
        return ticks
    assert asyncio.run(go()) > 20


# --- Process exit ------------------------------------------------------------------

def test_fatal_error_exits_with_code_1(monkeypatch):
    def boom(self): raise RuntimeError("no display")
    monkeypatch.setattr(LeelooBrain, "__init__", boom)
    with pytest.raises(SystemExit) as e:
        brain_mod.main()
    assert e.value.code == 1
