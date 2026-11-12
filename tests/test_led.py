"""LED manager invariants under random interleavings.

The hard rule (CLAUDE.md gotcha 11): no LED write may reach the hardware
while the microphone records. WS2812B and the I2S mic share the DMA
controller, and one write kills the recording.
"""
import asyncio
import random

import pytest

import leeloo_led
from leeloo_led import LEDManager

REAL_SLEEP = asyncio.sleep


class FakePixels:
    def __init__(self):
        self.led = None
        self.bad_writes = 0
        self.writes = 0

    def fill(self, color):
        self.writes += 1
        if self.led.recording and not self.led._in_listening:
            self.bad_writes += 1


def ambient_tasks():
    return [t for t in asyncio.all_tasks()
            if not t.done() and "_start_ambient_task" in t.get_coro().__qualname__]


def make_led():
    led = LEDManager()
    led.pixels = FakePixels()
    led.pixels.led = led
    led._in_listening = False
    real_listening = led.listening

    async def listening():
        led._in_listening = True
        try:
            await real_listening()
        finally:
            led._in_listening = False
    led.listening = listening
    return led


async def fast_sleep(seconds):
    """Animation sleeps become a few loop turns (keeps the interleaving, no wall time)."""
    for _ in range(max(1, min(4, int(seconds * 100)))):
        await REAL_SLEEP(0)


async def one_sequence(seed, brain_like):
    rng = random.Random(seed)
    led = make_led()
    await led.set_ambient(rng.choice(["idle", "playing"]))
    notify = [led.ack, led.message_received, led.music_received, led.error,
              lambda: led.nudge(duration=0.3), lambda: led.set_ambient(rng.choice(["idle", "playing"]))]
    spawned = []
    max_ambient = 0
    for _ in range(rng.randint(3, 12)):
        for _ in range(rng.randint(0, 4)):                # a burst of notifications
            spawned.append(asyncio.ensure_future(rng.choice(notify)()))
        for _ in range(rng.randint(0, 3)):
            await REAL_SLEEP(0)
        if brain_like or rng.random() < 0.5:
            # A voice session, as leeloo_brain runs it
            await led.ack()
            await led._cancel_ambient()
            await led.listening()
            for _ in range(rng.randint(1, 6)):            # "recording"
                if rng.random() < 0.5:
                    spawned.append(asyncio.ensure_future(rng.choice(notify)()))
                await REAL_SLEEP(0)
                assert not ambient_tasks() or led.pixels.bad_writes == 0
            if rng.random() < 0.3:
                await led.error(end_recording=True)
            else:
                spawned.append(led.start_processing())
                for _ in range(rng.randint(0, 5)):
                    await REAL_SLEEP(0)
                await led.off()
        else:
            await rng.choice([led.off, led._cancel_ambient, led._cancel_current])()
        max_ambient = max(max_ambient, len(ambient_tasks()))
    await led.off()
    await asyncio.gather(*spawned, return_exceptions=True)
    for _ in range(30):
        await REAL_SLEEP(0)
    result = (led.pixels.bad_writes, max_ambient, len(ambient_tasks()), led._animations_active, led.recording)
    await led.off_all()
    for t in ambient_tasks():
        t.cancel()
    return result


@pytest.mark.parametrize("brain_like", [True, False])
def test_led_invariants_under_random_interleavings(monkeypatch, brain_like):
    monkeypatch.setattr(leeloo_led.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(leeloo_led, "print", lambda *a, **k: None, raising=False)

    async def go():
        failures = []
        for seed in range(600):
            bad_writes, max_ambient, ambient_end, active, recording = await one_sequence(seed, brain_like)
            if bad_writes or max_ambient > 1 or ambient_end != 1 or active != 0 or recording:
                failures.append((seed, bad_writes, max_ambient, ambient_end, active, recording))
        return failures
    failures = asyncio.run(go())
    assert not failures, f"{len(failures)} bad sequences, first: {failures[:5]}"


def test_three_notifications_in_one_tick_then_recording(monkeypatch):
    """Three blinks requested together used to leave one that nobody could
    cancel; it wrote LEDs during the recording."""
    async def go():
        led = make_led()
        await led.set_ambient("idle")
        blinks = [asyncio.ensure_future(led.message_received()) for _ in range(3)]
        await asyncio.sleep(0.05)
        await led.ack()
        await led._cancel_ambient()
        await led.listening()
        await asyncio.sleep(1.2)                 # longer than a blink animation
        bad = led.pixels.bad_writes
        await led.off()
        await asyncio.gather(*blinks, return_exceptions=True)
        await asyncio.sleep(0.05)
        n = len(ambient_tasks())
        await led.off_all()
        return bad, n
    assert asyncio.run(go()) == (0, 1)


def test_hardware_write_is_refused_while_recording():
    async def go():
        led = make_led()
        await led.listening()
        before = led.pixels.writes
        led._set_color((255, 0, 0))
        led._set_brightness_color((0, 0, 255), 0.5)
        assert led.pixels.writes == before
        await led.off()
        assert led.pixels.writes > before        # works again after recording
    asyncio.run(go())


def test_error_from_a_notification_does_not_end_the_recording_protection():
    """A failed reaction or a notification calls error() too. Only the voice
    session that owns the recording may end it."""
    async def go():
        led = make_led()
        await led.set_ambient("idle")
        await led.listening()
        await led.error()                          # from a notification: ignored
        await asyncio.sleep(0.3)
        assert led.recording is True and led.pixels.bad_writes == 0 and not ambient_tasks()
        await led.error(end_recording=True)        # from the voice session: flashes
        assert led.recording is False
        await asyncio.sleep(0.05)
        n = len(ambient_tasks())
        await led.off_all()
        return n
    assert asyncio.run(go()) == 1


def test_processing_requested_before_error_or_listening_never_runs():
    async def go():
        led = make_led()
        led.start_processing()
        await led.error(end_recording=True)        # no loop turn between
        assert led._current_animation is None
        led.start_processing()
        await led.listening()
        await asyncio.sleep(0.2)
        assert led.recording is True and led.pixels.bad_writes == 0
        await led.off_all()
    asyncio.run(go())


def test_cancelled_ack_restores_the_leds():
    async def go():
        led = make_led()
        await led.set_ambient("idle")
        task = asyncio.ensure_future(led.ack())
        await asyncio.sleep(0.03)                  # inside the 100 ms flash
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0.05)
        n = len(ambient_tasks())
        await led.off_all()
        return n
    assert asyncio.run(go()) == 1


def test_off_sync_ends_recording_state():
    async def go():
        led = make_led()
        await led.listening()
        led.off_sync()
        assert led.recording is False
    asyncio.run(go())
