#!/usr/bin/env python3
"""
LEELOO Tap Manager — ADXL345 accelerometer tap detection

Detects single, double, and triple taps via acceleration magnitude spikes.
Uses software tap detection (delta magnitude threshold) which is more
reliable than the ADXL345's hardware tap interrupt.

How a tap sequence is resolved (all values are module constants below):
- Every spike counts as one tap, then the sensor is ignored for
  REFRACTORY_TIME so the mechanical rebound is not counted again.
- After each tap a timer of MULTI_TAP_WINDOW starts. Another tap inside the
  window extends the sequence; when the timer fires the sequence is emitted
  as 'single_tap' or 'double_tap'.
- The third tap emits 'triple_tap' immediately (no waiting).

The user callback runs in its own asyncio task. Later taps never cancel it.
"""

import asyncio
import inspect
import math
import time

# Hardware imports
try:
    import board
    import busio
    import adafruit_adxl34x
    HARDWARE_AVAILABLE = True
except (ImportError, NotImplementedError):
    HARDWARE_AVAILABLE = False
    print("[TAP] ADXL345 hardware not available — running in mock mode")


# -----------------------------------------------------------------------------
# Tuning constants (seconds unless noted). Tune these on real hardware.
# -----------------------------------------------------------------------------

# How often the accelerometer is read. 50 Hz: one I2C read is about 1 ms, so
# the event loop stays free (the loop awaits on every iteration).
POLL_INTERVAL = 0.02

# Minimum change in acceleration magnitude (m/s²) between two consecutive
# reads that counts as a tap. Lower = more sensitive.
TAP_THRESHOLD = 1.2

# Changes above this (but below TAP_THRESHOLD) are logged as near-misses,
# to help tune TAP_THRESHOLD.
NEAR_MISS_THRESHOLD = 0.6

# After a spike, further spikes are ignored for this long so that the
# mechanical rebound of ONE physical tap is not counted as a second tap.
# The sensor is still read during this time to keep the baseline current.
# Must stay clearly below the fastest double tap you want to detect
# (0.15 s between taps), minus one POLL_INTERVAL of jitter.
# Raise it if one tap is reported as a double; lower it if fast doubles are
# reported as singles.
REFRACTORY_TIME = 0.12

# How long to wait after a tap for the next tap of the same sequence.
# This is also the single-tap latency: a 'single_tap' is emitted this long
# after the tap. Must be larger than the slowest double tap you want to
# detect (0.45 s between taps) plus one POLL_INTERVAL of jitter.
MULTI_TAP_WINDOW = 0.50

# Number of taps that ends a sequence at once (emits 'triple_tap').
MAX_TAPS = 3

# Sensor read errors are printed at most once per this many seconds.
READ_ERROR_LOG_INTERVAL = 10.0

_TAP_TYPES = {1: 'single_tap', 2: 'double_tap', 3: 'triple_tap'}


