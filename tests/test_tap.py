"""TapManager: tap classification timing, callback isolation, error logging.

Uses a fake ADXL345 and a virtual clock: leeloo_tap's `time` and `asyncio`
module references are swapped for proxies whose time()/sleep() run on
simulated time, so a 0.3s double tap is reproduced exactly and instantly.
"""
import asyncio
import heapq
import itertools
import types

import pytest

import leeloo_tap
from leeloo_tap import TapManager

BASELINE = (0.0, 0.0, 9.8)
SPIKE = (0.0, 0.0, 14.0)        # delta 4.2 m/s² >> TAP_THRESHOLD
SPIKE_DURATION = 0.05           # a knuckle tap: one or two 100Hz samples


class VirtualClock:
    """Discrete-event clock: sleep() parks the caller until advance() reaches
    its wake time. Real event-loop iterations are run between wake-ups so
    every task woken at a given instant gets to run."""

    def __init__(self):
        self.now = 1000.0
        self._sleepers = []
        self._seq = itertools.count()
        self._real_sleep = asyncio.sleep

    def time(self):
        return self.now

    async def sleep(self, delay, result=None):
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + max(delay, 0), next(self._seq), fut))
        await fut
        return result

    async def _settle(self):
        for _ in range(20):
            await self._real_sleep(0)

    async def advance(self, duration):
        end = self.now + duration
        await self._settle()
        while self._sleepers and self._sleepers[0][0] <= end:
            wake, _, fut = heapq.heappop(self._sleepers)
            self.now = max(self.now, wake)
            if not fut.done():
                fut.set_result(None)
            await self._settle()
        self.now = end
        await self._settle()


class AsyncioProxy:
    """leeloo_tap.asyncio replacement: virtual sleep, everything else real."""

    def __init__(self, clock):
        self._clock = clock

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @property
    def sleep(self):
        return self._clock.sleep


class FakeAccel:
    """ADXL345 stand-in. Each tap is a spike lasting SPIKE_DURATION;
    optional ringing models the enclosure rebound after the spike."""

    def __init__(self, clock, ring=0.0):
        self.clock = clock
        self.taps = []
        self.ring = ring
        self.fail = False
        self._ring_reads = 0

    def tap_at(self, t):
        self.taps.append(t)

    @property
    def acceleration(self):
        if self.fail:
            raise OSError("I2C read failed")
        now = self.clock.now
        for t in self.taps:
            if t <= now < t + SPIKE_DURATION:
                return SPIKE
            if t + SPIKE_DURATION <= now < t + SPIKE_DURATION + self.ring:
                # rebound: consecutive reads swing high/low
                self._ring_reads += 1
                return SPIKE if self._ring_reads % 2 == 1 else BASELINE
        return BASELINE


@pytest.fixture
def clock(monkeypatch):
    clk = VirtualClock()
    monkeypatch.setattr(leeloo_tap, "time", types.SimpleNamespace(time=clk.time))
    monkeypatch.setattr(leeloo_tap, "asyncio", AsyncioProxy(clk))
    return clk


def make_manager(clock, callback, ring=0.0):
    mgr = TapManager(callback=callback)
    mgr.accel = FakeAccel(clock, ring=ring)
    mgr._prev_magnitude = 9.8
    return mgr


async def run_sequence(clock, mgr, tap_offsets, total=3.0):
    """Start the poll loop, schedule taps at the given offsets (seconds from
    start) and run simulated time forward."""
    start = clock.now
    for off in tap_offsets:
        mgr.accel.tap_at(start + off)
    loop_task = asyncio.ensure_future(mgr.start())
    await clock.advance(total)
    mgr.stop()
    await clock.advance(0.2)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


def test_timing_constants_allow_human_double_tap():
    # Blind time and debounce must both be clearly shorter than the window,
    # leaving room for a 2nd tap 0.2-0.4s after the first
    assert leeloo_tap.BLIND_TIME < 0.2
    assert leeloo_tap.BLIND_TIME <= leeloo_tap.DEBOUNCE_TIME < 0.2
    assert leeloo_tap.DOUBLE_TAP_WINDOW > 0.4 + leeloo_tap.POLL_INTERVAL
    assert leeloo_tap.TRIPLE_TAP_WINDOW > 0.4 + leeloo_tap.POLL_INTERVAL
    # Single-tap latency stays reasonable
    assert leeloo_tap.DOUBLE_TAP_WINDOW < 0.8


