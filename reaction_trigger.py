#!/usr/bin/env python3
"""
Single reaction trigger for interactive testing
Usage: python reaction_trigger.py <type> <sender>
Example: python reaction_trigger.py love Amy
"""

import sys
from gadget_display import LEELOODisplay
from display.reaction_animator import ReactionAnimator


def trigger_reaction(reaction_type: str, sender_name: str):
    """Trigger a single reaction animation with expanded message box"""

    # Create display in preview mode
    display = LEELOODisplay(preview_mode=True)

    # Sample base content (music share screen)
    weather_data = {'temp': 72, 'sun': 60, 'rain': 0}
    time_data = {'time_str': '9:06 PM', 'hour': 9, 'date_str': 'Feb 6'}

    # Message box shows the reaction sender
    # Format: "Ben says -" in the expanded messages area
    reaction_labels = {
        'love': '❤️',
        'fire': '🔥',
        'haha': '😂',
        'wave': '👋'
    }
    emoji = reaction_labels.get(reaction_type, '✨')

    # Expanded message shows sender + emoji
    messages = [
        {'name': sender_name, 'preview': f'{emoji}'}
    ]

    album_data = {
        'artist_1': 'Cinnamon',
        'artist_2': 'Chasers',
        'track': 'Doorways',
        'pushed_by': 'Amy',
        'bpm': 120,
        'duration': '2:42 s',
        'current_time': '1:30',
        'current_seconds': 90,
        'total_seconds': 162,
        'plays': 73,
    }

    # Create animator with callback
    def render_frame(ascii_art, message):
        # Re-render base screen with expanded message
        display.render(weather_data, time_data, messages, album_data)
        # Draw reaction overlay with ASCII art
        display.draw_reaction_overlay(ascii_art, message)
        # Show frame (saves to /tmp/leeloo_preview.png)
        display.show()

    animator = ReactionAnimator(render_frame)

    # Emoji mapping for console output
    emoji_map = {
        'love': '❤️',
        'fire': '🔥',
        'haha': '😂',
        'wave': '👋'
    }

    emoji = emoji_map.get(reaction_type, '✨')
    print(f"{emoji} Playing {reaction_type} reaction from {sender_name}...")

    # Play the animation
    success = animator.play_reaction(reaction_type, sender_name)

    if success:
        print(f"✓ Animation complete!")
    else:
        print(f"✗ Failed to play reaction")

    return success


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python reaction_trigger.py <type> <sender>")
        print("Types: love, fire, haha, wave")
        print("Example: python reaction_trigger.py love Amy")
        sys.exit(1)

    reaction_type = sys.argv[1]
    sender_name = sys.argv[2]

    trigger_reaction(reaction_type, sender_name)