class TapManager:
    """Async tap detection using ADXL345 accelerometer magnitude spikes"""

    def __init__(self, callback=None):
        """
        Args:
            callback: async or sync function called with tap_type string
                      ('single_tap', 'double_tap', 'triple_tap')
        """
        self.callback = callback
        self._running = False
        self._tap_count = 0
        self._refractory_until = 0.0     # time.monotonic() value
        self._pending_timer = None       # asyncio.TimerHandle for resolution
        self._callback_tasks = set()     # strong refs to running callbacks
        self._last_read_error_log = None
        self.accel = None
        self._prev_magnitude = 0

        # Initialize hardware
        if HARDWARE_AVAILABLE:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self.accel = adafruit_adxl34x.ADXL345(i2c)

                # Set range to ±16g and high data rate for tap sensitivity
                self.accel.range = adafruit_adxl34x.Range.RANGE_16_G
                self.accel.data_rate = adafruit_adxl34x.DataRate.RATE_100_HZ
                time.sleep(0.1)

                # Prime with initial reading
                x, y, z = self.accel.acceleration
                self._prev_magnitude = math.sqrt(x*x + y*y + z*z)

                print(f"[TAP] ADXL345 initialized (±16g, 100Hz, threshold={TAP_THRESHOLD} m/s²)")
            except Exception as e:
                print(f"[TAP] Failed to initialize ADXL345: {e}")
                self.accel = None

    # ------------------------------------------------------------------
    # Sensor
    # ------------------------------------------------------------------

    def _read_magnitude(self):
        """Read acceleration magnitude. Returns None if the read failed."""
        try:
            x, y, z = self.accel.acceleration
            return math.sqrt(x*x + y*y + z*z)
        except Exception as e:
            now = time.monotonic()
            if (self._last_read_error_log is None
                    or now - self._last_read_error_log >= READ_ERROR_LOG_INTERVAL):
                self._last_read_error_log = now
                print(f"[TAP] Sensor read error: {e}")
            return None

    def _poll_once(self):
        """One sensor poll: read, update baseline, count a tap on a spike."""
        if not self.accel:
            return

        magnitude = self._read_magnitude()
        if magnitude is None:
            return

        delta = abs(magnitude - self._prev_magnitude)
        # Always follow the signal, also during the refractory period, so the
        # baseline is already settled when the refractory period ends.
        self._prev_magnitude = magnitude

        now = time.monotonic()
        if now < self._refractory_until:
            return  # rebound of the tap we already counted

        if delta > TAP_THRESHOLD:
            print(f"[TAP] Hit! delta={delta:.2f} (threshold={TAP_THRESHOLD})")
            self._refractory_until = now + REFRACTORY_TIME
            self._on_tap_detected()
        elif delta > NEAR_MISS_THRESHOLD:
            # Log near-misses for tuning
            print(f"[TAP] Near-miss delta={delta:.2f}")

    # ------------------------------------------------------------------
    # Tap sequence resolution
    # ------------------------------------------------------------------

    def _cancel_pending_timer(self):
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None

    def _on_tap_detected(self):
        """Called once per physical tap (must run inside the event loop)."""
        self._tap_count += 1
        print(f"[TAP] Raw tap #{self._tap_count}")

        # Only the resolution TIMER is restarted. Running callbacks are
        # separate tasks and are never touched here.
        self._cancel_pending_timer()

        if self._tap_count >= MAX_TAPS:
            self._resolve_taps()
        else:
            loop = asyncio.get_running_loop()
            self._pending_timer = loop.call_later(MULTI_TAP_WINDOW, self._resolve_taps)

    def _resolve_taps(self):
        """Tap sequence is complete: emit the event."""
        self._pending_timer = None
        count = self._tap_count
        self._tap_count = 0
        if count <= 0:
            return

        tap_type = _TAP_TYPES[min(count, MAX_TAPS)]
        print(f"[TAP] Detected: {tap_type} (count={count})")
        self._dispatch(tap_type)

    def _dispatch(self, tap_type):
        """Run the user callback in its own task that taps cannot cancel."""
        if not self.callback:
            return
        task = asyncio.get_running_loop().create_task(self._run_callback(tap_type))
        # Keep a strong reference, otherwise the task can be garbage collected
        # while it is still running.
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    async def _run_callback(self, tap_type):
        try:
            result = self.callback(tap_type)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            print(f"[TAP] Callback error for {tap_type}: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self):
        """Start tap monitoring (async loop). Returns after stop()."""
        self._running = True
        print("[TAP] Monitoring started (acceleration-based detection)")

        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                # Nothing in one poll may stop tap monitoring
                print(f"[TAP] Poll error: {type(e).__name__}: {e}")
            # Always yield, every iteration (never block the event loop)
            await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        """Stop tap monitoring.

        Cancels the pending resolution timer only. Callbacks that are already
        running are left alone.
        """
        self._running = False
        self._cancel_pending_timer()
        self._tap_count = 0
        print("[TAP] Monitoring stopped")


# =============================================================================
# Test
# =============================================================================

async def demo():
    """Demo — run on Pi and tap the device"""
    events = []

    async def on_tap(tap_type):
        events.append(tap_type)
        print(f"\n  >>> TAP EVENT: {tap_type} <<<\n")

    manager = TapManager(callback=on_tap)

    print("\n--- Tap Detection Demo ---")
    print(f"ADXL345 initialized: {manager.accel is not None}")
    print(f"Threshold: {TAP_THRESHOLD} m/s² delta")
    print("Tap the device! Ctrl+C to stop.\n")

    try:
        await asyncio.wait_for(manager.start(), timeout=30)
    except (asyncio.TimeoutError, KeyboardInterrupt):
        manager.stop()

    print(f"\nDetected {len(events)} events: {events}")


if __name__ == "__main__":
    asyncio.run(demo())
