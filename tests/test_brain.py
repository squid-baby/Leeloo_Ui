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
    for name in ("messages.json", "crew_config.json", "device_config.json", "leeloo_data.json",
                 ".spotify_offered", ".welcome_sent"):
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

    async def spy(frame_type, lines, duration=20.0, **kw):
        b.frames.append((frame_type, [l[0] for l in lines]))
        return await real_expand(frame_type, lines, min(duration, b.frame_hold), **kw)

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


def test_empty_transcript_returns_to_normal_and_tells_the_user(brain):
    brain.voice = FakeVoice(transcript="")
    run_voice(brain, Intent("NUDGE"))
    assert brain.ui_state == UIState.NORMAL
    assert "didn't catch that" in frame_text(brain)
    assert not brain.ws_client.sent


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
        brain._on_ws_message("Amy", "listen to this")
        await asyncio.sleep(0.1)
        assert brain.message_view_active
        assert "2 taps = love" in frame_text(brain)
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

def test_render_only_when_something_changed(brain, monkeypatch):
    # Fixed clock: a real 10-second boundary between two calls would repaint
    monkeypatch.setattr(brain, "_update_time", lambda: brain.__setattr__(
        "time_data", {'time_str': '9:00 AM', 'date_str': 'Nov 12', 'seconds': 5}))
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
    assert asyncio.run(go()) > 5


# --- Process exit ------------------------------------------------------------------

def test_fatal_error_exits_with_code_1(monkeypatch):
    def boom(self): raise RuntimeError("no display")
    monkeypatch.setattr(LeelooBrain, "__init__", boom)
    with pytest.raises(SystemExit) as e:
        brain_mod.main()
    assert e.value.code == 1


# =============================================================================
# Review round 2: defects found by independent review
# =============================================================================

def ambient_tasks():
    return [t for t in asyncio.all_tasks()
            if not t.done() and "_breathe" in repr(t.get_coro()) and "_start_ambient_task" in repr(t.get_coro())]


def test_no_led_task_survives_into_a_recording(brain):
    """A nudge animation + tap used to leave an ambient task that nobody could cancel."""
    brain.voice = FakeVoice(transcript="", delay=0.3)
    seen = {}

    async def go():
        await brain.led.set_ambient("idle")
        brain._on_ws_nudge("Amy")                  # 30 s LED animation + frame
        await asyncio.sleep(0.1)
        brain.dismiss_frame()
        await asyncio.sleep(0.2)
        tap = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.25)
        seen['state'] = brain.ui_state
        seen['recording'] = brain.led.recording
        seen['ambient_during'] = len(ambient_tasks())
        brain._on_ws_message("Ben", "hi")          # LED blink must not start now
        await asyncio.sleep(0.02)
        seen['anim_during'] = brain.led._current_animation
        await tap
        await settle(brain)
        await asyncio.sleep(1.5)                   # message blink animation ends
        seen['ambient_after'] = len(ambient_tasks())
        await brain.led.off_all()
    asyncio.run(go())
    assert seen['state'] == UIState.LISTENING and seen['recording'] is True
    assert seen['ambient_during'] == 0
    assert seen['anim_during'] == "listening"
    assert seen['ambient_after'] == 1              # exactly one, never two


def test_led_manager_blocks_animations_while_recording():
    from leeloo_led import LEDManager

    async def go():
        led = LEDManager()
        await led.set_ambient("idle")
        led._start_ambient_task(); led._start_ambient_task()
        assert len(ambient_tasks()) == 1
        await led.listening()
        await led.ack(); await led.message_received(); await led.nudge(duration=1)
        await led.set_ambient("playing")
        assert led._current_animation == "listening" and len(ambient_tasks()) == 0
        await led.off()
        assert led.recording is False and len(ambient_tasks()) == 1
        assert led._ambient_state == "playing"     # change made during recording is kept
        await led.off_all()
    asyncio.run(go())


def test_tap_during_slow_intent_does_not_start_second_session(brain, monkeypatch):
    async def slow_time():
        await asyncio.sleep(0.4)
        return "the hour is late"
    monkeypatch.setattr(brain, "_generate_cosmic_time", slow_time)
    brain.intent_router = FakeRouter(Intent("TIME_QUERY", {}, ""))

    async def go():
        first = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.25)                  # voice done, intent still running
        assert brain._voice_busy and brain.ui_state == UIState.PROCESSING
        await brain._on_tap('single_tap')
        await first
        await settle(brain)
    asyncio.run(go())
    assert brain.voice.calls == 1
    assert "the hour is late" in frame_text(brain)


