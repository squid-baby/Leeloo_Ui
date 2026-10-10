"""Shared pytest setup for LEELOO.

Tests run on a normal computer (no Raspberry Pi hardware). Hardware modules
(board, neopixel, adafruit_adxl34x) are missing there, so LEDManager and
TapManager fall back to their built-in mock modes.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
