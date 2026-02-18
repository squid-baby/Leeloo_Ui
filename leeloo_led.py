#!/usr/bin/env python3
"""
LEELOO LED Manager — Controls 3 WS2812B NeoPixel LEDs on GPIO 12

LED behaviors:
- ack:              quick green flash (100ms)
- listening:        solid green while mic is recording
- processing:       pulsing green breathe while waiting for STT/LLM
- message_received: 3 purple blinks
- music_received:   3 green blinks
- nudge:            cyan breathe on/off for 30 seconds
- error:            red flash
- off:              all off
- set_ambient:      persistent background breathe (blue=idle, purple=playing)
                    automatically resumes after any transient animation
"""

import asyncio
import time
import math

# NeoPixel imports — will fail on non-Pi, handled gracefully
try:
    import board
    import neopixel
    HARDWARE_AVAILABLE = True
except (ImportError, NotImplementedError):
    HARDWARE_AVAILABLE = False
    print("[LED] NeoPixel hardware not available — running in mock mode")


# Color constants (GRB order for WS2812B, but neopixel lib handles RGB)
GREEN = (0, 255, 0)
PURPLE = (180, 0, 255)
CYAN = (0, 255, 255)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
OFF = (0, 0, 0)

# Ambient breathe settings
AMBIENT_PEAK = 0.5       # max brightness (50%)
AMBIENT_CYCLE_S = 8.0    # full breathe in+out cycle (seconds)