def test_double_tap_nudge_frame_is_deferred_during_recording(brain):
    brain.voice = FakeVoice(transcript="", delay=0.4)
    real_send = brain.ws_client.send_nudge

    async def slow_send():
        await asyncio.sleep(0.2)
        return await real_send()
    brain.ws_client.send_nudge = slow_send

    async def go():
        double = asyncio.ensure_future(brain._on_tap('double_tap'))
        await asyncio.sleep(0.05)
        single = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.3)                   # nudge sent while recording
        assert brain.ui_state == UIState.LISTENING and not brain.frames
        await asyncio.gather(double, single)
        await settle(brain)
    asyncio.run(go())
    assert "nudge sent" in frame_text(brain)


def test_remote_confirm_without_a_proposal_does_not_claim_confirmed(brain):
    async def go():
        brain._on_ws_hang_confirm("Amy")
        await settle(brain)
    asyncio.run(go())
    assert "hang confirmed!" not in frame_text(brain)
    assert brain_mod.gadget_data.get_next_hang() is None


def test_pending_hang_survives_restart(brain):
    when = (datetime.now() + timedelta(days=3)).replace(microsecond=0)
    run_voice(brain, Intent("HANG_PROPOSE", {"datetime": when.isoformat()}, "sat?"))
    assert brain._pending_hang_dt
    second = LeelooBrain()                         # simulated restart
    assert second._pending_hang_dt == brain._pending_hang_dt
    second._pending_hang_dt = None
    assert brain._pending_hang_dt is None


def test_queued_old_or_expired_items_are_not_counted(brain):
    async def go():
        brain._on_ws_queued("Amy", "text", {"text": "old"}, time.time() - 90000)
        brain._on_ws_queued("Amy", "hang_propose", {"datetime": "2020-01-01T10:00:00"}, time.time())
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert brain._queued_count == 0 and not brain.message_mgr.get_unread_counts()


def test_music_polling_tiers(brain):
    now = time.time()
    brain.last_music_fetch = 0
    assert brain._music_interval(now) == 0                       # first fetch at once
    brain.last_music_fetch = now
    brain.music_data = {'is_playing': True}
    assert brain._music_interval(now) == brain_mod.MUSIC_REFRESH_INTERVAL
    brain.music_data = {'is_playing': False, 'last_updated': now}  # paused track, polled just now
    brain._last_playing_at = now - 100
    assert brain._music_interval(now) == 60
    brain._last_playing_at = now - 400
    assert brain._music_interval(now) == 120                     # reaches the idle tier
    brain.music_data = None
    assert brain._music_interval(now) == 120


class JoinClient(FakeClient):
    def __init__(self, crew_id=""):
        super().__init__()
        self.config.crew_id = crew_id
        self.created = 0
    async def connect(self): self.connected = True; return True
    async def rejoin_crew(self): return False
    async def create_crew(self, name): self.created += 1; return None
    async def listen(self): await asyncio.sleep(10)
    async def keepalive(self): await asyncio.sleep(10)


def run_ws_loop(brain, seconds=0.4):
    async def go():
        task = asyncio.ensure_future(brain._ws_connection_loop())
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        brain.dismiss_frame()
        await settle(brain)
    asyncio.run(go())


def test_wrong_join_code_does_not_create_a_crew(brain):
    brain.ws_client = JoinClient()
    brain.crew_config = {'invite_code': 'LEELOO-ABCD', 'is_creator': False}
    run_ws_loop(brain)
    assert brain.ws_client.created == 0
    assert "crew not found" in frame_text(brain)


@pytest.mark.parametrize("config,crew_id", [({'is_creator': True}, ""), ({'is_creator': False}, "LEELOO-ABCD")])
def test_creator_or_former_member_recreates_crew_after_server_reset(brain, config, crew_id):
    brain.ws_client = JoinClient(crew_id=crew_id)
    brain.crew_config = config
    run_ws_loop(brain)
    assert brain.ws_client.created == 1
    assert "crew not found" not in frame_text(brain)


