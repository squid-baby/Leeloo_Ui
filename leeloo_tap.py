#!/usr/bin/env python3
"""
LEELOO Tap Manager — ADXL345 accelerometer tap detection

Detects single, double, and triple taps via acceleration magnitude spikes.
Uses software tap detection (delta magnitude threshold) which is more
reliable than the ADXL345's hardware tap interrupt.

Timing:
- Single tap:  1 tap, no follow-up within 400ms
- Double tap:  2 taps within 400ms
- Triple tap:  3 taps within 600ms
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
DOUBLE_TAP_WINDOW = 0.50   # Max time between taps for double (increased for human speed)
TRIPLE_TAP_WINDOW = 0.80   # Max time for triple tap sequence
DEBOUNCE_TIME = 0.50       # Min time between raw tap events (must match settle)
POLL_INTERVAL = 0.04       # 25Hz polling

# Acceleration tap detection
TAP_THRESHOLD = 1.2        # Minimum delta magnitude (m/s²) to count as tap
SETTLE_TIME = 0.50         # Time to wait after spike before accepting new tap (absorb rebound)
SETTLE_DRAIN_READS = 5     # Number of readings to drain after settle (reset prev_magnitude)


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
        except Exception:
            pass

        return False

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
                await self.callback(tap_type)
            else:
                self.callback(tap_type)

    def _on_tap_detected(self):
        """Called when a raw tap is detected"""
        now = time.time()

        # Debounce
        if now - self._last_tap_time < DEBOUNCE_TIME:
            return
        self._last_tap_time = now

        self._tap_count += 1
        print(f"[TAP] Raw tap #{self._tap_count}")

        # Cancel pending resolution
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        # If triple, resolve immediately
        if self._tap_count >= 3:
            self._pending_task = asyncio.ensure_future(self._resolve_taps())
        else:
            self._pending_task = asyncio.ensure_future(self._resolve_taps())

    async def start(self):
        """Start tap monitoring (async loop)"""
        self._running = True
        print("[TAP] Monitoring started (acceleration-based detection)")

        while self._running:
            if self._check_tap():
                self._on_tap_detected()
                # Wait for physical rebound to fully dampen
                await asyncio.sleep(SETTLE_TIME)
                # Drain residual rebound readings so prev_magnitude is stable
                for _ in range(SETTLE_DRAIN_READS):
                    if self.accel:
                        try:
                            x, y, z = self.accel.acceleration
                            self._prev_magnitude = math.sqrt(x*x + y*y + z*z)
                        except Exception:
                            pass
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