class LEDManager:
    """Async LED animation controller for 3 WS2812B LEDs"""

    def __init__(self, num_leds=3, pin=None, brightness=0.3):
        self.num_leds = num_leds
        self.brightness = brightness

        # Transient animation state
        self._current_animation = None  # name of current transient animation
        self._animation_task = None     # asyncio task for current transient animation
        self._running = False

        # Ambient background state (persists across transient animations)
        self._ambient_state = None      # "idle" | "playing" | None
        self._ambient_task = None       # asyncio task for ambient breathe

        # Hardware
        if HARDWARE_AVAILABLE:
            led_pin = pin or board.D12
            self.pixels = neopixel.NeoPixel(
                led_pin, num_leds,
                brightness=brightness,
                auto_write=True
            )
            self.pixels.fill(OFF)
        else:
            self.pixels = None

    def _set_color(self, color):
        """Set all LEDs to a color"""
        if self.pixels:
            self.pixels.fill(color)
        # Console feedback in mock mode
        if not HARDWARE_AVAILABLE:
            r, g, b = color
            if color == OFF:
                pass  # don't spam logs
            else:
                print(f"[LED] Color: ({r},{g},{b})")

    def _set_brightness_color(self, color, brightness_factor):
        """Set color with brightness multiplier (0.0 - 1.0)"""
        r, g, b = color
        dimmed = (
            int(r * brightness_factor),
            int(g * brightness_factor),
            int(b * brightness_factor)
        )
        self._set_color(dimmed)

    async def _cancel_current(self):
        """Cancel any running animation"""
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass
        self._current_animation = None

    async def _run_animation(self, name, coro):
        """Run a transient animation, cancelling any existing one.
        Ambient breathe is suspended during the animation and resumes after."""
        await self._cancel_ambient()
        await self._cancel_current()
        self._current_animation = name
        self._animation_task = asyncio.create_task(coro)
        try:
            await self._animation_task
        except asyncio.CancelledError:
            pass
        finally:
            if self._current_animation == name:
                self._set_color(OFF)
                self._current_animation = None
            # Resume ambient breathe if one was active
            if self._ambient_state:
                self._start_ambient_task()

    async def _cancel_ambient(self):
        """Cancel ambient breathe task without clearing ambient state"""
        if self._ambient_task and not self._ambient_task.done():
            self._ambient_task.cancel()
            try:
                await self._ambient_task
            except asyncio.CancelledError:
                pass
        self._ambient_task = None

    def _start_ambient_task(self):
        """Spawn the ambient breathe coroutine as a background task.
        Reads _ambient_state dynamically each tick so color changes don't
        require a cancel/restart (avoids the OFF-flash flicker)."""

        async def _breathe():
            start = time.time()
            while True:
                # Read state each tick — allows seamless color transitions
                color = BLUE if self._ambient_state == "idle" else PURPLE
                elapsed = time.time() - start
                # Sine wave: 0→peak→0 over AMBIENT_CYCLE_S seconds
                phase = (elapsed % AMBIENT_CYCLE_S) / AMBIENT_CYCLE_S  # 0.0–1.0
                brightness = AMBIENT_PEAK * math.sin(phase * math.pi)
                self._set_brightness_color(color, brightness)
                await asyncio.sleep(0.1)  # 10fps — smooth enough for 8s cycle, easy on Pi Zero

        self._ambient_task = asyncio.create_task(_breathe())

    # =========================================================================
    # PUBLIC API — Animation methods
    # =========================================================================

    async def set_ambient(self, state):
        """Set persistent background breathe state.
        state: 'idle'    → slow blue breathe (0–50%, 8s cycle)
               'playing' → slow purple breathe (same timing)
               None      → turn off ambient
        If the ambient task is already running, just updates the color in-place
        (no cancel/restart) to avoid the OFF-flash flicker."""
        if state not in ("idle", "playing", None):
            raise ValueError(f"Unknown ambient state: {state!r}")

        ambient_already_running = (
            self._ambient_task and not self._ambient_task.done()
        )

        if state and ambient_already_running:
            # Just update color — running _breathe() picks it up next tick
            if state != self._ambient_state:
                print(f"[LED] ambient → {state}")
                self._ambient_state = state
            return

        # Starting fresh or turning off — cancel everything cleanly
        await self._cancel_current()
        await self._cancel_ambient()
        self._set_color(OFF)

        self._ambient_state = state
        if state:
            print(f"[LED] ambient → {state}")
            self._start_ambient_task()
        else:
            print("[LED] ambient off")

    async def ack(self):
        """Quick green flash — tap acknowledgment (100ms)"""
        print("[LED] ack (green flash)")
        await self._cancel_ambient()
        await self._cancel_current()
        self._set_color(GREEN)
        await asyncio.sleep(0.1)
        self._set_color(OFF)
        # Resume ambient after the flash
        if self._ambient_state:
            self._start_ambient_task()

    async def listening(self):
        """Solid green — mic is recording (suspends ambient until off() is called)"""
        print("[LED] listening (solid green)")
        await self._cancel_ambient()
        await self._cancel_current()
        self._current_animation = "listening"
        self._set_color(GREEN)

    async def processing(self):
        """Pulsing green breathe — waiting for STT/LLM response"""
        print("[LED] processing (green breathe)")

        async def _breathe():
            while True:
                # Breathe in
                for i in range(0, 100, 5):
                    brightness = i / 100.0
                    self._set_brightness_color(GREEN, brightness)
                    await asyncio.sleep(0.02)
                # Breathe out
                for i in range(100, 0, -5):
                    brightness = i / 100.0
                    self._set_brightness_color(GREEN, brightness)
                    await asyncio.sleep(0.02)

        await self._run_animation("processing", _breathe())

    async def message_received(self):
        """3 purple blinks — incoming message"""
        print("[LED] message_received (purple blinks)")

        async def _blink():
            for _ in range(3):
                self._set_color(PURPLE)
                await asyncio.sleep(0.2)
                self._set_color(OFF)
                await asyncio.sleep(0.15)

        await self._run_animation("message_received", _blink())

    async def music_received(self):
        """3 green blinks — incoming shared music"""
        print("[LED] music_received (green blinks)")

        async def _blink():
            for _ in range(3):
                self._set_color(GREEN)
                await asyncio.sleep(0.2)
                self._set_color(OFF)
                await asyncio.sleep(0.15)

        await self._run_animation("music_received", _blink())

    async def nudge(self, duration=30):
        """Cyan breathe on/off — nudge/wink from a friend"""
        print(f"[LED] nudge (cyan breathe for {duration}s)")

        async def _breathe():
            start = time.time()
            while time.time() - start < duration:
                # Smooth sine wave breathing
                elapsed = time.time() - start
                brightness = (math.sin(elapsed * 2.0) + 1.0) / 2.0  # 0.0 - 1.0
                self._set_brightness_color(CYAN, brightness)
                await asyncio.sleep(0.03)

        await self._run_animation("nudge", _breathe())

    async def error(self):
        """Red flash — something went wrong"""
        print("[LED] error (red flash)")

        async def _flash():
            for _ in range(2):
                self._set_color(RED)
                await asyncio.sleep(0.15)
                self._set_color(OFF)
                await asyncio.sleep(0.1)

        await self._run_animation("error", _flash())

    async def off(self):
        """Turn off all LEDs and cancel any transient animation.
        Ambient breathe resumes if active."""
        await self._cancel_current()
        self._set_color(OFF)
        if self._ambient_state:
            self._start_ambient_task()

    async def off_all(self):
        """Turn off everything including ambient (e.g. for shutdown)"""
        self._ambient_state = None
        await self._cancel_ambient()
        await self._cancel_current()
        self._set_color(OFF)

    def off_sync(self):
        """Synchronous off — for cleanup/shutdown (kills ambient too)"""
        self._ambient_state = None
        if self._ambient_task and not self._ambient_task.done():
            self._ambient_task.cancel()
        self._ambient_task = None
        self._set_color(OFF)

    @property
    def is_animating(self):
        """Check if an animation is currently running"""
        return self._current_animation is not None


# =============================================================================
# Test / Demo
# =============================================================================

async def demo():
    """Demo all LED animations"""
    led = LEDManager()

    print("\n--- LED Animation Demo ---\n")

    print("1. Ack (tap acknowledgment)")
    await led.ack()
    await asyncio.sleep(0.5)

    print("\n2. Listening (solid green, 2s)")
    await led.listening()
    await asyncio.sleep(2)

    print("\n3. Processing (green breathe, 3s)")
    task = asyncio.create_task(led.processing())
    await asyncio.sleep(3)
    await led.off()

    print("\n4. Message received (purple blinks)")
    await led.message_received()
    await asyncio.sleep(0.5)

    print("\n5. Music received (green blinks)")
    await led.music_received()
    await asyncio.sleep(0.5)

    print("\n6. Nudge (cyan breathe, 5s demo)")
    task = asyncio.create_task(led.nudge(duration=5))
    await asyncio.sleep(5)

    print("\n7. Error (red flash)")
    await led.error()
    await asyncio.sleep(0.5)

    print("\n8. Off")
    await led.off()

    print("\n--- Demo complete ---")


if __name__ == "__main__":
    asyncio.run(demo())