def test_spotify_setup_intent_shows_qr_and_restores_art(brain, monkeypatch):
    monkeypatch.setattr(brain, "_generate_qr_image", lambda *a: None)
    brain.album_art_path = None
    run_voice(brain, Intent("SPOTIFY_SETUP", {}, ""))
    assert "connect spotify" in frame_text(brain)
    assert brain._welcome_qr_active is False

    brain.frames.clear()
    brain.ws_client.config.device_id = ""
    run_voice(brain, Intent("SPOTIFY_SETUP", {}, ""))
    assert "not ready yet" in frame_text(brain)

    brain.frames.clear()
    monkeypatch.setattr(brain, "_has_live_spotify", lambda: True)
    run_voice(brain, Intent("SPOTIFY_SETUP", {}, ""))
    assert "spotify is connected" in frame_text(brain)


def test_missing_spotify_secret_is_explained(brain, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(brain_mod.music_manager, "search_and_save_shared", lambda q, by: None)
    run_voice(brain, Intent("SONG_PUSH", {"query": "a song"}, ""))
    assert "spotify is off" in frame_text(brain)


def test_first_boot_welcome_runs_and_cleans_up(brain, monkeypatch):
    monkeypatch.setattr(brain, "_generate_qr_image", lambda *a: None)
    brain.device_config = {'telegram_opted_in': True}
    brain.crew_config = {'invite_code': 'LEELOO-ABCD'}
    brain.user_name = "nat"

    async def go():
        brain._start_frame_task(brain._show_welcome_with_qr)
        await settle(brain, timeout=15)
    asyncio.run(go())
    text = frame_text(brain)
    assert "welcome nat!" in text and "one more thing..." in text
    assert brain._welcome_qr_active is False


# =============================================================================
# Review round 3
# =============================================================================

def test_double_tap_on_a_non_message_frame_sends_nothing(brain):
    brain.frame_hold = 30

    async def go():
        brain.show_frame(FrameType.MESSAGES, [("message sent", "large", "#fff")], duration=30)
        await asyncio.sleep(0.1)
        assert not brain.message_view_active
        await brain._on_tap('double_tap')
        await brain._on_tap('triple_tap')
        brain.dismiss_frame()
        await settle(brain)
    asyncio.run(go())
    assert brain.ws_client.sent == []


def test_next_frame_waits_for_the_closing_repaint(brain, monkeypatch):
    """A frame that arrives during the repaint of the previous one must not
    draw while the repaint still writes to the screen."""
    events = []
    real_render = brain._render_normal

    def slow_render(force=False):
        if force:
            events.append("repaint-start")
            time.sleep(0.25)
            real_render(force)
            events.append("repaint-end")
            return True
        return real_render(force)
    monkeypatch.setattr(brain, "_render_normal", slow_render)
    real_typewriter = brain._typewriter

    async def typewriter(lines, box_right):
        events.append("draw:" + lines[0][0])
        return await real_typewriter(lines, box_right)
    brain._typewriter = typewriter

    async def go():
        brain.show_frame(FrameType.WEATHER, [("one", "normal", "#fff")], duration=30)
        await asyncio.sleep(0.1)
        brain.show_frame(FrameType.WEATHER, [("two", "normal", "#fff")], duration=30)   # cancels "one"
        await asyncio.sleep(0.1)                                                        # repaint running
        brain.show_frame(FrameType.WEATHER, [("three", "normal", "#fff")])              # cancels again
        await settle(brain, timeout=10)
    brain.frame_hold = 0.3
    asyncio.run(go())
    for i, e in enumerate(events):
        if e.startswith("draw:") and i > 0:
            starts = events[:i].count("repaint-start")
            ends = events[:i].count("repaint-end")
            assert starts == ends, f"{e} drew during a repaint: {events}"
    assert "draw:three" in events


def test_large_lines_are_wrapped_by_pixel_width(brain):
    from PIL import ImageFont
    try:
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 18)
    except OSError:
        pytest.skip("DejaVu font not installed")
    brain.font_large = big
    lines = brain._fit_lines([
        ("spotify is connected", "large", "#fff"),
        ("from bartholomew-longname", "large", "#fff"),
        ("", None, None),
        ("short", "normal", "#fff"),
    ], 176)
    assert all(big.getlength(t) <= 176 for t, size, _ in lines if size == "large")
    assert [t for t, s, _ in lines if s == "large"][:2] == ["spotify is", "connected"]
    assert ("", None, None) in lines and ("short", "normal", "#fff") in lines
    assert "".join(t for t, s, _ in lines if s == "large").replace(" ", "") == \
        "spotifyisconnectedfrombartholomew-longname"


