"""Brain thread safety, event-loop blocking, voice robustness and LED guard.

LeelooBrain is built for real (hardware falls back to mock mode); network,
framebuffer, FrameAnimator and typewriter are stubbed.
"""
import asyncio
import os
import threading
import time

import numpy as np
import pytest
from PIL import Image

import leeloo_brain
from leeloo_brain import LeelooBrain, UIState, FrameType, COLORS
from leeloo_messages import MessageManager
from display import frame_animator
from display.frame_animator import FrameAnimator


class FakeAnimator:
    def __init__(self, display, box_right=153, fb_path=None):
        pass

    async def async_expand(self, frame_type, *a, **k):
        await asyncio.sleep(0.01)

    async def async_collapse(self, frame_type, *a, **k):
        await asyncio.sleep(0.01)


class FakeIntent:
    def __init__(self, action, response_text="", params=None):
        self.action = action
        self.response_text = response_text
        self.params = params or {}


@pytest.fixture
async def env(monkeypatch, tmp_path):
    for key in ("DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(leeloo_brain, "LEELOO_HOME", str(tmp_path))
    monkeypatch.setattr(
        leeloo_brain, "MessageManager",
        lambda: MessageManager(storage_path=str(tmp_path / "messages.json"))
    )
    fb_writes = []
    monkeypatch.setattr(leeloo_brain, "FrameAnimator", FakeAnimator)
    monkeypatch.setattr(
        leeloo_brain, "write_to_framebuffer",
        lambda img, fb_path=None: fb_writes.append(threading.current_thread())
    )
    monkeypatch.setattr(leeloo_brain, "get_weather", lambda *a, **k: {"temp_f": 70})

    brain = LeelooBrain()
    brain.display.render = lambda *a, **k: Image.new("RGB", (480, 320))

    async def fake_typewriter(content_lines, box_right):
        await asyncio.sleep(0.01)
        return None
    brain._typewriter = fake_typewriter

    brain.fb_writes = fb_writes
    brain.tmp_path = tmp_path
    yield brain

    brain.led._recording = False
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()
    await asyncio.sleep(0.05)


async def wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def _live_ambient_tasks():
    return [
        t for t in asyncio.all_tasks()
        if not t.done()
        and "_start_ambient_task.<locals>._breathe" in t.get_coro().__qualname__
    ]


# -----------------------------------------------------------------------------
# 8. _format_display_text honours its colour
# -----------------------------------------------------------------------------

def test_format_display_text_uses_color_argument():
    brain = LeelooBrain.__new__(LeelooBrain)
    out = LeelooBrain._format_display_text(
        brain, "the quick brown fox jumps over the lazy dog again", COLORS['tan'])
    assert len(out) > 1
    assert all(color == COLORS['tan'] for _, _, color in out)
    assert LeelooBrain._format_display_text(brain, "", COLORS['green']) == [
        ("no response", "normal", COLORS['green'])]


# -----------------------------------------------------------------------------
# 7. _sync_ambient_led from executor threads
# -----------------------------------------------------------------------------

def _make_playing(brain):
    (brain.tmp_path / "spotify_tokens.json").write_text("{}")
    brain.music_data = {"is_playing": True}


async def test_sync_ambient_led_from_executor_thread(env):
    brain = env
    brain._loop = asyncio.get_running_loop()
    _make_playing(brain)
    assert brain._led_ambient_state == "idle"

    await brain._loop.run_in_executor(None, brain._sync_ambient_led)
    assert brain._led_ambient_state == "playing"
    await wait_for(lambda: brain.led._ambient_state == "playing")
    assert len(_live_ambient_tasks()) == 1

    # And back to idle
    brain.music_data = {"is_playing": False}
    await brain._loop.run_in_executor(None, brain._sync_ambient_led)
    await wait_for(lambda: brain.led._ambient_state == "idle")
    assert brain._led_ambient_state == "idle"


async def test_sync_ambient_led_without_loop_does_not_record_state(env):
    """No loop known yet (before run()): nothing scheduled, state not updated,
    so the change is retried later instead of being lost."""
    brain = env
    brain._loop = None
    _make_playing(brain)
    t = threading.Thread(target=brain._sync_ambient_led)
    t.start()
    t.join()
    assert brain._led_ambient_state == "idle"


async def test_sync_ambient_led_on_loop_thread(env):
    brain = env
    _make_playing(brain)
    brain._sync_ambient_led()
    assert brain._led_ambient_state == "playing"
    await wait_for(lambda: brain.led._ambient_state == "playing")


async def test_update_music_in_thread_switches_ambient(env, monkeypatch):
    """End-to-end: the display tick's _update_music in an executor thread
    now really switches the ambient LED (it used to raise silently)."""
    brain = env
    brain._loop = asyncio.get_running_loop()
    (brain.tmp_path / "spotify_tokens.json").write_text("{}")
    import leeloo_music_manager
    result = {"artist": "a", "track": "t", "is_playing": True}
    monkeypatch.setattr(leeloo_music_manager, "update_music_display",
                        lambda: result, raising=False)
    # Two ticks: the Spotify branch returns right after fetching; the next
    # tick (inside the poll interval) syncs the ambient LED
    await brain._loop.run_in_executor(None, brain._update_music)
    await brain._loop.run_in_executor(None, brain._update_music)
    assert brain._led_ambient_state == "playing"
    await wait_for(lambda: brain.led._ambient_state == "playing")


# -----------------------------------------------------------------------------
# 6. Display lock + tick re-check
# -----------------------------------------------------------------------------

async def test_display_tick_does_not_paint_over_expansion_started_mid_tick(env):
    brain = env
    fetching = threading.Event()
    release = threading.Event()

    def slow_weather():
        fetching.set()
        release.wait(2)
    brain._update_weather = slow_weather
    brain._update_music = lambda: None

    loop = asyncio.get_running_loop()
    tick = loop.run_in_executor(None, brain._display_tick)
    await loop.run_in_executor(None, fetching.wait, 2)
    # An expansion starts while the tick is still fetching data
    brain.ui_state = UIState.EXPANDING
    release.set()
    await tick
    assert brain.fb_writes == []


async def test_display_tick_skips_during_welcome(env):
    brain = env
    brain._update_weather = lambda: None
    brain._update_music = lambda: None
    brain._welcome_qr_active = True
    await asyncio.get_running_loop().run_in_executor(None, brain._display_tick)
    assert brain.fb_writes == []
    brain._welcome_qr_active = False
    await asyncio.get_running_loop().run_in_executor(None, brain._display_tick)
    assert len(brain.fb_writes) == 1


async def test_render_normal_skips_when_not_normal(env):
    brain = env
    for state in (UIState.EXPANDING, UIState.EXPANDED, UIState.COLLAPSING,
                  UIState.LISTENING, UIState.PROCESSING):
        brain.ui_state = state
        brain._render_normal()
    assert brain.fb_writes == []
    brain.ui_state = UIState.NORMAL
    brain._render_normal()
    assert len(brain.fb_writes) == 1


async def test_display_lock_serialises_display_use(env):
    """render (tick thread) and draw_album_art (expansion's box_right) must
    never use self.display at the same time."""
    brain = env
    inside = []
    overlap = []
    guard = threading.Lock()

    def enter_exit(name):
        with guard:
            if inside:
                overlap.append((inside[0], name))
            inside.append(name)
        time.sleep(0.05)
        with guard:
            inside.remove(name)

    def fake_render(*a, **k):
        enter_exit("render")
        return Image.new("RGB", (480, 320))

    def fake_draw_album_art(album_art_path=None, **k):
        enter_exit("album_art")
        return 240

    brain.display.render = fake_render
    brain.display.draw_album_art = fake_draw_album_art
    loop = asyncio.get_running_loop()
    jobs = []
    for _ in range(4):
        jobs.append(loop.run_in_executor(None, brain._render_normal))
        jobs.append(loop.run_in_executor(None, brain._calculate_box_right))
    results = await asyncio.gather(*jobs)
    assert overlap == []
    assert 230 in results


async def test_no_deadlock_tick_nested_lock(env):
    """_display_tick holds the (re-entrant) lock while calling _render_normal."""
    brain = env
    brain._update_weather = lambda: None
    brain._update_music = lambda: None
    await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(None, brain._display_tick), timeout=2)
    assert len(brain.fb_writes) == 1


