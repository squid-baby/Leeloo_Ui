#!/usr/bin/env python3
"""Quick tap+LED integration test for LEELOO Phase 1"""
import asyncio
import sys
sys.path.insert(0, '/home/pi/leeloo-ui')

from leeloo_tap import TapManager
from leeloo_led import LEDManager

async def main():
    led = LEDManager()

    async def on_tap(tap_type):
        print(f">>> TAP EVENT: {tap_type}", flush=True)
        if tap_type == 'single_tap':
            await led.ack()
        elif tap_type == 'double_tap':
            asyncio.create_task(led.nudge(duration=5))
        elif tap_type == 'triple_tap':
            await led.message_received()

    tap = TapManager(callback=on_tap)
    print("=== Tap+LED Test Running ===", flush=True)
    print("Tap the device! Single=green flash, Double=cyan breathe, Triple=purple blinks", flush=True)
    print("Test runs for 5 minutes.", flush=True)

    try:
        await asyncio.wait_for(tap.start(), timeout=300)
    except (asyncio.TimeoutError, KeyboardInterrupt):
        pass
    finally:
        tap.stop()
        led.off_sync()
        print("Test ended.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
