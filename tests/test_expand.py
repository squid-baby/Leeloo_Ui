"""Expansion serialisation: at most one expanded frame draws at a time.

Original bug: every caller did `self._expand_task = ensure_future(expand_frame(...))`
and expand_frame then "cancelled" self._expand_task — by then the new task
itself — so the old expansion was never cancelled and two drew at once
(reproduced with two crew messages within 20s).

FrameAnimator, the typewriter, box_right and framebuffer writes are stubbed;
LeelooBrain itself is real (hardware falls back to mock mode).
"""
import asyncio

import pytest
from PIL import Image

import leeloo_brain
from leeloo_brain import LeelooBrain, UIState, FrameType
from leeloo_messages import MessageManager


class FakeLED:
    """Async no-op LED stand-in (LED behaviour is covered in test_led.py)"""

    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return None
        return _noop

    def recording_finished(self):
        pass

    def off_sync(self):
        pass


class Tracker:
    """Records what expansions do, and how many draw at once."""

    def __init__(self):
        self.events = []
        self.active = 0
        self.max_active = 0
        self.fb_writes = 0


@pytest.fixture
async def env(monkeypatch, tmp_path):
    for key in ("DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(leeloo_brain, "LEELOO_HOME", str(tmp_path))
    monkeypatch.setattr(
        leeloo_brain, "MessageManager",
        lambda: MessageManager(storage_path=str(tmp_path / "messages.json"))
    )
    tracker = Tracker()

    class FakeAnimator:
        def __init__(self, display, box_right=153, fb_path=None):
            pass

        async def async_expand(self, frame_type, *a, **k):
            tracker.events.append(("expand", frame_type))
            await asyncio.sleep(0.01)

        async def async_collapse(self, frame_type, *a, **k):
            tracker.events.append(("collapse", frame_type))
            await asyncio.sleep(0.01)

    def fake_fb_write(img, fb_path=None):
        tracker.fb_writes += 1

    monkeypatch.setattr(leeloo_brain, "FrameAnimator", FakeAnimator)
    monkeypatch.setattr(leeloo_brain, "write_to_framebuffer", fake_fb_write)

    brain = LeelooBrain()
    brain.led = FakeLED()
    brain.display.render = lambda *a, **k: Image.new("RGB", (480, 320))
    brain._calculate_box_right = lambda: 230
    brain._update_music = lambda: None
    brain._update_weather = lambda: None

    async def fake_typewriter(content_lines, box_right):
        tracker.events.append(("type", content_lines[0][0]))
        await asyncio.sleep(0.01)
        return None
    brain._typewriter = fake_typewriter

    async def no_music(text):
        return None
    brain._detect_music_in_message = no_music

    # Measure concurrency at the expand_frame level (instance attribute is
    # looked up at call time by every call site, old and new)
    real_expand = brain.expand_frame

    async def tracked_expand(frame_type, content_lines, duration=leeloo_brain.EXPANDED_HOLD_DURATION):
        tracker.active += 1
        tracker.max_active = max(tracker.max_active, tracker.active)
        tracker.events.append(("start", content_lines[0][0] if content_lines else None))
        try:
            await real_expand(frame_type, content_lines, duration=duration)
        finally:
            tracker.active -= 1
            tracker.events.append(("end", content_lines[0][0] if content_lines else None))
    brain.expand_frame = tracked_expand

    yield brain, tracker

    # Teardown: stop anything still running
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


def lines(label):
    return [(label, "large", (255, 255, 255))]


# -----------------------------------------------------------------------------
# Regression: two crew messages within the hold time
# -----------------------------------------------------------------------------

async def test_two_crew_messages_regression(env, monkeypatch):
    """Two crew messages 0.1s apart (hold 0.3s): the first expansion must be
    cancelled, never more than one draws, and the old one must not reset
    ui_state to NORMAL while the new one is showing."""
    brain, tracker = env
    monkeypatch.setattr(leeloo_brain, "EXPANDED_HOLD_DURATION", 0.3)

    brain._on_ws_message("amy", "first message")
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    await asyncio.sleep(0.1)
    brain._on_ws_message("ben", "second message")

    # While the second message is held, ui_state must stay EXPANDED and the
    # first expansion must be gone
    await wait_for(lambda: ("type", "from ben") in tracker.events)
    for _ in range(10):
        await asyncio.sleep(0.02)
        assert brain.ui_state == UIState.EXPANDED
        assert tracker.active == 1
    assert tracker.max_active == 1

    # The first one ended before the second one started
    assert tracker.events.index(("end", "from amy")) < tracker.events.index(("start", "from ben"))
    # The first message was cancelled during its hold — never collapsed normally
    assert ("collapse", FrameType.MESSAGES) not in tracker.events[:tracker.events.index(("end", "from amy"))]

    await wait_for(lambda: tracker.active == 0 and brain.ui_state == UIState.NORMAL, timeout=3.0)
    assert brain._expand_tasks == set()


async def test_same_iteration_requests_only_last_runs(env):
    brain, tracker = env
    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=0.05)
    await asyncio.wait_for(asyncio.gather(a, b, return_exceptions=True), timeout=2.0)
    assert a.cancelled()
    starts = [e for e in tracker.events if e[0] == "start"]
    assert starts == [("start", "B")]  # A's expand_frame never even started
    assert tracker.max_active == 1
    assert brain.ui_state == UIState.NORMAL


async def test_new_request_cancels_running_and_queued(env):
    brain, tracker = env
    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=5.0)  # queued behind A's finally
    c = brain._start_expansion(FrameType.TIME, lines("C"), duration=0.05)
    await asyncio.wait_for(asyncio.gather(a, b, c, return_exceptions=True), timeout=2.0)
    assert a.cancelled() and b.cancelled()
    starts = [e[1] for e in tracker.events if e[0] == "start"]
    assert starts == ["A", "C"]
    assert tracker.max_active == 1