# -----------------------------------------------------------------------------
# 5. Blocking work runs off the event loop
# -----------------------------------------------------------------------------

def _thread_recorder(record, name, result=None):
    def fn(*a, **k):
        record.append((name, threading.current_thread() is threading.main_thread()))
        return result
    return fn


async def test_expand_frame_blocking_work_off_loop(env):
    brain = env
    calls = []
    brain._calculate_box_right = _thread_recorder(calls, "box_right", 230)
    brain._update_music = _thread_recorder(calls, "update_music")
    brain._render_normal = _thread_recorder(calls, "render")
    task = brain._start_expansion(FrameType.MESSAGES, [("x", "large", COLORS['white'])], duration=0.01)
    await asyncio.wait_for(task, timeout=2)
    names = [c[0] for c in calls]
    assert names == ["box_right", "update_music", "render"]
    assert not any(on_main for _, on_main in calls)


async def test_message_music_refresh_off_loop(env):
    brain = env
    calls = []
    brain._update_music = _thread_recorder(calls, "update_music")
    brain._render_normal = _thread_recorder(calls, "render")
    brain._calculate_box_right = lambda: 230

    async def detect(text):
        return "artist song"

    async def search(query, pushed_by=None):
        return {"artist": "a", "track": "t"}
    brain._detect_music_in_message = detect
    brain._search_spotify_display_only = search
    await brain._handle_message_with_music("amy", "listen to artist song")
    assert ("update_music", False) in calls and ("render", False) in calls
    assert not any(on_main for _, on_main in calls)


