"""LEDManager follow-ups (LED mock mode).

1. set_ambient() must not cancel a running transient animation (nudge,
   processing breathe, listening): playback-state changes arrive any time
   from executor threads (_sync_ambient_led), and used to kill a 30s nudge.
2. Waiting for a cancelled animation/ambient task must not swallow a
   cancellation of the CALLER's own task.
"""
import asyncio

import pytest

import leeloo_led
from leeloo_led import LEDManager


def _live_ambient_tasks():
    return [
        t for t in asyncio.all_tasks()
        if not t.done()
        and "_start_ambient_task.<locals>._breathe" in t.get_coro().__qualname__
    ]


def _alive(task):
    return task is not None and not task.done()


@pytest.fixture
async def led():
    assert not leeloo_led.HARDWARE_AVAILABLE  # tests must run in mock mode
    manager = LEDManager()
    yield manager
    manager._recording = False
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()
    await asyncio.sleep(0.01)
    manager.off_sync()


async def _slow_to_stop(started, delay=0.2):
    """Animation that needs `delay` seconds to stop after being cancelled"""
    started.set()
    try:
        await asyncio.sleep(10)
    finally:
        await asyncio.sleep(delay)


# -----------------------------------------------------------------------------
# set_ambient vs transient animations
# -----------------------------------------------------------------------------

async def test_set_ambient_from_executor_does_not_kill_nudge(led):
    """Reviewer repro (exp3): a playback change during a nudge"""
    await led.set_ambient("idle")
    nudge = asyncio.ensure_future(led.nudge(duration=0.4))
    await asyncio.sleep(0.1)
    assert led.current_animation == "nudge"

    loop = asyncio.get_running_loop()

    def tick():  # what _sync_ambient_led does from the display-tick thread
        asyncio.run_coroutine_threadsafe(led.set_ambient("playing"), loop).result(1)
    await loop.run_in_executor(None, tick)
    await asyncio.sleep(0.05)

    assert led.current_animation == "nudge"
    assert not nudge.done()
    assert _live_ambient_tasks() == []
    assert led._ambient_state == "playing"

    # When the nudge ends, ambient resumes with the NEW state
    await asyncio.wait_for(nudge, timeout=2.0)
    assert led.current_animation is None
    assert len(_live_ambient_tasks()) == 1
    assert led._ambient_state == "playing"


async def test_set_ambient_does_not_kill_processing(led):
    processing = asyncio.ensure_future(led.processing())
    await asyncio.sleep(0.05)
    await led.set_ambient("idle")
    assert led.current_animation == "processing"
    assert not processing.done()
    assert _live_ambient_tasks() == []

    await led.off()  # how _voice_interaction ends the breathe
    await asyncio.wait_for(processing, timeout=1.0)
    await asyncio.sleep(0)
    assert len(_live_ambient_tasks()) == 1


async def test_set_ambient_none_during_animation_only_stores(led):
    await led.set_ambient("idle")
    nudge = asyncio.ensure_future(led.nudge(duration=0.2))
    await asyncio.sleep(0.05)
    await led.set_ambient(None)
    assert led.current_animation == "nudge" and not nudge.done()
    await asyncio.wait_for(nudge, timeout=2.0)
    assert led._ambient_state is None
    assert _live_ambient_tasks() == []


async def test_set_ambient_during_listening_keeps_listening_colour(led):
    await led.listening()
    writes = []
    real_set_color = led._set_color
    led._set_color = lambda color: (writes.append(color), real_set_color(color))
    await led.set_ambient("playing")
    assert led.current_animation == "listening"
    assert writes == []  # solid green untouched
    assert _live_ambient_tasks() == []

    await led.off()  # end of the voice session resumes ambient
    assert len(_live_ambient_tasks()) == 1
    assert led._ambient_state == "playing"


async def test_set_ambient_without_animation_still_starts(led):
    await led.set_ambient("idle")
    assert len(_live_ambient_tasks()) == 1
    await led.set_ambient(None)
    await asyncio.sleep(0)
    assert _live_ambient_tasks() == []


# -----------------------------------------------------------------------------
# Caller cancellation is never swallowed
# -----------------------------------------------------------------------------