async def test_single_tap(clock):
    events = []

    async def on_tap(tap_type):
        events.append((tap_type, clock.now))

    mgr = make_manager(clock, on_tap)
    start = clock.now
    await run_sequence(clock, mgr, [0.1])
    assert [e[0] for e in events] == ["single_tap"]
    latency = events[0][1] - (start + 0.1)
    assert latency < 0.8


@pytest.mark.parametrize("gap", [0.2, 0.3, 0.4])
async def test_double_tap(clock, gap):
    events = []

    async def on_tap(tap_type):
        events.append(tap_type)

    mgr = make_manager(clock, on_tap)
    await run_sequence(clock, mgr, [0.1, 0.1 + gap])
    assert events == ["double_tap"]


async def test_triple_tap(clock):
    events = []

    async def on_tap(tap_type):
        events.append(tap_type)

    mgr = make_manager(clock, on_tap)
    await run_sequence(clock, mgr, [0.1, 0.4, 0.7])
    assert events == ["triple_tap"]


async def test_rebound_is_not_a_second_tap(clock, capsys):
    """Ringing that is still visible on the first read after the blind period
    is rejected by DEBOUNCE_TIME, not counted as a 2nd tap."""
    events = []

    async def on_tap(tap_type):
        events.append(tap_type)

    mgr = make_manager(clock, on_tap, ring=0.08)
    await run_sequence(clock, mgr, [0.1])
    assert events == ["single_tap"]
    out = capsys.readouterr().out
    assert out.count("Hit!") >= 2          # the rebound was seen...
    assert out.count("Raw tap #") == 1     # ...but debounced


async def test_two_separate_single_taps(clock):
    events = []

    async def on_tap(tap_type):
        events.append(tap_type)

    mgr = make_manager(clock, on_tap)
    await run_sequence(clock, mgr, [0.1, 1.5], total=3.0)
    assert events == ["single_tap", "single_tap"]


async def test_raw_tap_does_not_cancel_running_callback(clock):
    """Old bug: the callback was awaited inside _pending_task, which the next
    raw tap cancels — a tap during voice recording killed the interaction."""
    release = asyncio.Event()
    started = []
    finished = []
    cancelled = []

    async def on_tap(tap_type):
        started.append(tap_type)
        try:
            await release.wait()  # e.g. a voice interaction in progress
            finished.append(tap_type)
        except asyncio.CancelledError:
            cancelled.append(tap_type)
            raise

    mgr = make_manager(clock, on_tap)
    start = clock.now
    mgr.accel.tap_at(start + 0.1)
    mgr.accel.tap_at(start + 1.5)  # tap while the first callback still runs
    loop_task = asyncio.ensure_future(mgr.start())
    await clock.advance(3.0)

    assert started == ["single_tap", "single_tap"]
    assert cancelled == []
    assert finished == []
    assert len(mgr._callback_tasks) == 2  # strong refs kept while running

    release.set()
    await clock.advance(0.1)
    assert finished == ["single_tap", "single_tap"]
    assert cancelled == []
    assert mgr._callback_tasks == set()

    mgr.stop()
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


async def test_callback_exception_is_logged_not_raised(clock, capsys):
    async def on_tap(tap_type):
        raise RuntimeError("boom")

    mgr = make_manager(clock, on_tap)
    await run_sequence(clock, mgr, [0.1])
    assert "Callback error" in capsys.readouterr().out
    assert mgr._callback_tasks == set()


async def test_sync_callback_still_supported(clock):
    events = []
    mgr = make_manager(clock, events.append)
    await run_sequence(clock, mgr, [0.1, 0.4])
    assert events == ["double_tap"]


def test_read_errors_logged_first_and_every_100th(capsys):
    mgr = TapManager(callback=None)
    mgr.accel = types.SimpleNamespace()  # has no .acceleration → AttributeError
    for _ in range(250):
        assert mgr._check_tap() is False
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "Accelerometer read error" in ln]
    assert len(lines) == 3  # errors #1, #100, #200
    assert "#1:" in lines[0] and "#100:" in lines[1] and "#200:" in lines[2]


def test_drain_read_errors_are_logged(capsys):
    mgr = TapManager(callback=None)
    mgr.accel = types.SimpleNamespace()
    mgr._drain_reading()
    assert "Accelerometer read error #1" in capsys.readouterr().out