async def test_song_push_refresh_off_loop(env):
    brain = env
    calls = []
    brain._update_music = _thread_recorder(calls, "update_music")
    brain._calculate_box_right = lambda: 230
    brain._render_normal = lambda: None

    async def search(query, pushed_by=None):
        return {"artist": "a", "track": "t"}
    brain._search_spotify_display_only = search
    brain._on_ws_song_push("amy", {"artist": "a", "track": "t"})
    await wait_for(lambda: ("update_music", False) in calls)
    assert not any(on_main for _, on_main in calls)


async def test_welcome_renders_off_loop(env):
    brain = env
    calls = []
    brain._render_normal = _thread_recorder(calls, "render")
    brain._calculate_box_right = lambda: 230
    brain._update_music = lambda: None
    brain.device_config = {"user_name": "nat", "telegram_opted_in": True}
    brain.crew_config = {"invite_code": "ABC-123"}
    brain._generate_qr_image = lambda **k: str(brain.tmp_path / "qr.png")
    real_expand = brain.expand_frame

    async def short_expand(frame_type, content_lines, duration=0.0):
        await real_expand(frame_type, content_lines, duration=0.01)
    brain.expand_frame = short_expand
    await asyncio.wait_for(brain._launch_welcome_screen(), timeout=3)
    assert calls and not any(on_main for _, on_main in calls)


@pytest.mark.parametrize("spotify", [False, True])
async def test_welcome_qr_generation_off_loop(env, spotify):
    """Both welcome phases build their QR PNG in the executor."""
    brain = env
    calls = []
    brain._render_normal = lambda: None
    brain._calculate_box_right = lambda: 230
    brain._update_music = lambda: None
    brain.device_config = {"user_name": "nat", "telegram_opted_in": True}
    brain.crew_config = {"invite_code": "ABC-123"}
    brain._should_show_spotify_qr = lambda: spotify
    brain._generate_spotify_oauth_url = lambda: "https://example.invalid/auth"

    def gen_qr(qr_url, label_text, sublabel_text, label_color, hint_text=None):
        calls.append((label_text, threading.current_thread() is threading.main_thread()))
        return str(brain.tmp_path / "qr.png")
    brain._generate_qr_image = gen_qr
    real_expand = brain.expand_frame

    async def short_expand(frame_type, content_lines, duration=0.0):
        await real_expand(frame_type, content_lines, duration=0.01)
    brain.expand_frame = short_expand
    await asyncio.wait_for(brain._launch_welcome_screen(), timeout=3)
    expected = ["scan for Telegram"] + (["scan to sync"] if spotify else [])
    assert [c[0] for c in calls] == expected
    assert not any(on_main for _, on_main in calls)
    assert brain._welcome_qr_active is False