async def test_cancel_during_error_propagates_to_caller(led):
    """Reviewer repro (exp2): _voice_interaction cancelled during
    `await self.led.error()` must not continue as if nothing happened."""
    await led.set_ambient("idle")

    async def caller():
        await led.error()
        return "continued after cancel"

    t = asyncio.ensure_future(caller())
    await asyncio.sleep(0.05)
    assert led.current_animation == "error"
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    # The animation itself stopped too, LEDs were cleaned up, ambient resumed
    assert not _alive(led._animation_task)
    assert led.current_animation is None
    assert len(_live_ambient_tasks()) == 1


async def test_superseded_animation_returns_normally(led):
    """Being replaced by another animation is not an error for its caller"""
    nudge = asyncio.ensure_future(led.nudge(duration=5))
    await asyncio.sleep(0.05)
    await led.message_received()
    assert nudge.done() and not nudge.cancelled()
    assert nudge.exception() is None


async def test_off_returns_superseded_processing_normally(led):
    processing = asyncio.ensure_future(led.processing())
    await asyncio.sleep(0.05)
    await led.off()
    await asyncio.wait_for(processing, timeout=1.0)
    assert not processing.cancelled()


async def test_cancel_current_does_not_swallow_caller_cancel(led):
    started = asyncio.Event()
    anim = asyncio.ensure_future(led._run_animation("slow", _slow_to_stop(started)))
    await started.wait()

    caller = asyncio.ensure_future(led._cancel_current())
    await asyncio.sleep(0.05)  # caller is waiting for the slow stop
    assert not caller.done()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.wait_for(anim, timeout=1.0)  # the animation still ends


async def test_cancel_ambient_does_not_swallow_caller_cancel(led):
    started = asyncio.Event()
    led._ambient_task = asyncio.ensure_future(_slow_to_stop(started))
    await started.wait()

    caller = asyncio.ensure_future(led._cancel_ambient())
    await asyncio.sleep(0.05)
    assert not caller.done()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.sleep(0.25)
    # A later call cleans up normally
    await led._cancel_ambient()
    assert led._ambient_task is None


async def test_animation_error_still_propagates(led):
    async def broken():
        raise RuntimeError("pixels gone")
    with pytest.raises(RuntimeError):
        await led._run_animation("broken", broken())
    assert led.current_animation is None


async def test_recording_started_cancels_animation_cleanly(led):
    """Recording guard still cancels a running animation without raising
    in the guard's caller, and the animation's caller returns normally."""
    nudge = asyncio.ensure_future(led.nudge(duration=5))
    await asyncio.sleep(0.05)
    await led.listening()
    await led.recording_started()
    await asyncio.wait_for(nudge, timeout=1.0)
    assert not nudge.cancelled()
    assert led.is_recording
    led.recording_finished()


# -----------------------------------------------------------------------------
# Ambient restarted by a superseded animation's finally
# -----------------------------------------------------------------------------

async def test_replacing_animation_stops_ambient_restarted_by_old_one(led):
    """Review finding: B replaces A; A's _run_animation finally restarts
    ambient (A's task is already done) before B starts. That stray breathe
    must not keep running alongside B."""
    await led.set_ambient("idle")
    nudge = asyncio.ensure_future(led.nudge(duration=5))
    await asyncio.sleep(0.05)
    msg = asyncio.ensure_future(led.message_received())
    await asyncio.sleep(0.05)
    assert led.current_animation == "message_received"
    assert _live_ambient_tasks() == []
    await asyncio.wait_for(asyncio.gather(nudge, msg), timeout=5.0)
    assert len(_live_ambient_tasks()) == 1  # resumed once B ended


async def test_set_ambient_none_during_animation_stops_stray_breathe(led):
    """set_ambient(None) while an animation runs must not leave an ambient
    task breathing (None maps to PURPLE in _breathe)."""
    await led.set_ambient("idle")
    nudge = asyncio.ensure_future(led.nudge(duration=0.3))
    await asyncio.sleep(0.05)
    # Simulate a stray breathe that slipped in alongside the animation
    stray = asyncio.ensure_future(asyncio.sleep(10))
    led._ambient_task = stray
    await led.set_ambient(None)
    assert led.current_animation == "nudge" and not nudge.done()
    assert stray.cancelled()
    await asyncio.wait_for(nudge, timeout=2.0)
    assert led._ambient_state is None
    assert _live_ambient_tasks() == []
