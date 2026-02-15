#!/usr/bin/env python3
"""
LEELOO Device ID — Deterministic crew code from Pi serial number

Each Pi has a unique CPU serial. We hash it to produce a stable,
collision-resistant crew code (LEELOO-XXXX). Same Pi always gets
the same code.

~923K unique codes with 4 chars from a 31-char alphabet.
"""

import hashlib
import os


# 31 chars — no I, O, 0, 1 to avoid ambiguity
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def get_pi_serial() -> str | None:
    """Read unique Pi CPU serial from /proc/cpuinfo"""
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.strip().split(':')[1].strip()
    except (FileNotFoundError, PermissionError):
        pass
    return None


def derive_crew_code(serial: str) -> str:
    """
    Deterministic crew code from Pi serial.
    Always returns same code for same Pi.
    """
    h = hashlib.sha256(serial.encode()).hexdigest()
    code = ''.join(
        ALPHABET[int(h[i:i+2], 16) % len(ALPHABET)]
        for i in range(0, 8, 2)
    )
    return f"LEELOO-{code}"


def get_device_crew_code() -> str:
    """Get this device's unique crew code"""
    serial = get_pi_serial()
    if serial:
        return derive_crew_code(serial)

    # Fallback: derive from MAC address (works on dev machines too)
    try:
        from wifi_manager import get_device_id
        mac_suffix = get_device_id()
        return f"LEELOO-{mac_suffix}"
    except ImportError:
        pass

    # Last resort: derive from hostname
    hostname = os.uname().nodename
    h = hashlib.sha256(hostname.encode()).hexdigest()
    code = ''.join(
        ALPHABET[int(h[i:i+2], 16) % len(ALPHABET)]
        for i in range(0, 8, 2)
    )
    return f"LEELOO-{code}"


if __name__ == '__main__':
    serial = get_pi_serial()
    code = get_device_crew_code()

    if serial:
        print(f"Pi serial: {serial}")
    else:
        print("Not running on Pi (no /proc/cpuinfo serial)")
        print(f"Using fallback ID source")

    print(f"Device crew code: {code}")

    # Collision test
    import random
    print(f"\nCollision test (10K random serials)...")
    codes = set()
    for _ in range(10_000):
        fake_serial = ''.join(random.choices('0123456789abcdef', k=16))
        c = derive_crew_code(fake_serial)
        codes.add(c)
    print(f"  Unique codes: {len(codes)} / 10000")
    print(f"  Collisions: {10000 - len(codes)}")
