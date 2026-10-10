"""LEDManager recording guard + ambient task hygiene (runs in LED mock mode).

CLAUDE.md gotcha 11: WS2812B DMA and INMP441 I2S DMA conflict, so no LED
activity may happen while arecord runs.
"""
import asyncio

import pytest

import leeloo_led
from leeloo_led import LEDManager, GREEN


def _live_ambient_tasks():
    """All live ambient breathe tasks on the loop (catches orphans that
    self._ambient_task no longer points at)."""
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
    await manager.off_all()
    for t in _live_ambient_tasks():
        t.cancel()
    await asyncio.sleep(0)


async def test_transient_animations_skipped_during_recording(led):
    await led.set_ambient("idle")
    assert len(_live_ambient_tasks()) == 1

    await led._cancel_ambient()
    await led.listening()
    await led.recording_started()
    assert led.is_recording
    assert _live_ambient_tasks() == []

    writes = []
    led._set_color = lambda color: writes.append(color)

    # Every one of these must return immediately without touching the LEDs
    await asyncio.wait_for(led.message_received(), timeout=0.5)
    await asyncio.wait_for(led.music_received(), timeout=0.5)
    await asyncio.wait_for(led.nudge(duration=30), timeout=0.5)
    await asyncio.wait_for(led.error(), timeout=0.5)
    await asyncio.wait_for(led.ack(), timeout=0.5)
    await asyncio.wait_for(led.off(), timeout=0.5)
    await led.set_ambient("playing")
    await asyncio.sleep(0.15)  # give any wrongly-started task time to draw

    assert writes == []
    assert not _alive(led._animation_task)
    assert not _alive(led._ambient_task)
    assert _live_ambient_tasks() == []
    # Desired ambient state is remembered for later
    assert led._ambient_state == "playing"


async def test_processing_task_during_recording_returns(led):
    await led.listening()
    await led.recording_started()
    task = asyncio.ensure_future(led.processing())
    await asyncio.wait_for(task, timeout=0.5)
    assert not _alive(led._animation_task)


async def test_recording_finished_restores_ambient(led):
    await led.set_ambient("idle")
    await led._cancel_ambient()
    await led.listening()
    await led.recording_started()
    await led.set_ambient("playing")
    assert _live_ambient_tasks() == []

    led.recording_finished()
    assert not led.is_recording
    await asyncio.sleep(0)
    assert len(_live_ambient_tasks()) == 1
    assert led._ambient_state == "playing"


async def test_recording_finished_without_ambient_starts_nothing(led):
    await led.listening()
    await led.recording_started()
    led.recording_finished()
    await asyncio.sleep(0)
    assert _live_ambient_tasks() == []


async def test_recording_started_kills_ambient_restarted_by_cancelled_animation(led):
    """A transient animation cancelled by listening() restarts ambient in its
    finally block — recording_started() must stop it and restore green."""
    await led.set_ambient("idle")
    nudge = asyncio.ensure_future(led.nudge(duration=30))
    await asyncio.sleep(0.05)
    assert _alive(led._animation_task)

    await led._cancel_ambient()
    await led.listening()
    await asyncio.sleep(0)

    colors = []
    real_set_color = led._set_color
    led._set_color = lambda c: (colors.append(c), real_set_color(c))

    await led.recording_started()
    assert _live_ambient_tasks() == []
    assert not _alive(led._animation_task)
    if colors:
        assert colors[-1] == GREEN  # listening colour restored before arecord
    await asyncio.wait_for(nudge, timeout=0.5)


async def test_recording_starts_while_animation_is_setting_up(led):
    """An animation that was cancelling the previous one when recording began
    must not start afterwards."""
    await led.set_ambient("idle")
    msg = asyncio.ensure_future(led.message_received())
    await asyncio.sleep(0)  # message_received is now awaiting _cancel_ambient
    await led.recording_started()
    await asyncio.wait_for(msg, timeout=1.0)
    assert not _alive(led._animation_task)
    assert _live_ambient_tasks() == []


async def test_error_racing_processing_leaves_one_ambient_task(led):
    await led.set_ambient("idle")
    processing = asyncio.ensure_future(led.processing())
    await asyncio.sleep(0.05)
    error = asyncio.ensure_future(led.error())
    await asyncio.wait_for(error, timeout=2.0)
    await asyncio.wait_for(processing, timeout=1.0)
    await asyncio.sleep(0.05)
    live = _live_ambient_tasks()
    assert len(live) == 1
    assert live[0] is led._ambient_task


async def test_off_racing_processing_leaves_one_ambient_task(led):
    await led.set_ambient("playing")
    processing = asyncio.ensure_future(led.processing())
    await asyncio.sleep(0.05)
    await led.off()
    await asyncio.wait_for(processing, timeout=1.0)
    await asyncio.sleep(0.05)
    assert len(_live_ambient_tasks()) == 1


async def test_start_ambient_task_is_idempotent(led):
    led._ambient_state = "idle"
    led._start_ambient_task()
    first = led._ambient_task
    led._start_ambient_task()
    led._start_ambient_task()
    assert led._ambient_task is first
    await asyncio.sleep(0)
    assert len(_live_ambient_tasks()) == 1


async def test_cancel_ambient_does_not_orphan_restarted_task(led):
    """If someone restarts ambient while _cancel_ambient awaits the old task,
    the new task must be cancelled too (not silently forgotten)."""
    led._ambient_state = "idle"
    led._start_ambient_task()
    await asyncio.sleep(0)
    old = led._ambient_task

    def restart_when_old_done(_t):
        # Runs right after the old task finishes, before _cancel_ambient resumes
        led._start_ambient_task()

    old.add_done_callback(restart_when_old_done)
    await led._cancel_ambient()
    await asyncio.sleep(0)
    assert led._ambient_task is None
    assert _live_ambient_tasks() == []