def test_clean_display_text():
    f = brain_mod.clean_display_text
    assert f("it’s 🔥🔥  ok\n❤️") == "it's (fire) (fire) ok <3"
    assert f("привет café") == "привет café"
    assert f("日本語") == "(text that leeloo can't show)"
    assert f("") == ""
    long = f("word " * 500, 400)
    assert len(long) <= 400 and long.endswith("...")
    assert brain_mod.format_display_text("hey 😀", "#fff")[0][0] == "hey :D"


def test_three_member_hang(brain):
    """A proposes. B confirms. C (this device) saw both, then says "i'm in"."""
    when = (datetime.now() + timedelta(days=2)).replace(microsecond=0)

    async def go():
        brain._on_ws_hang_propose("Amy", when.isoformat(), "pizza")
        await settle(brain)
        brain._on_ws_hang_confirm("Ben")
        await settle(brain)
    asyncio.run(go())
    assert brain_mod.gadget_data.get_next_hang()          # the hang is on for everybody
    brain.frames.clear()
    run_voice(brain, Intent("HANG_CONFIRM", {}, ""))
    assert ('hang_confirm',) in brain.ws_client.sent      # C can still say "i'm in"
    assert "you're in!" in frame_text(brain)
    brain_mod.gadget_data.clear_next_hang()


def test_cannot_confirm_own_proposal(brain):
    when = (datetime.now() + timedelta(days=2)).replace(microsecond=0)
    run_voice(brain, Intent("HANG_PROPOSE", {"datetime": when.isoformat()}, ""))
    brain.frames.clear()
    run_voice(brain, Intent("HANG_CONFIRM", {}, ""))
    assert ('hang_confirm',) not in brain.ws_client.sent
    assert "waiting for your crew" in frame_text(brain)
    assert brain_mod.gadget_data.get_next_hang() is None


def test_stale_proposal_cannot_be_confirmed(brain):
    old = (datetime.now() - timedelta(days=3)).replace(microsecond=0)
    brain_mod.gadget_data.save_data({'pending_hang': {'datetime': old.isoformat(), 'by': 'Amy'}})
    run_voice(brain, Intent("HANG_CONFIRM", {}, ""))
    assert ('hang_confirm',) not in brain.ws_client.sent
    assert "no hang to confirm" in frame_text(brain)
    assert brain._pending_hang_dt is None

    async def go():                                     # a live proposal in the past is not stored
        brain._on_ws_hang_propose("Amy", old.isoformat(), "x")
        await settle(brain)
    asyncio.run(go())
    assert brain._pending_hang_dt is None


def test_time_that_just_passed_is_not_moved_to_next_year(brain):
    earlier = (datetime.now() - timedelta(hours=1)).replace(microsecond=0)
    run_voice(brain, Intent("HANG_PROPOSE", {"datetime": earlier.isoformat()}, ""))
    assert "that time has passed" in frame_text(brain)
    assert not brain.ws_client.sent and brain._pending_hang_dt is None


def test_hang_between_time_zones(brain):
    """The proposer is in New York, this device in Los Angeles: same instant."""
    from zoneinfo import ZoneInfo
    brain.local_tz = ZoneInfo("America/Los_Angeles")
    brain_mod.gadget_data.set_local_tz(brain.local_tz)
    try:
        ny = (datetime.now(ZoneInfo("America/New_York")) + timedelta(days=2)).replace(
            hour=20, minute=0, second=0, microsecond=0)

        async def go():
            brain._on_ws_hang_propose("Amy", ny.isoformat(), "call")
            await settle(brain)
        asyncio.run(go())
        assert "5:00pm" in frame_text(brain)             # 8 pm New York = 5 pm Los Angeles
        run_voice(brain, Intent("HANG_CONFIRM", {}, ""))
        saved = datetime.fromisoformat(brain_mod.gadget_data.get_next_hang()['datetime'])
        assert saved == ny
        assert brain_mod.gadget_data.format_countdown_display()['time_str'].startswith("5:00")
    finally:
        brain_mod.gadget_data.set_local_tz(None)
        brain_mod.gadget_data.clear_next_hang()


