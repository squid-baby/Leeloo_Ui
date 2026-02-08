#!/usr/bin/env python3
"""
Gadget Data - Persistent storage for hang times and other gadget state
Stores data in JSON format on the Pi.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any

# Default path on Pi - can be overridden via environment variable
DATA_FILE_PATH = os.environ.get("GADGET_DATA_PATH", "/home/pi/gadget_data.json")


def load_data() -> Dict[str, Any]:
    """Load gadget data from JSON file. Returns empty dict if file doesn't exist."""
    try:
        with open(DATA_FILE_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_data(data: Dict[str, Any]) -> bool:
    """Save gadget data to JSON file. Returns True on success."""
    try:
        with open(DATA_FILE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving gadget data: {e}")
        return False


def get_next_hang() -> Optional[Dict[str, Any]]:
    """Get the next scheduled hang time."""
    return load_data().get('next_hang')


def set_next_hang(hang_datetime: datetime, set_by: str = "unknown") -> bool:
    """
    Set the next hang time.

    Args:
        hang_datetime: When the hang is scheduled
        set_by: Who set it ("telegram", "voice", etc.)

    Returns:
        True on success
    """
    data = load_data()
    data['next_hang'] = {
        'datetime': hang_datetime.isoformat(),
        'set_by': set_by,
        'created_at': datetime.now().isoformat()
    }
    return save_data(data)


def clear_next_hang() -> bool:
    """Clear the next hang time."""
    data = load_data()
    if 'next_hang' in data:
        del data['next_hang']
    return save_data(data)


def format_countdown_display() -> Dict[str, Any]:
    """
    Format hang data for display.

    Returns:
        dict with 'date_str', 'time_str', 'countdown_str', 'slider_boxes'
    """
    hang_data = get_next_hang()

    # Default/empty state
    if not hang_data:
        return {
            'date_str': '_/__',
            'time_str': '__:__',
            'countdown_str': '__:__',
            'slider_boxes': 0
        }

    try:
        hang_dt = datetime.fromisoformat(hang_data['datetime'])
    except (ValueError, KeyError):
        return {
            'date_str': '_/__',
            'time_str': '__:__',
            'countdown_str': '__:__',
            'slider_boxes': 0
        }

    now = datetime.now()
    delta = hang_dt - now

    # If hang time has passed, auto-clear it
    if delta.total_seconds() < 0:
        clear_next_hang()
        return {
            'date_str': '_/__',
            'time_str': '__:__',
            'countdown_str': '__:__',
            'slider_boxes': 0
        }

    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    total_hours = total_seconds // 3600

    # Format date as M/DD
    date_str = hang_dt.strftime('%-m/%d')

    # Format time as H:MMam/pm
    time_str = hang_dt.strftime('%-I:%M%p').lower()

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

    print("Testing gadget_data module...")

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