async def test_next_starts_only_after_previous_finally(env):
    """The previous expansion's finally (ui reset + re-render) must finish
    before the next one sets EXPANDING."""
    brain, tracker = env
    real_render = brain._render_normal

    def recording_render():
        tracker.events.append(("render", brain.ui_state))
        real_render()
    brain._render_normal = recording_render

    brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=0.05)
    await asyncio.wait_for(b, timeout=2.0)

    ev = tracker.events
    a_end = ev.index(("end", "A"))
    b_start = ev.index(("start", "B"))
    # A's re-render ran (with ui_state NORMAL) between A's end and B's start
    assert ("render", UIState.NORMAL) in ev[:a_end]
    assert a_end < b_start


async def test_old_task_never_resets_state_after_new_expanding(env):
    brain, tracker = env
    states = []
    real_typewriter = brain._typewriter

    async def typewriter_probe(content_lines, box_right):
        result = await real_typewriter(content_lines, box_right)
        states.append((content_lines[0][0], brain.ui_state))
        return result
    brain._typewriter = typewriter_probe

    brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.2)
    await asyncio.sleep(0)
    brain._start_expansion(FrameType.ALBUM, lines("B"), duration=0.2)
    await wait_for(lambda: any(s[0] == "B" for s in states))
    for _ in range(5):
        assert brain.ui_state == UIState.EXPANDED
        await asyncio.sleep(0.02)


async def test_single_tap_dismisses_expansion(env):
    brain, tracker = env
    task = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.0)
    assert task.cancelled()
    assert brain.ui_state == UIState.NORMAL
    # The tap only dismissed — it did not start anything new
    assert [e for e in tracker.events if e[0] == "start"] == [("start", "A")]


async def test_tap_during_collapse_dismisses(env):
    brain, tracker = env
    task = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    brain.ui_state = UIState.COLLAPSING
    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.0)
    assert task.cancelled()


async def test_spotify_auth_cancels_all_expansions(env):
    brain, tracker = env
    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    brain._on_spotify_auth({"access_token": "x"})
    await asyncio.wait_for(asyncio.gather(a, return_exceptions=True), timeout=2.0)
    assert a.cancelled()
    assert brain.ui_state == UIState.NORMAL


def _configure_welcome(brain):
    brain.device_config = {"user_name": "nat", "telegram_opted_in": True}
    brain.crew_config = {"invite_code": "ABC-123"}
    brain._generate_qr_image = lambda **kwargs: None


async def test_welcome_cancelled_by_message_without_deadlock(env, monkeypatch):
    brain, tracker = env
    monkeypatch.setattr(leeloo_brain, "EXPANDED_HOLD_DURATION", 0.1)
    _configure_welcome(brain)

    welcome = brain._launch_welcome_screen()
    assert brain._welcome_qr_active
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    assert ("start", "welcome nat!") in tracker.events

    brain._on_ws_message("amy", "hi")
    await asyncio.wait_for(asyncio.gather(welcome, return_exceptions=True), timeout=2.0)
    assert welcome.cancelled()
    assert brain._welcome_qr_active is False

    # The message expansion gets the lock (no deadlock) and completes
    await wait_for(lambda: ("end", "from amy") in tracker.events, timeout=3.0)
    assert tracker.max_active == 1
    assert brain.ui_state == UIState.NORMAL


async def test_welcome_cancelled_by_tap(env):
    brain, tracker = env
    _configure_welcome(brain)
    welcome = brain._launch_welcome_screen()
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(welcome, return_exceptions=True), timeout=2.0)
    assert welcome.cancelled()
    assert brain._welcome_qr_active is False
    assert brain.ui_state == UIState.NORMAL


async def test_welcome_cancelled_before_start_clears_flag(env):
    """If the welcome task is replaced before it ever runs, its finally never
    executes — the flag must still be cleared or the display stays frozen."""
    brain, tracker = env
    _configure_welcome(brain)
    welcome = brain._launch_welcome_screen()
    other = brain._start_expansion(FrameType.MESSAGES, lines("B"), duration=0.05)
    await asyncio.wait_for(asyncio.gather(welcome, other, return_exceptions=True), timeout=2.0)
    assert welcome.cancelled()
    assert brain._welcome_qr_active is False
    assert ("start", "welcome nat!") not in tracker.events


async def test_welcome_runs_both_phases_inside_one_lock(env):
    """The welcome flow awaits expand_frame twice while holding the expansion
    lock — must not deadlock."""
    brain, tracker = env
    _configure_welcome(brain)
    brain._should_show_spotify_qr = lambda: True
    brain._generate_spotify_oauth_url = lambda: "https://example.invalid"

    real_expand = brain.expand_frame

    async def short_expand(frame_type, content_lines, duration=0.0):
        await real_expand(frame_type, content_lines, duration=0.05)
    brain.expand_frame = short_expand

    welcome = brain._launch_welcome_screen()
    await asyncio.wait_for(welcome, timeout=3.0)
    starts = [e[1] for e in tracker.events if e[0] == "start"]
    assert starts == ["welcome nat!", "one more thing..."]
    assert brain._welcome_qr_active is False


async def test_expand_frame_has_no_self_cancel(env):
    """expand_frame must not touch self._expand_task (the old self-cancel)."""
    import inspect
    src = inspect.getsource(LeelooBrain.expand_frame)
    assert "_expand_task" not in src


async def test_all_call_sites_use_helper():
    """No caller may create an expand_frame task directly any more."""
    import inspect
    import re
    src = inspect.getsource(leeloo_brain)
    assert not re.search(r"(create_task|ensure_future)\(\s*self\.expand_frame", src)