def test_deferred_frame_is_shown_after_the_voice_result(brain):
    brain.voice = FakeVoice(transcript="what's the weather", delay=0.2)
    brain.intent_router = FakeRouter(Intent("WEATHER_EXPAND", {}, "sunny"))

    async def go():
        tap = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.1)
        brain._on_ws_nudge("Amy")              # arrives while recording
        await tap
        await settle(brain)
        await asyncio.sleep(0.3)
        await settle(brain)
    asyncio.run(go())
    texts = [" ".join(lines) for _t, lines in brain.frames]
    assert any("sunny" in t for t in texts) and any("nudged you" in t for t in texts)
    assert texts.index(next(t for t in texts if "sunny" in t)) < \
        texts.index(next(t for t in texts if "nudged you" in t))


def test_voice_without_brain_key_explains_and_shows_deferred(brain):
    brain.intent_router = None
    brain.voice = FakeVoice(transcript="hello", delay=0.2)

    async def go():
        tap = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.1)
        brain._on_ws_message("Amy", "yo")
        await tap
        await settle(brain)
        await asyncio.sleep(0.3)
        await settle(brain)
    asyncio.run(go())
    text = frame_text(brain)
    assert "ANTHROPIC_API_KEY" in text.replace(" ", "_") or "ANTHROPIC_API_KEY" in text
    assert "from amy" in text
    assert brain.ui_state == UIState.NORMAL and not brain._voice_busy


def test_time_query_shows_the_clock_time_first(brain, monkeypatch):
    async def cosmic(): return "a long poem about the season"
    monkeypatch.setattr(brain, "_generate_cosmic_time", cosmic)
    run_voice(brain, Intent("TIME_QUERY", {}, ""))
    first = brain.frames[-1][1][0]
    assert first.endswith("am") or first.endswith("pm")


def test_long_content_scrolls_and_closes(brain):
    lines = [(f"line {i}", "normal", "#fff") for i in range(30)]
    brain.frame_hold = 0.2

    async def go():
        real_sleep = asyncio.sleep
        brain.show_frame(FrameType.MESSAGES, lines)
        await settle(brain, timeout=60)
    import leeloo_brain
    orig = brain._scroll_content
    called = {}

    async def scroll(overflow, hold):
        called['max'] = overflow['total_height'] - overflow['visible_height']
        overflow = dict(overflow, total_height=overflow['visible_height'] + 5)   # 5 scroll steps
        return await orig(overflow, 0.1)
    brain._scroll_content = scroll
    asyncio.run(go())
    assert called['max'] > 0 and brain.ui_state == UIState.NORMAL


def test_offline_header_after_grace_period(brain):
    brain.ws_client.connected = False
    brain._ws_down_since = time.time() - brain_mod.OFFLINE_GRACE_S - 1
    assert brain._status_text() == "offline"
    brain._ws_down_since = time.time()
    assert brain._status_text() is None


def test_incoming_song_push_and_reaction(brain, monkeypatch):
    seen = []
    monkeypatch.setattr(brain_mod.music_manager, "search_and_save_shared",
                        lambda q, by: seen.append((q, by)) or {'artist': 'A', 'track': 'T'})

    async def go():
        brain._on_ws_song_push("Amy", {'artist': 'A', 'track': 'T', 'spotify_uri': 'spotify:track:9', 'note': 'banger'})
        await settle(brain)
        brain._on_ws_reaction("Ben", "fire")
        await settle(brain)
    asyncio.run(go())
    text = frame_text(brain)
    assert "amy shared" in text and "banger" in text and "ben sent" in text and "fire!" in text
    assert seen == [('spotify:track:9', 'Amy')]


def test_spotify_offer_survives_a_skipped_welcome(brain, monkeypatch):
    monkeypatch.setattr(brain, "_generate_qr_image", lambda *a: None)
    assert not os.path.exists(brain_mod.SPOTIFY_OFFERED_FLAG)

    async def go():
        await brain._offer_spotify()
    asyncio.run(go())
    assert os.path.exists(brain_mod.SPOTIFY_OFFERED_FLAG)
    assert "one more thing..." in frame_text(brain)
    os.remove(brain_mod.SPOTIFY_OFFERED_FLAG)