async def test_run_initial_render_off_loop(env, monkeypatch):
    brain = env
    calls = []
    brain._update_weather = _thread_recorder(calls, "weather")
    brain._update_music = _thread_recorder(calls, "music")
    brain._render_normal = _thread_recorder(calls, "render")
    monkeypatch.setattr(leeloo_brain.os, "system", lambda cmd: 0)

    async def forever():
        await asyncio.sleep(3600)
    brain._display_loop = forever
    brain.tap.start = forever
    brain.ws_client.is_configured = lambda: False

    run_task = asyncio.ensure_future(brain.run())
    await wait_for(lambda: len(calls) >= 3)
    assert brain._loop is asyncio.get_running_loop()
    assert [c[0] for c in calls[:3]] == ["weather", "music", "render"]
    assert not any(on_main for _, on_main in calls)
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass


async def test_voice_music_refresh_runs_concurrently_with_intent(env):
    """The Spotify refresh must not hold the intent call: here the refresh
    can only finish once routing has started (sequential code would hang)."""
    brain = env
    routing_started = threading.Event()
    calls = []

    def blocking_update_music():
        calls.append(threading.current_thread() is threading.main_thread())
        assert routing_started.wait(2), "intent routing waited for the music refresh"

    brain._update_music = blocking_update_music

    class Voice:
        async def record_and_transcribe(self):
            return "what's the weather"

    class Router:
        async def route(self, transcript):
            routing_started.set()
            await asyncio.sleep(0.05)
            return FakeIntent("NUDGE")

    brain.voice = Voice()
    brain.intent_router = Router()
    brain.ws_client.connected = False
    await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    assert calls == [False]
    assert brain.ui_state == UIState.NORMAL


# -----------------------------------------------------------------------------
# 3 + 4. Voice robustness and LED recording guard in the brain
# -----------------------------------------------------------------------------

class BlockingVoice:
    def __init__(self):
        self.recording = asyncio.Event()

    async def record_and_transcribe(self):
        self.recording.set()
        await asyncio.sleep(3600)
        return "never"


@pytest.mark.parametrize("with_router", [True, False])
async def test_cancel_during_recording_resets_state(env, with_router):
    brain = env
    voice = BlockingVoice()
    brain.voice = voice

    class Router:
        async def route(self, transcript):
            return FakeIntent("UNKNOWN")
    brain.intent_router = Router() if with_router else None

    await brain.led.set_ambient("idle")
    task = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(voice.recording.wait(), timeout=2)

    assert brain.ui_state == UIState.LISTENING
    assert brain.led.is_recording
    assert _live_ambient_tasks() == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert brain.ui_state == UIState.NORMAL
    assert not brain.led.is_recording
    assert brain._voice_active is False
    assert not (brain.led._animation_task and not brain.led._animation_task.done())
    await asyncio.sleep(0)
    assert len(_live_ambient_tasks()) == 1  # ambient restored afterwards

    # And the device accepts the next tap (no stuck LISTENING)
    voice2 = BlockingVoice()
    brain.voice = voice2
    task2 = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(voice2.recording.wait(), timeout=2)
    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2


@pytest.mark.parametrize("with_router", [True, False])
async def test_cancel_inside_recording_started_clears_guard(env, with_router):
    """A cancel that lands while recording_started() is still awaiting must
    not leave the guard on (that would silence every LED until restart)."""
    brain = env
    brain.voice = BlockingVoice()

    class Router:
        async def route(self, transcript):
            return FakeIntent("UNKNOWN")
    brain.intent_router = Router() if with_router else None

    entered = asyncio.Event()
    real_started = brain.led.recording_started

    async def slow_recording_started():
        await real_started()
        entered.set()
        await asyncio.sleep(3600)  # cancelled while still in here
    brain.led.recording_started = slow_recording_started

    task = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert brain.led.is_recording
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not brain.led.is_recording
    assert brain.ui_state == UIState.NORMAL
    assert brain._voice_active is False


