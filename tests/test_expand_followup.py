"""Expansion follow-ups: taps and cancels while an expansion is in its finally.

Bug: expand_frame's finally sets ui_state = NORMAL, then awaits a slow
Spotify refresh (0.5-10s) and the re-render in the executor. A tap in that
window after an expansion ENDED NORMALLY was swallowed (it only "dismissed"
the already-collapsed expansion, no voice started) and its cancel landed in
the finally's await, skipping the re-render — the collapsed frame stayed on
screen.

Fix: the refresh + re-render is one executor job that always completes even
when the task is cancelled, and a tap while every live expansion is only
finishing (ended normally, nothing queued) starts voice.

Same stubs as test_expand.py (FrameAnimator, typewriter, framebuffer).
"""
import asyncio

from PIL import Image

from leeloo_brain import UIState, FrameType
import test_expand
from test_expand import (
    lines, wait_for, _arm_voice, _slow_refresh_and_record_renders,
    _configure_welcome,
)

env = test_expand.env  # reuse the pytest fixture


def _renders(tracker):
    return [e for e in tracker.events if e[0] == "render"]


async def _wait_in_finally(brain, tracker, task, frame_type=FrameType.MESSAGES):
    """Wait until task collapsed normally and sits in its slow refresh"""
    await wait_for(lambda: ("collapse", frame_type) in tracker.events)
    await asyncio.sleep(0.05)
    assert brain.ui_state == UIState.NORMAL
    assert not task.done()
    assert _renders(tracker) == []


async def test_tap_after_natural_collapse_starts_voice_and_rerenders(env):
    """Reviewer repro: the tap must start voice, not be swallowed, and the
    collapsed expansion's re-render must still run."""
    brain, tracker = env
    sessions = _arm_voice(brain)
    _slow_refresh_and_record_renders(brain, tracker, delay=0.6)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await _wait_in_finally(brain, tracker, a)

    await brain._handle_single_tap()  # user taps to talk
    assert sessions == [UIState.NORMAL]  # voice started

    await asyncio.wait_for(asyncio.gather(a, return_exceptions=True), timeout=3.0)
    assert not a.cancelled()  # the tap did not cancel the finishing expansion
    assert len(_renders(tracker)) == 1  # re-render was not aborted


async def test_cancel_during_finally_still_rerenders_before_next(env):
    """A new expansion arriving while the previous one is in its finally
    cancels it; the refresh + re-render must still complete, and the new one
    must start only after it (the lock is held until the job is done)."""
    brain, tracker = env
    _slow_refresh_and_record_renders(brain, tracker, delay=0.4)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await _wait_in_finally(brain, tracker, a)

    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=0.05)
    await asyncio.wait_for(asyncio.gather(a, b, return_exceptions=True), timeout=3.0)

    assert a.cancelled()  # the cancel still propagates after the job
    ev = tracker.events
    first_render = ev.index(("render", UIState.NORMAL))
    assert first_render < ev.index(("start", "B"))
    assert tracker.max_active == 1


async def test_repeated_cancels_during_finally_still_rerender(env):
    brain, tracker = env
    _slow_refresh_and_record_renders(brain, tracker, delay=0.3)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await _wait_in_finally(brain, tracker, a)
    for _ in range(3):
        a.cancel()
        await asyncio.sleep(0.02)

    await asyncio.wait_for(asyncio.gather(a, return_exceptions=True), timeout=3.0)
    assert a.cancelled()
    assert _renders(tracker) == [("render", UIState.NORMAL)]


async def test_render_runs_even_if_music_refresh_fails(env):
    brain, tracker = env
    _slow_refresh_and_record_renders(brain, tracker)

    def broken_update_music():
        raise RuntimeError("spotify down")
    brain._update_music = broken_update_music

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await asyncio.wait_for(a, timeout=3.0)
    assert _renders(tracker) == [("render", UIState.NORMAL)]


async def test_tap_with_queued_expansion_behind_finishing_one_dismisses(env):
    """A finishing expansion with another one queued behind it: the tap
    still dismisses (the queued one would draw over the voice session)."""
    brain, tracker = env
    sessions = _arm_voice(brain)
    _slow_refresh_and_record_renders(brain, tracker, delay=0.4)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await _wait_in_finally(brain, tracker, a)
    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=5.0)
    await asyncio.sleep(0.02)

    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(a, b, return_exceptions=True), timeout=3.0)
    assert sessions == []
    assert b.cancelled()
    assert ("start", "B") not in tracker.events
    assert ("render", UIState.NORMAL) in tracker.events  # A still re-rendered


