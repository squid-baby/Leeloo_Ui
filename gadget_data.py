#!/usr/bin/env python3
"""
LEELOO Data - Persistent storage for hang times and other LEELOO state
Stores data in JSON format on the Pi.

Hang datetimes are stored as ISO strings, either:
  - naive (legacy): device-local wall-clock time, or
  - timezone-aware: one shared instant for devices in different time zones.

The file is parsed once and re-read only when it changes on disk
(mtime / size / inode), so the display loop can call
format_countdown_display() every frame without touching the SD card.
"""

import copy
import json
import os
import tempfile
import threading
from datetime import datetime, tzinfo
from typing import Optional, Dict, Any

# Default path on Pi - can be overridden via environment variable.
# Functions read this module variable at call time (tests monkeypatch it).
DATA_FILE_PATH = os.environ.get("LEELOO_DATA_PATH", "/home/pi/leeloo_data.json")

_EMPTY_DISPLAY = {
    'date_str': '_/__',
    'time_str': '__:__',
    'countdown_str': '__:__',
    'slider_boxes': 0
}

_lock = threading.RLock()
_cache_path: Optional[str] = None
_cache_sig = None                      # (mtime_ns, size, inode) or None = no file
_cache_data: Dict[str, Any] = {}
_cache_valid = False

# Local zone used for display and for interpreting naive datetimes.
# None = system local time.
_local_tz: Optional[tzinfo] = None


def set_local_tz(tz: Optional[tzinfo]) -> None:
    """Set the device's local time zone (e.g. ZoneInfo('America/Chicago')).
    None = use the system local time zone."""
    global _local_tz
    _local_tz = tz


def get_local_tz() -> Optional[tzinfo]:
    """The configured local zone (None = system local time)."""
    return _local_tz


def _as_aware(dt: datetime) -> datetime:
    """Aware version of dt; a naive dt is taken as device-local time."""
    if dt.tzinfo is not None and dt.utcoffset() is not None:
        return dt
    dt = dt.replace(tzinfo=None)
    if _local_tz is not None:
        return dt.replace(tzinfo=_local_tz)
    return dt.astimezone()  # naive -> system local zone


def _to_local(dt: datetime) -> datetime:
    """dt expressed in the configured local zone."""
    return _as_aware(dt).astimezone(_local_tz)  # astimezone(None) = system local


def _file_sig(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _read_file(path: str) -> Dict[str, Any]:
    """Parse the data file from disk (no caching)."""
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_shared() -> Dict[str, Any]:
    """Cached parsed file. Read-only: callers must not mutate the result."""
    global _cache_path, _cache_sig, _cache_data, _cache_valid
    path = DATA_FILE_PATH
    with _lock:
        sig = _file_sig(path)
        if not _cache_valid or _cache_path != path or _cache_sig != sig:
            _cache_data = _read_file(path) if sig is not None else {}
            _cache_path, _cache_sig, _cache_valid = path, sig, True
        return _cache_data


def load_data() -> Dict[str, Any]:
    """Load LEELOO data from JSON file. Returns empty dict if file doesn't exist.
    The result is a private copy and may be modified freely."""
    with _lock:
        return copy.deepcopy(_load_shared())


def save_data(data: Dict[str, Any]) -> bool:
    """Save LEELOO data to JSON file atomically (temp file + rename).
    Returns True on success."""
    global _cache_path, _cache_sig, _cache_data, _cache_valid
    path = DATA_FILE_PATH
    tmp_path = None
    with _lock:
        try:
            directory = os.path.dirname(os.path.abspath(path))
            fd, tmp_path = tempfile.mkstemp(prefix='.leeloo_data.', suffix='.tmp', dir=directory)
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
        except Exception as e:
            print(f"Error saving LEELOO data: {e}")
            _cache_valid = False
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return False
        # Refresh the cache from what was just written (no re-read needed)
        _cache_data = copy.deepcopy(data)
        _cache_path, _cache_sig, _cache_valid = path, _file_sig(path), True
        return True


def get_next_hang() -> Optional[Dict[str, Any]]:
    """Get the next scheduled hang time."""
    with _lock:
        hang = _load_shared().get('next_hang')
        return copy.deepcopy(hang) if hang else hang


def set_next_hang(hang_datetime: datetime, set_by: str = "unknown") -> bool:
    """
    Set the next hang time.

    Args:
        hang_datetime: When the hang is scheduled. Stored as isoformat()
                       unchanged: aware stays aware, naive means device-local.
        set_by: Who set it ("telegram", "voice", etc.)

    Returns:
        True on success
    """
    with _lock:
        data = load_data()
        data['next_hang'] = {
            'datetime': hang_datetime.isoformat(),
            'set_by': set_by,
            'created_at': datetime.now().isoformat()
        }
        return save_data(data)


def clear_next_hang() -> bool:
    """Clear the next hang time."""
    with _lock:
        data = load_data()
        if 'next_hang' not in data:
            return True  # nothing to do, no write
        del data['next_hang']
        return save_data(data)


def format_countdown_display(now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Format hang data for display.

    Args:
        now: current time (default: the real clock). Naive = device-local.

    Returns:
        dict with 'date_str', 'time_str', 'countdown_str', 'slider_boxes'
        (date/time shown in the configured local zone)
    """
    with _lock:
        hang_data = _load_shared().get('next_hang')
        raw = hang_data.get('datetime') if isinstance(hang_data, dict) else None

    # Default/empty state
    if not raw:
        return dict(_EMPTY_DISPLAY)

    try:
        hang_dt = _as_aware(datetime.fromisoformat(raw))
    except (ValueError, TypeError):
        return dict(_EMPTY_DISPLAY)

    now_aware = _as_aware(now) if now is not None else datetime.now().astimezone()
    delta = hang_dt - now_aware

    # If hang time has passed, auto-clear it
    if delta.total_seconds() < 0:
        clear_next_hang()
        return dict(_EMPTY_DISPLAY)

    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    total_hours = total_seconds // 3600

    local_dt = _to_local(hang_dt)

    # Format date as M/DD
    date_str = f"{local_dt.month}/{local_dt.day:02d}"

    # Format time as H:MMam/pm
    hour12 = local_dt.hour % 12 or 12
    time_str = f"{hour12}:{local_dt.minute:02d}{'am' if local_dt.hour < 12 else 'pm'}"

    # Calculate slider boxes (12 boxes = 7 days, each box ~14 hours)
    slider_boxes = min(12, int(total_hours / 14))

    # Format countdown based on remaining time
    if days == 0:
        # Under 24 hours: show hours:minutes
        countdown_str = f"{hours}h:{minutes:02d}m"
    else:
        # 24+ hours: show days:hours
        countdown_str = f"{days}d:{hours}h"

    return {
        'date_str': date_str,
        'time_str': time_str,
        'countdown_str': countdown_str,
        'slider_boxes': slider_boxes
    }


if __name__ == "__main__":
    # Test the module
    from datetime import timedelta

    print("Testing leeloo_data module...")

    # Test with a hang 2 days from now
    test_time = datetime.now() + timedelta(days=2, hours=4, minutes=20)
    print(f"\nSetting test hang for: {test_time}")
    set_next_hang(test_time, "test")

    display = format_countdown_display()
    print(f"Date: {display['date_str']}")
    print(f"Time: {display['time_str']}")
    print(f"Countdown: {display['countdown_str']}")
    print(f"Slider boxes: {display['slider_boxes']}/12")

    # Test clear
    print("\nClearing hang...")
    clear_next_hang()

    display = format_countdown_display()
    print(f"After clear - Date: {display['date_str']}, Countdown: {display['countdown_str']}")