async def test_voice_error_resets_state(env):
    brain = env

    class Voice:
        async def record_and_transcribe(self):
            raise RuntimeError("arecord died")

    class Router:
        async def route(self, transcript):
            return FakeIntent("UNKNOWN")

    brain.voice = Voice()
    brain.intent_router = Router()
    await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    assert brain.ui_state == UIState.NORMAL
    assert not brain.led.is_recording
    assert brain._voice_active is False


async def test_voice_only_error_resets_state(env):
    brain = env

    class Voice:
        async def record_and_transcribe(self):
            raise RuntimeError("arecord died")

    brain.voice = Voice()
    brain.intent_router = None
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    assert brain.ui_state == UIState.NORMAL
    assert not brain.led.is_recording
    assert brain._voice_active is False


async def test_nudge_intent_does_not_leave_processing(env):
    """Old bug: intents that start no expansion left ui_state=PROCESSING,
    so every later tap was ignored."""
    brain = env
    brain._update_music = lambda: None

    class Voice:
        async def record_and_transcribe(self):
            return "send a nudge"

    class Router:
        async def route(self, transcript):
            return FakeIntent("NUDGE")

    brain.voice = Voice()
    brain.intent_router = Router()
    brain.ws_client.connected = False
    await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    assert brain.ui_state == UIState.NORMAL


async def test_voice_nudge_keeps_cyan_nudge_running(env):
    """Review regression: the voice finally used to call led.off()
    unconditionally, cancelling the 30s cyan nudge that the NUDGE intent had
    just started (a brief flicker instead of the confirmation)."""
    brain = env
    brain._update_music = lambda: None
    sent = []

    class Voice:
        async def record_and_transcribe(self):
            return "send a nudge"

    class Router:
        async def route(self, transcript):
            await asyncio.sleep(0.02)
            return FakeIntent("NUDGE")

    async def send_nudge():
        await asyncio.sleep(0.05)
        sent.append(1)

    brain.voice = Voice()
    brain.intent_router = Router()
    brain.ws_client.connected = True
    brain.ws_client.send_nudge = send_nudge
    await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    assert sent == [1]
    await asyncio.sleep(0.2)
    assert brain.led.current_animation == "nudge"
    assert brain.led._animation_task and not brain.led._animation_task.done()
    assert brain.ui_state == UIState.NORMAL
    await brain.led.off()


async def test_voice_success_leaves_no_voice_leds(env):
    """A normal intent (no LED action) ends with the voice LEDs off and the
    ambient breathe back."""
    brain = env
    brain._update_music = lambda: None

    class Voice:
        async def record_and_transcribe(self):
            return "hello"

    class Router:
        async def route(self, transcript):
            await asyncio.sleep(0.05)  # processing breathe is running
            return FakeIntent("UNKNOWN")

    brain.voice = Voice()
    brain.intent_router = Router()
    await brain.led.set_ambient("idle")
    await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    await asyncio.sleep(0.05)
    assert brain.led.current_animation is None
    assert not (brain.led._animation_task and not brain.led._animation_task.done())
    assert len(_live_ambient_tasks()) == 1


async def test_cancel_during_routing_stops_processing_leds(env):
    brain = env
    brain._update_music = lambda: None
    routing = asyncio.Event()

    class Voice:
        async def record_and_transcribe(self):
            return "hello"

    class Router:
        async def route(self, transcript):
            routing.set()
            await asyncio.sleep(3600)

    brain.voice = Voice()
    brain.intent_router = Router()
    task = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(routing.wait(), timeout=2)
    await wait_for(lambda: brain.led.current_animation == "processing")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert brain.led.current_animation is None
    assert not (brain.led._animation_task and not brain.led._animation_task.done())
    assert brain.ui_state == UIState.NORMAL


