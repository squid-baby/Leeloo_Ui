"""Tests for leeloo_tap.TapManager (no hardware, plain pytest, no pytest-asyncio).

A fake accelerometer returns gravity (0, 0, 9.8) and a spike (0, 0, 30.0) at
scripted times. The real timing constants are used, so every scenario takes
real time. To keep the run short, all scenarios run concurrently in ONE event
loop (module-scoped fixture); each test then checks one scenario's result.
"""
import asyncio
import time

import pytest

import leeloo_tap
from leeloo_tap import TapManager

REST = (0.0, 0.0, 9.8)
SPIKE = (0.0, 0.0, 30.0)
SPIKE_LEN = 0.03  # seconds a scripted spike lasts


class FakeAccel:
    """Scripted accelerometer. Times are seconds after construction."""

    def __init__(self, spikes=(), fail_windows=()):
        self.t0 = time.monotonic()
        self.spikes = list(spikes)
        self.fail_windows = list(fail_windows)
        self.reads = 0
        self.failed_reads = 0

    def now(self):
        return time.monotonic() - self.t0

    @property
    def acceleration(self):
        t = self.now()
        self.reads += 1
        for start, end in self.fail_windows:
            if start <= t < end:
                self.failed_reads += 1
                raise OSError("fake I2C error")
        for s in self.spikes:
            if s <= t < s + SPIKE_LEN:
                return SPIKE
        return REST


async def _run(spikes, duration, make_callback=None, fail_windows=(), stop_at=None):
    """Run one TapManager against a scripted fake. Returns a result dict."""
    fake = FakeAccel(spikes, fail_windows)
    events = []  # (tap_type, seconds since start)
    extra = {}

    def record(tap_type):
        events.append((tap_type, fake.now()))

    callback = make_callback(record, extra) if make_callback else record
    tm = TapManager(callback=callback)
    tm.accel = fake
    tm._prev_magnitude = 9.8

    task = asyncio.create_task(tm.start())
    if stop_at is not None:
        await asyncio.sleep(stop_at)
        tm.stop()
        await asyncio.sleep(duration - stop_at)
    else:
        await asyncio.sleep(duration)
        tm.stop()
    await asyncio.wait_for(task, timeout=1.0)  # start() must return after stop()

    return {
        "types": [e[0] for e in events],
        "events": events,
        "fake": fake,
        "tm": tm,
        "extra": extra,
    }


# --- callbacks used by scenarios ---------------------------------------------

def _long_async_callback(record, extra):
    """First event simulates a long voice session (1.2 s)."""
    extra.update(started=0, finished=[], cancelled=[])

    async def cb(tap_type):
        record(tap_type)
        first = extra["started"] == 0
        extra["started"] += 1
        try:
            await asyncio.sleep(1.2 if first else 0.01)
            extra["finished"].append(tap_type)
        except asyncio.CancelledError:
            extra["cancelled"].append(tap_type)
            raise
    return cb


def _raising_sync_callback(record, extra):
    def cb(tap_type):
        record(tap_type)
        raise RuntimeError("callback exploded")
    return cb


def _raising_async_callback(record, extra):
    async def cb(tap_type):
        record(tap_type)
        await asyncio.sleep(0)
        raise RuntimeError("async callback exploded")
    return cb


def _slow_async_callback(record, extra):
    extra.update(finished=[])

    async def cb(tap_type):
        record(tap_type)
        await asyncio.sleep(0.5)
        extra["finished"].append(tap_type)
    return cb


SCENARIOS = {
    "single": lambda: _run([0.1], 0.9),
    "double_015": lambda: _run([0.1, 0.25], 1.0),
    "double_02": lambda: _run([0.1, 0.3], 1.1),
    "double_04": lambda: _run([0.1, 0.5], 1.3),
    "double_045": lambda: _run([0.1, 0.55], 1.3),
    "triple": lambda: _run([0.1, 0.3, 0.5], 1.3),
    "four_fast": lambda: _run([0.1, 0.3, 0.5, 0.7], 1.5),
    "two_singles": lambda: _run([0.1, 1.6], 2.4),
    "rebound": lambda: _run([0.1, 0.16], 0.9),
    # callback starts at ~0.6 and runs to ~1.8; taps at 0.9 and 1.1 arrive meanwhile
    "tap_during_callback": lambda: _run([0.1, 0.9, 1.1], 2.2, _long_async_callback),
    "raising_sync": lambda: _run([0.1, 1.0], 1.8, _raising_sync_callback),
    "raising_async": lambda: _run([0.1, 1.0], 1.8, _raising_async_callback),
    "sensor_errors": lambda: _run([0.6], 1.4, fail_windows=[(0.05, 0.35)]),
    # error exactly while the spike happens: tap is lost, the next one works
    "sensor_error_on_spike": lambda: _run([0.2, 0.8], 1.6, fail_windows=[(0.15, 0.3)]),
    # stop() while the callback (started ~0.6) is still running
    "stop_keeps_callback": lambda: _run([0.1], 1.4, _slow_async_callback, stop_at=0.8),
    # stop() before the single-tap window closed: no event at all
    "stop_cancels_timer": lambda: _run([0.1], 0.9, stop_at=0.3),
    "no_callback": lambda: _run([0.1], 0.9, lambda record, extra: None),
}


