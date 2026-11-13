"""Shared test setup for LEELOO.

All tests run without Pi hardware. Hardware modules (LEDs, accelerometer,
framebuffer, microphone) run in mock mode or are replaced by fakes.

Run all tests from the repository root:
    python3 -m pytest
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Point every module at a throwaway data directory BEFORE they are imported.
_TMP_HOME = tempfile.mkdtemp(prefix="leeloo-test-")
os.environ["LEELOO_HOME"] = _TMP_HOME
os.environ["LEELOO_MESSAGES_PATH"] = os.path.join(_TMP_HOME, "messages.json")
os.environ["LEELOO_DATA_PATH"] = os.path.join(_TMP_HOME, "leeloo_data.json")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test-secret")