async def test_tap_in_welcome_phase_one_finally_dismisses(env):
    """The welcome flow draws again after phase 1's finally (phase 2), so a
    tap there must dismiss the whole flow — and phase 1's re-render must
    still run."""
    brain, tracker = env
    sessions = _arm_voice(brain)
    _configure_welcome(brain)
    brain._should_show_spotify_qr = lambda: True
    brain._generate_spotify_oauth_url = lambda: "https://example.invalid"
    _slow_refresh_and_record_renders(brain, tracker, delay=0.4)

    real_expand = brain.expand_frame

    async def short_expand(frame_type, content_lines, duration=0.0):
        await real_expand(frame_type, content_lines, duration=0.05)
    brain.expand_frame = short_expand

    welcome = brain._launch_welcome_screen()
    await _wait_in_finally(brain, tracker, welcome)

    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(welcome, return_exceptions=True), timeout=3.0)
    assert sessions == []
    assert welcome.cancelled()
    assert ("start", "one more thing...") not in tracker.events
    assert _renders(tracker) == [("render", UIState.NORMAL)]


async def test_finishing_marker_cleared(env):
    brain, tracker = env
    _slow_refresh_and_record_renders(brain, tracker, delay=0.2)
    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=0.05)
    await _wait_in_finally(brain, tracker, a)
    assert a in brain._expand_finishing
    assert brain._has_live_expansion() and not brain._has_running_expansion()
    await asyncio.wait_for(a, timeout=3.0)
    await wait_for(lambda: brain._expand_tasks == set())
    assert brain._expand_finishing == set()
    assert brain._expand_single == set()


async def test_render_normal_skips_while_voice_owns_screen(env):
    """Why starting voice during the finally is safe: _render_normal
    re-checks ui_state (under the display lock) and doesn't paint."""
    brain, tracker = env
    brain.display.render = lambda *a, **k: Image.new("RGB", (480, 320))
    before = tracker.fb_writes
    brain.ui_state = UIState.LISTENING
    brain._render_normal()
    assert tracker.fb_writes == before
    brain.ui_state = UIState.NORMAL
    brain._render_normal()
    assert tracker.fb_writes == before + 1


async def test_second_tap_after_dismiss_during_finally_starts_voice(env):
    """Review finding: tap to dismiss, then tap to talk while the dismissed
    expansion is still in its (shielded) refresh + re-render, with nothing
    queued. The second tap must start voice, not be swallowed."""
    brain, tracker = env
    sessions = _arm_voice(brain)
    _slow_refresh_and_record_renders(brain, tracker, delay=1.0)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)

    await brain._handle_single_tap()  # dismiss
    assert sessions == []
    await asyncio.sleep(0.1)
    assert brain.ui_state == UIState.NORMAL
    assert brain._expand_tasks == {a} and not a.done()  # only finishing

    await brain._handle_single_tap()  # tap to talk
    assert sessions == [UIState.NORMAL]

    await asyncio.wait_for(asyncio.gather(a, return_exceptions=True), timeout=3.0)
    assert a.cancelled()
    assert len(_renders(tracker)) == 1  # dismissed one still re-rendered


async def test_replaced_expansion_finishing_still_dismisses_queued(env):
    """A dismissed/replaced expansion counts as finishing, but the one
    queued behind it is running: the tap dismisses that one."""
    brain, tracker = env
    sessions = _arm_voice(brain)
    _slow_refresh_and_record_renders(brain, tracker, delay=0.5)

    a = brain._start_expansion(FrameType.MESSAGES, lines("A"), duration=5.0)
    await wait_for(lambda: brain.ui_state == UIState.EXPANDED)
    b = brain._start_expansion(FrameType.ALBUM, lines("B"), duration=5.0)
    await wait_for(lambda: a in brain._expand_finishing)
    assert brain._has_running_expansion()  # b is queued

    await brain._handle_single_tap()
    await asyncio.wait_for(asyncio.gather(a, b, return_exceptions=True), timeout=3.0)
    assert sessions == []
    assert b.cancelled()
    assert ("start", "B") not in tracker.events
