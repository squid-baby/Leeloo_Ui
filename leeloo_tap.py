#!/usr/bin/env python3
"""
LEELOO Tap Manager — ADXL345 accelerometer tap detection

Detects single, double, and triple taps via acceleration magnitude spikes.
Uses software tap detection (delta magnitude threshold) which is more
reliable than the ADXL345's hardware tap interrupt.

Timing (see constants below for the reasoning):
- Single tap:  1 tap, no follow-up within DOUBLE_TAP_WINDOW (0.55s)
- Double tap:  2nd tap within 0.55s of the 1st, no 3rd within 0.55s of the 2nd
- Triple tap:  3rd tap within 0.55s of the 2nd (resolves immediately)

The tap callback runs in its own task, so a new raw tap (which restarts
tap resolution) never cancels a callback that is still running — e.g. a
voice interaction started by the previous tap.
"""

import asyncio
import time
import math

# Hardware imports
try:
    import board
    import busio
    import adafruit_adxl34x
    HARDWARE_AVAILABLE = True
except (ImportError, NotImplementedError):
    HARDWARE_AVAILABLE = False
    print("[TAP] ADXL345 hardware not available — running in mock mode")


# Timing constants (seconds)
#
# A human double tap lands its 2nd tap ~0.2-0.4s after the 1st. The old values
# made that impossible: after every detected tap the loop was blind for
# SETTLE 0.50 + 5 drain reads x 0.04 + 0.04 = 0.74s (and DEBOUNCE was 0.50s),
# which is longer than the 0.50s double-tap window. Now:
#
#   blind time after a tap = SETTLE_TIME + SETTLE_DRAIN_READS*POLL + POLL
#                          = 0.08 + 1*0.04 + 0.04 = 0.16s
#   DEBOUNCE_TIME          = 0.18s  (> blind time: a rebound that is still
#                                    ringing on the first read after the blind
#                                    period is rejected, but a real 2nd tap at
#                                    >= 0.2s is accepted)
#   DOUBLE_TAP_WINDOW      = 0.55s  (2nd tap at 0.4s + up to one 0.04s poll of
#                                    detection jitter still fits with margin)
#
# The physical rebound of a tap on the enclosure is a few tens of ms (one or
# two 100Hz samples), so 0.08s settle + a drain read + the 0.18s debounce
# still absorbs it. Single-tap latency = DOUBLE_TAP_WINDOW = 0.55s (< 0.8s).
DOUBLE_TAP_WINDOW = 0.55   # Max gap 1st→2nd tap (also single-tap resolve delay)
TRIPLE_TAP_WINDOW = 0.55   # Max gap 2nd→3rd tap (double-tap resolve delay)
DEBOUNCE_TIME = 0.18       # Min time between accepted raw taps (rejects rebound)
POLL_INTERVAL = 0.04       # 25Hz polling

# Acceleration tap detection
TAP_THRESHOLD = 1.2        # Minimum delta magnitude (m/s²) to count as tap
SETTLE_TIME = 0.08         # Time to wait after spike before reading again (absorb rebound)
SETTLE_DRAIN_READS = 1     # Readings to drain after settle (reset prev_magnitude)

# Total time the poll loop ignores the sensor after an accepted tap
BLIND_TIME = SETTLE_TIME + SETTLE_DRAIN_READS * POLL_INTERVAL + POLL_INTERVAL

