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
        self._animations_active = 0     # _run_animation calls in progress
        self._stop_epoch = 0            # counts off()/listening() calls
        self._background = set()        # strong refs for start_processing() tasks

        # Ambient background state (persists across transient animations)
        self._ambient_state = None      # "idle" | "playing" | None
        self._ambient_task = None       # asyncio task for ambient breathe

        # True while the microphone records. WS2812B (PWM DMA) and the I2S
        # mic share the DMA controller: ANY LED write during recording kills
        # the audio. While this is True no animation can start.
        self.recording = False

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

    def _set_color(self, color, force=False):
        """Set all LEDs to a color.

        This is the ONLY function that writes to the LED hardware, and it
        refuses to write while the microphone records (`force` is for the
        one solid-green write in listening(), made before recording starts).
        So no animation, however it was started or orphaned, can disturb
        the audio (WS2812B and I2S share the DMA controller)."""
        if self.recording and not force:
            return
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

    async def _run_animation(self, name, coro, epoch=None):
        """Run a transient animation, cancelling any existing one.
        Ambient breathe is suspended during the animation and resumes after."""
        if self.recording:
            coro.close()      # never animate while the microphone records
            return
        self._animations_active += 1
        if epoch is None:
            epoch = self._stop_epoch
        try:
            await self._cancel_ambient()
            await self._cancel_current()
            # Several animations can be requested in the same moment; each one
            # waits here. Make sure none is still alive before we start ours.
            while self._animation_task and not self._animation_task.done():
                await self._cancel_current()
        except BaseException:
            self._animations_active -= 1
            coro.close()
            raise
        if self.recording or self._stop_epoch != epoch:
            # While we waited, recording started or off() was called. off()
            # means "stop everything that was requested before": an animation
            # that started now could never be stopped by that caller.
            self._animations_active -= 1
            coro.close()
            if self._ambient_state and self._animations_active == 0:
                self._start_ambient_task()
            return
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
            self._animations_active -= 1
            # Resume ambient breathe if one was active — but not when another
            # animation replaced this one (ambient would write over it).
            if self._ambient_state and self._animations_active == 0:
                self._start_ambient_task()

    async def _cancel_ambient(self):
        """Cancel ambient breathe task without clearing ambient state"""
        # Loop: another LED call can start a NEW ambient task while we wait
        # for the old one to end. Never leave a live task without a reference.
        while self._ambient_task and not self._ambient_task.done():
            task = self._ambient_task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ambient_task = None

    def _start_ambient_task(self):
        """Spawn the ambient breathe coroutine as a background task.
        Reads _ambient_state dynamically each tick so color changes don't
        require a cancel/restart (avoids the OFF-flash flicker).

        Idempotent: there is never more than ONE ambient task. (Two callers
        used to start one each; the first lost its reference, could not be
        cancelled, and kept the LEDs active during every later recording.)"""
        if self.recording:
            return
        if self._ambient_task and not self._ambient_task.done():
            return

        async def _breathe():
            start = time.time()
            me = asyncio.current_task()
            while True:
                if self._ambient_task is not me:
                    return    # safety net: a task without a reference stops itself
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

        if self.recording:
            self._ambient_state = state   # applied when recording ends
            return

        if state and ambient_already_running:
            # Just update color — running _breathe() picks it up next tick
            if state != self._ambient_state:
                print(f"[LED] ambient → {state}")
                self._ambient_state = state
            return

        # Starting fresh or turning off — cancel everything cleanly
        await self._cancel_current()
        await self._cancel_ambient()
        self._ambient_state = state
        if self.recording:    # recording started while we waited
            return
        self._set_color(OFF)

        if state:
            print(f"[LED] ambient → {state}")
            self._start_ambient_task()
        else:
            print("[LED] ambient off")

    async def ack(self):
        """Quick green flash — tap acknowledgment (100ms)"""
        if self.recording:
            return
        print("[LED] ack (green flash)")
        await self._cancel_ambient()
        await self._cancel_current()
        if self.recording:    # recording started while we waited
            return
        self._set_color(GREEN)
        await asyncio.sleep(0.1)
        if self.recording:    # do not write over the solid "listening" green
            return
        self._set_color(OFF)
        # Resume ambient after the flash
        if self._ambient_state:
            self._start_ambient_task()

    async def listening(self):
        """Solid green — mic is recording (suspends ambient until off() is called)"""
        print("[LED] listening (solid green)")
        self.recording = True      # blocks every animation until off()/processing()/error()
        self._stop_epoch += 1
        await self._cancel_ambient()
        await self._cancel_current()
        self._current_animation = "listening"
        self._set_color(GREEN, force=True)   # the one write, before the microphone starts

    def start_processing(self):
        """Start the endless "processing" animation in the background.
        It runs until off() / error() / listening(). Use this instead of
        create_task(led.processing()): the animation is registered at once,
        so an off() that comes right after this call still stops it."""
        self.recording = False
        task = asyncio.ensure_future(self.processing(self._stop_epoch))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def processing(self, epoch=None):
        """Pulsing green breathe — waiting for STT/LLM response"""
        print("[LED] processing (green breathe)")
        self.recording = False

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

        await self._run_animation("processing", _breathe(), epoch)

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
        self.recording = False
        self._stop_epoch += 1      # also stops a "processing" animation that has not started yet

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
        # If a _run_animation task is active, its finally block will restart
        # ambient after cancellation — don't also restart here or we get two
        # concurrent ambient tasks and the orphaned one can't be cancelled,
        # leaving DMA active and killing I2S audio on the next tap.
        self.recording = False
        self._stop_epoch += 1
        await self._cancel_current()
        self._set_color(OFF)
        # An animation that is still closing restarts ambient itself
        if self._ambient_state and self._animations_active == 0:
            self._start_ambient_task()

    async def off_all(self):
        """Turn off everything including ambient (e.g. for shutdown)"""
        self.recording = False
        self._stop_epoch += 1
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