async def test_cancel_before_processing_breathe_starts(env):
    """Cancelled after the processing task was created but before it ran:
    the task must be cancelled too, or it would start after the finally's
    off() and breathe green forever."""
    brain = env
    brain._update_music = lambda: None

    class Voice:
        async def record_and_transcribe(self):
            return "hello"

    class Router:
        async def route(self, transcript):
            # No await before this: the processing task has not run yet
            raise asyncio.CancelledError()

    brain.voice = Voice()
    brain.intent_router = Router()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(brain._handle_single_tap(), timeout=3)
    await asyncio.sleep(0.1)
    assert brain.led.current_animation is None
    assert not (brain.led._animation_task and not brain.led._animation_task.done())
    assert brain.ui_state == UIState.NORMAL


async def test_incoming_message_during_recording_does_not_touch_leds(env):
    brain = env
    voice = BlockingVoice()
    brain.voice = voice
    brain.intent_router = None
    brain._calculate_box_right = lambda: 230
    brain._update_music = lambda: None

    async def no_music(text):
        return None
    brain._detect_music_in_message = no_music

    async def no_search(query, pushed_by=None):
        return None
    brain._search_spotify_display_only = no_search  # no network in tests

    await brain.led.set_ambient("idle")
    task = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(voice.recording.wait(), timeout=2)

    writes = []
    real_set_color = brain.led._set_color
    brain.led._set_color = lambda c: writes.append(c)

    brain._on_ws_message("amy", "hi")
    brain._on_ws_nudge("ben")
    brain._on_ws_reaction("cat", "love")
    brain._on_ws_song_push("dan", {"artist": "a", "track": "t"})
    brain._loop = asyncio.get_running_loop()
    _make_playing(brain)
    await asyncio.get_running_loop().run_in_executor(None, brain._sync_ambient_led)
    await asyncio.sleep(0.3)

    assert writes == []
    assert _live_ambient_tasks() == []
    assert brain.led.is_recording

    brain.led._set_color = real_set_color
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not brain.led.is_recording
    assert brain.led._ambient_state == "playing"


async def test_second_tap_during_ack_does_not_start_second_session(env):
    brain = env
    sessions = []

    class Voice:
        async def record_and_transcribe(self):
            sessions.append(1)
            await asyncio.sleep(0.1)
            return ""

    class Router:
        async def route(self, transcript):
            return FakeIntent("UNKNOWN")

    brain.voice = Voice()
    brain.intent_router = Router()
    t1 = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.sleep(0)  # t1 is now in the ack flash
    t2 = asyncio.ensure_future(brain._handle_single_tap())
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=3)
    assert sessions == [1]


# -----------------------------------------------------------------------------
# 5. FrameAnimator: preprocessing off the loop, framebuffer opened once
# -----------------------------------------------------------------------------

async def test_frame_animator_preprocess_in_executor_and_single_open(monkeypatch, tmp_path):
    fb_file = tmp_path / "fb1"
    fb_file.write_bytes(b"\x00" * (480 * 320 * 2))

    opens = []
    real_open = open

    def counting_open(path, *a, **k):
        opens.append(path)
        return real_open(path, *a, **k)
    monkeypatch.setattr(frame_animator, "open", counting_open, raising=False)

    animator = FrameAnimator(display=None, box_right=230, fb_path=str(fb_file))
    threads = []
    real_preprocess = animator._preprocess_frames

    def recording_preprocess(*a, **k):
        threads.append(threading.current_thread() is threading.main_thread())
        return real_preprocess(*a, **k)
    animator._preprocess_frames = recording_preprocess

    await animator.async_expand(FrameType.MESSAGES)
    await animator.async_collapse(FrameType.MESSAGES)

    assert threads == [False, False]           # preprocessing ran in a worker thread
    assert opens == [str(fb_file), str(fb_file)]  # one open per animation, not per frame
    data = np.frombuffer(fb_file.read_bytes(), dtype=np.uint16)
    assert data.any()  # frames were actually written


async def test_frame_animator_missing_framebuffer_does_not_crash(tmp_path):
    animator = FrameAnimator(display=None, box_right=230, fb_path=str(tmp_path / "missing"))
    await animator.async_expand(FrameType.WEATHER)
    assert not os.path.exists(tmp_path / "missing")