def test_led_error_flash_is_not_overwritten_by_ambient():
    """An animation that replaces another must not let ambient restart under it."""
    from leeloo_led import LEDManager

    async def go():
        led = LEDManager()
        await led.set_ambient("idle")
        proc = asyncio.ensure_future(led.processing())
        await asyncio.sleep(0.05)
        err = asyncio.ensure_future(led.error())
        await asyncio.sleep(0.1)
        during = len(ambient_tasks())
        await asyncio.gather(proc, err)
        await asyncio.sleep(0.05)
        after = len(ambient_tasks())
        await led.off_all()
        return during, after
    assert asyncio.run(go()) == (0, 1)


def test_ack_in_flight_does_not_write_after_listening_starts():
    from leeloo_led import LEDManager, OFF

    async def go():
        led = LEDManager()
        writes = []
        led._set_color = lambda c: writes.append(c)
        ack = asyncio.ensure_future(led.ack())
        await asyncio.sleep(0.02)
        await led.listening()
        n = len(writes)
        await ack
        await asyncio.sleep(0.15)
        return writes[n:]
    assert asyncio.run(go()) == []


# =============================================================================
# Review round 4
# =============================================================================

def test_ambient_task_is_never_orphaned_by_a_racing_cancel():
    """_cancel_ambient() must not erase the reference to an ambient task
    that another LED call started while it was waiting."""
    from leeloo_led import LEDManager

    async def go():
        worst = 0
        for yields in range(0, 6):
            led = LEDManager()
            await led.set_ambient("idle")
            blink = asyncio.ensure_future(led.message_received())
            for _ in range(yields):
                await asyncio.sleep(0)
            await led.off()
            await led._cancel_ambient()
            await led.listening()
            await asyncio.sleep(0.02)
            worst = max(worst, len(ambient_tasks()))
            await led.off_all()
            blink.cancel()
            await asyncio.gather(blink, return_exceptions=True)
            for t in ambient_tasks():
                t.cancel()
        return worst
    assert asyncio.run(go()) == 0


def test_full_width_normal_line_is_not_rewrapped(brain):
    from PIL import ImageFont
    try:
        brain.font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
    except OSError:
        pytest.skip("DejaVu font not installed")
    line = "right now. check wifi"            # exactly WRAP_CHARS characters
    assert len(line) == brain_mod.WRAP_CHARS
    assert brain._fit_lines([(line, "normal", "#fff")], 176) == [(line, "normal", "#fff")]


def test_all_frames_deferred_during_voice_are_shown_in_order(brain):
    brain.voice = FakeVoice(transcript="", delay=0.3)

    async def go():
        tap = asyncio.ensure_future(brain._on_tap('single_tap'))
        await asyncio.sleep(0.1)
        brain._on_ws_message("Amy", "first")
        brain._on_ws_nudge("Ben")
        await tap
        for _ in range(4):
            await asyncio.sleep(0.2)
            await settle(brain)
    asyncio.run(go())
    texts = [" ".join(lines) for _t, lines in brain.frames]
    i_msg = next(i for i, t in enumerate(texts) if "from amy" in t)
    i_nudge = next(i for i, t in enumerate(texts) if "nudged you" in t)
    assert i_msg < i_nudge
    assert sum("from amy" in t for t in texts) == 1 and not brain._deferred_frames


def test_clean_display_text_keeps_supported_characters():
    assert brain_mod.clean_display_text("costs 20€ „Hallo“ −5° well‑known Việt") == \
        'costs 20€ "Hallo" -5° well-known Việt'


def test_confirm_for_unknown_proposal_does_not_show_an_old_hang_time(brain):
    old = (datetime.now() + timedelta(days=5)).replace(microsecond=0)
    brain_mod.gadget_data.set_next_hang(old, set_by="test")

    async def go():
        brain._on_ws_hang_confirm("Bob")
        await settle(brain)
    asyncio.run(go())
    text = frame_text(brain)
    assert "bob is in!" in text and "hang confirmed!" not in text
    assert brain._hang_label(old) not in text
    brain_mod.gadget_data.clear_next_hang()


def test_readout_cuts_very_long_messages(brain):
    async def no_scroll(overflow, hold):        # scrolling is tested elsewhere
        await asyncio.sleep(0.05)
    brain._scroll_content = no_scroll
    brain.message_mgr.add_message("Amy", "word " * 1000)
    run_voice(brain, Intent("MESSAGE_READOUT", {}, ""))
    assert sum(len(l) for l in brain.frames[-1][1]) < 600
