#!/usr/bin/env python3
"""
LEELOO Message Manager — Message storage, unread counts, 24h history

Stores incoming crew messages in JSON format on the Pi.
Provides unread counts for display badges and message history for readout.

Features:
- Add incoming messages with sender, text, timestamp
- Track read/unread status per message
- Get unread counts by sender
- Get 24-hour message history
- Auto-cleanup of old messages (>24h)
"""

import json
import os
import time
from typing import Dict, List, Optional, Any


# Default path on Pi
MESSAGES_FILE = os.environ.get(
    "LEELOO_MESSAGES_PATH",
    "/home/pi/leeloo-ui/messages.json"
)

# Messages older than this are cleaned up
MESSAGE_TTL = 86400  # 24 hours in seconds


class MessageManager:
    """Manages crew message storage and unread counts"""

    def __init__(self, storage_path=MESSAGES_FILE):
        self.storage_path = storage_path
        self._messages = self._load()
        self._cleanup_old()

    def _load(self) -> List[Dict[str, Any]]:
        """Load messages from JSON file"""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"[MSG] Error loading messages: {e}")
        return []

    def _save(self):
        """Save messages to JSON file"""
        try:
            with open(self.storage_path, 'w') as f:
                json.dump(self._messages, f, indent=2)
        except Exception as e:
            print(f"[MSG] Error saving messages: {e}")

    def _cleanup_old(self):
        """Remove messages older than 24 hours"""
        now = time.time()
        before = len(self._messages)
        self._messages = [
            m for m in self._messages
            if now - m.get('timestamp', 0) < MESSAGE_TTL
        ]
        removed = before - len(self._messages)
        if removed > 0:
            print(f"[MSG] Cleaned up {removed} old messages")
            self._save()

    def add_message(self, sender: str, text: str, timestamp: float = None):
        """
        Add a new incoming message.

        Args:
            sender: Name of the sender
            text: Message text
            timestamp: Unix timestamp (defaults to now)
        """
        msg = {
            'sender': sender,
            'text': text,
            'timestamp': timestamp or time.time(),
            'read': False
        }
        self._messages.append(msg)
        self._save()
        print(f"[MSG] New message from {sender}: {text[:50]}")

    def get_unread_counts(self) -> Dict[str, int]:
        """
        Get unread message counts by sender.

        Returns:
            Dict mapping sender name to unread count
        """
        counts = {}
        for m in self._messages:
            if not m.get('read', True):
                sender = m.get('sender', 'unknown')
                counts[sender] = counts.get(sender, 0) + 1
        return counts

    def get_total_unread(self) -> int:
        """Get total unread message count"""
        return sum(1 for m in self._messages if not m.get('read', True))

    def get_history_24h(self) -> List[Dict[str, Any]]:
        """
        Get all messages from the last 24 hours, newest first.

        Returns:
            List of message dicts with sender, text, timestamp, read
        """
        self._cleanup_old()
        return sorted(
            self._messages,
            key=lambda m: m.get('timestamp', 0),
            reverse=True
        )

    def get_recent(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get the most recent N messages"""
        history = self.get_history_24h()
        return history[:limit]

    def mark_all_read(self):
        """Mark all messages as read"""
        changed = False
        for m in self._messages:
            if not m.get('read', True):
                m['read'] = True
                changed = True
        if changed:
            self._save()
            print("[MSG] All messages marked as read")

    def mark_sender_read(self, sender: str):
        """Mark all messages from a specific sender as read"""
        changed = False
        for m in self._messages:
            if m.get('sender') == sender and not m.get('read', True):
                m['read'] = True
                changed = True
        if changed:
            self._save()

    def get_unread_badge(self) -> str:
        """
        Get a badge character for unread count.
        ○ for 0, ① through ⑨ for 1-9, ⑩+ for 10+
        """
        count = self.get_total_unread()
        if count == 0:
            return "○"
        elif count <= 9:
            # Unicode circled digits ① = U+2460, ② = U+2461, etc.
            return chr(0x2460 + count - 1)
        else:
            return "⑩"


# =============================================================================
# Test
# =============================================================================

if __name__ == "__main__":
    import tempfile

    # Use temp file for testing
    test_path = tempfile.mktemp(suffix='.json')
    mgr = MessageManager(storage_path=test_path)

    print("--- Message Manager Test ---\n")

    # Add some messages
    mgr.add_message("Jen", "hey! listen to this song")
    mgr.add_message("Marcus", "yo what's up")
    mgr.add_message("Jen", "it's so good")
    mgr.add_message("Dev", "coming over at 3")

    # Check counts
    print(f"\nUnread counts: {mgr.get_unread_counts()}")
    print(f"Total unread: {mgr.get_total_unread()}")
    print(f"Badge: {mgr.get_unread_badge()}")

    # Get history
    print(f"\nHistory (24h):")
    for m in mgr.get_history_24h():
        status = "●" if not m['read'] else "○"
        print(f"  {status} {m['sender']}: {m['text']}")

    # Mark read
    mgr.mark_all_read()
    print(f"\nAfter mark_all_read:")
    print(f"  Unread: {mgr.get_total_unread()}")
    print(f"  Badge: {mgr.get_unread_badge()}")

    # Cleanup
    os.unlink(test_path)
    print("\n--- Test complete ---")