@pytest.fixture(scope="module")
def results():
    async def run_all():
        names = list(SCENARIOS)
        out = await asyncio.gather(*(SCENARIOS[n]() for n in names))
        return dict(zip(names, out))
    return asyncio.run(run_all())


# --- tests -------------------------------------------------------------------

def test_mock_mode_has_no_accel():
    assert leeloo_tap.HARDWARE_AVAILABLE is False
    assert TapManager().accel is None


def test_timing_constants_are_consistent():
    # refractory must be shorter than the fastest supported double tap (0.15 s)
    assert leeloo_tap.REFRACTORY_TIME + leeloo_tap.POLL_INTERVAL < 0.15
    # window must cover the slowest supported double tap (0.45 s)
    assert leeloo_tap.MULTI_TAP_WINDOW > 0.45 + leeloo_tap.POLL_INTERVAL


def test_single_tap(results):
    r = results["single"]
    assert r["types"] == ["single_tap"]
    latency = r["events"][0][1] - 0.1
    assert leeloo_tap.MULTI_TAP_WINDOW - 0.01 <= latency < leeloo_tap.MULTI_TAP_WINDOW + 0.2


@pytest.mark.parametrize("name", ["double_015", "double_02", "double_04", "double_045"])
def test_double_tap(results, name):
    assert results[name]["types"] == ["double_tap"]


def test_triple_tap_emitted_immediately(results):
    r = results["triple"]
    assert r["types"] == ["triple_tap"]
    # third tap is at 0.5 s; no waiting for a window
    assert r["events"][0][1] < 0.5 + 0.15


def test_fourth_fast_tap_starts_new_sequence(results):
    assert results["four_fast"]["types"] == ["triple_tap", "single_tap"]


def test_two_separate_singles(results):
    assert results["two_singles"]["types"] == ["single_tap", "single_tap"]


def test_rebound_spike_is_ignored(results):
    assert results["rebound"]["types"] == ["single_tap"]


def test_tap_during_long_callback_does_not_cancel_it(results):
    r = results["tap_during_callback"]
    assert r["types"] == ["single_tap", "double_tap"]
    assert r["extra"]["cancelled"] == []
    # the long first callback finished AFTER the later one, and was not cancelled
    assert r["extra"]["finished"] == ["double_tap", "single_tap"]
    assert r["tm"]._callback_tasks == set()  # references dropped when done


@pytest.mark.parametrize("name", ["raising_sync", "raising_async"])
def test_raising_callback_does_not_stop_detection(results, name):
    assert results[name]["types"] == ["single_tap", "single_tap"]


def test_sensor_read_errors_are_survived(results):
    r = results["sensor_errors"]
    assert r["fake"].failed_reads > 5
    assert r["types"] == ["single_tap"]


def test_sensor_error_during_spike_only_loses_that_tap(results):
    r = results["sensor_error_on_spike"]
    assert r["fake"].failed_reads > 0
    assert r["types"] == ["single_tap"]
    assert r["events"][0][1] > 0.8


def test_polling_loop_yields_and_keeps_polling(results):
    r = results["single"]
    # 0.9 s at 50 Hz: clearly many reads, and other scenarios ran in the same loop
    assert r["fake"].reads > 20


def test_stop_does_not_cancel_running_callback(results):
    r = results["stop_keeps_callback"]
    assert r["types"] == ["single_tap"]
    assert r["extra"]["finished"] == ["single_tap"]


def test_stop_cancels_pending_resolution(results):
    r = results["stop_cancels_timer"]
    assert r["types"] == []
    assert r["tm"]._pending_timer is None


def test_no_callback_is_fine(results):
    assert results["no_callback"]["types"] == []
