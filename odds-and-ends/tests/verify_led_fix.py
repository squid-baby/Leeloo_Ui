#!/usr/bin/env python3
"""
Verification test after moving LED wire from Pin 11 to Pin 32
Run with: sudo python3 verify_led_fix.py
"""
import board
import neopixel
import time

def main():
    print("=" * 70)
    print("LED WIRING VERIFICATION TEST")
    print("=" * 70)
    print("\nThis test verifies the LED wire is on Pin 32 (GPIO 12)")
    print("\nExpected behavior:")
    print("  1. All LEDs RED for 2 seconds")
    print("  2. All LEDs GREEN for 2 seconds")
    print("  3. All LEDs BLUE for 2 seconds")
    print("  4. All LEDs CYAN for 2 seconds")
    print("  5. All LEDs OFF")
    print("\nIf you see these colors, the fix worked!")
    print("=" * 70)

    try:
        # Initialize NeoPixels on GPIO 12 (Pin 32)
        pixels = neopixel.NeoPixel(board.D12, 3, brightness=0.5, auto_write=True)

        input("\nPress ENTER to start the test...")

        print("\n[1/5] RED (should be bright and visible)")
        pixels.fill((255, 0, 0))
        time.sleep(2)

        print("[2/5] GREEN")
        pixels.fill((0, 255, 0))
        time.sleep(2)

        print("[3/5] BLUE")
        pixels.fill((0, 0, 255))
        time.sleep(2)

        print("[4/5] CYAN (blue-green)")
        pixels.fill((0, 255, 255))
        time.sleep(2)

        print("[5/5] OFF")
        pixels.fill((0, 0, 0))

        print("\n" + "=" * 70)
        print("✓ Test complete!")
        print("\nDid you see all the colors?")
        print("  YES → LEDs are working! The fix is complete.")
        print("  NO  → Check wiring:")
        print("        - Pin 2 (5V) → LED 5V")
        print("        - Pin 6 (GND) → LED GND")
        print("        - Pin 32 (GPIO 12) → [330Ω resistor] → LED DIN")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("\nTroubleshooting:")
        print("  1. Run with sudo: sudo python3 verify_led_fix.py")
        print("  2. Check /boot/firmware/config.txt has: dtparam=audio=off")
        print("  3. Verify neopixel library: pip3 list | grep neopixel")

if __name__ == "__main__":
    main()