# Log accelerometer read errors on the 1st and then every Nth occurrence
READ_ERROR_LOG_EVERY = 100


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
        self._last_tap_time = 0
        self._pending_task = None
        self._callback_tasks = set()  # strong refs so running callbacks aren't GC'd
        self.accel = None
        self._prev_magnitude = 0
        self._read_errors = 0

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

                print(f"[TAP] ADXL345 initialized (±16g, 400Hz, threshold={TAP_THRESHOLD} m/s²)")
            except Exception as e:
                print(f"[TAP] Failed to initialize ADXL345: {e}")
                self.accel = None

    def _check_tap(self):
        """Check for tap by monitoring acceleration magnitude delta"""
        if not self.accel:
            return False

        try:
            x, y, z = self.accel.acceleration
            magnitude = math.sqrt(x*x + y*y + z*z)
            delta = abs(magnitude - self._prev_magnitude)
            self._prev_magnitude = magnitude

            if delta > TAP_THRESHOLD:
                print(f"[TAP] Hit! delta={delta:.2f} (threshold={TAP_THRESHOLD})")
                return True
            elif delta > 0.6:
                # Log near-misses for tuning
                print(f"[TAP] Near-miss delta={delta:.2f}")
        except Exception as e:
            self._log_read_error(e)

        return False

    def _log_read_error(self, e):
        """Log accelerometer read errors without flooding the journal
        (first error, then every READ_ERROR_LOG_EVERY-th)"""
        self._read_errors += 1
        if self._read_errors == 1 or self._read_errors % READ_ERROR_LOG_EVERY == 0:
            print(f"[TAP] Accelerometer read error #{self._read_errors}: {e}")

    def _drain_reading(self):
        """Take one reading just to reset prev_magnitude (after a tap)"""
        if not self.accel:
            return
        try:
            x, y, z = self.accel.acceleration
            self._prev_magnitude = math.sqrt(x*x + y*y + z*z)
        except Exception as e:
            self._log_read_error(e)

    async def _resolve_taps(self):
        """Wait for tap sequence to complete, then emit event"""
        if self._tap_count >= 3:
            tap_type = 'triple_tap'
        else:
            wait_time = TRIPLE_TAP_WINDOW if self._tap_count >= 2 else DOUBLE_TAP_WINDOW
            await asyncio.sleep(wait_time)

            if self._tap_count >= 3:
                tap_type = 'triple_tap'
            elif self._tap_count >= 2:
                tap_type = 'double_tap'
            else:
                tap_type = 'single_tap'

        count = self._tap_count
        self._tap_count = 0

        print(f"[TAP] Detected: {tap_type} (count={count})")

        if self.callback:
            if asyncio.iscoroutinefunction(self.callback):
                # Run in its own task: _pending_task is cancelled by every new
                # raw tap, and awaiting the callback here would cancel e.g. a
                # running voice interaction mid-recording (ui_state stuck).
                task = asyncio.ensure_future(self.callback(tap_type))
                self._callback_tasks.add(task)
                task.add_done_callback(self._on_callback_done)
            else:
                try:
                    self.callback(tap_type)
                except Exception as e:
                    print(f"[TAP] Callback error: {e}")

    def _on_callback_done(self, task):
        """Drop the reference and surface errors from a finished callback task"""
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            print(f"[TAP] Callback error: {exc!r}")

    def _on_tap_detected(self):
        """Called when a raw tap is detected.
        Returns True if the tap was accepted, False if debounced (rebound)."""
        now = time.time()

        # Debounce
        if now - self._last_tap_time < DEBOUNCE_TIME:
            return False
        self._last_tap_time = now

        self._tap_count += 1
        print(f"[TAP] Raw tap #{self._tap_count}")

        # Cancel pending resolution (only the resolver — never a running callback)
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        # _resolve_taps resolves a triple immediately, otherwise waits for more
        self._pending_task = asyncio.ensure_future(self._resolve_taps())
        return True

    async def start(self):
        """Start tap monitoring (async loop)"""
        self._running = True
        print("[TAP] Monitoring started (acceleration-based detection)")

        while self._running:
            if self._check_tap() and self._on_tap_detected():
                # Wait for physical rebound to dampen (only after an accepted
                # tap — a debounced rebound must not re-blind the loop, or a
                # real 2nd tap right after it would be missed)
                await asyncio.sleep(SETTLE_TIME)
                # Drain residual rebound readings so prev_magnitude is stable
                for _ in range(SETTLE_DRAIN_READS):
                    self._drain_reading()
                    await asyncio.sleep(POLL_INTERVAL)

            await asyncio.sleep(POLL_INTERVAL)

    def stop(self):
        """Stop tap monitoring"""
        self._running = False
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
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
