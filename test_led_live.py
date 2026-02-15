#!/usr/bin/env python3
"""
LED diagnostic test - Run on Pi to verify LEDs work in context similar to leeloo_brain
"""
import asyncio
import sys
sys.path.insert(0, '/home/pi/leeloo-ui')

from leeloo_led import LEDManager

async def main():
    print("=" * 60)
    print("LED DIAGNOSTIC TEST")
    print("=" * 60)

    led = LEDManager(num_leds=3, brightness=0.5)
    print(f"\n✓ LEDManager initialized")
    print(f"  - Hardware available: {led.pixels is not None}")
    print(f"  - Pixels object: {type(led.pixels)}")

    print("\n[TEST 1] Quick green ack (like single tap)")
    await led.ack()
    await asyncio.sleep(1)

    print("\n[TEST 2] Solid green (listening)")
    await led.listening()
    await asyncio.sleep(2)
    await led.off()

    print("\n[TEST 3] Green breathe (processing)")
    task = asyncio.create_task(led.processing())
    await asyncio.sleep(3)
    await led.off()

    print("\n[TEST 4] Purple blinks (message)")
    await led.message_received()
    await asyncio.sleep(1)

    print("\n[TEST 5] Cyan breathe (nudge) - 5 seconds")
    await led.nudge(duration=5)

    print("\n[TEST 6] Red error flash")
    await led.error()
    await asyncio.sleep(1)

    print("\n✓ All tests complete!")
    print("If you saw the LED animations, hardware is working.")
    print("If print statements appeared but no LED lit up, there's a hardware issue.")

    await led.off()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted")
