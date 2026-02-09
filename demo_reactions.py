#!/usr/bin/env python3
"""
Interactive demo of LEELOO reaction animations
Shows all 4 reaction types in preview mode
"""

from gadget_display import LEELOODisplay
from display.reaction_animator import ReactionAnimator
import time


def demo_all_reactions():
    """Demo all reaction animations"""

    print("\n🛸 LEELOO Reaction Animations Demo")
    print("=" * 50)

    # Create display in preview mode
    display = LEELOODisplay(preview_mode=True)

    # Sample base content (music share screen)
    weather_data = {'temp': 72, 'sun': 60, 'rain': 0}
    time_data = {'time_str': '2:47 PM', 'hour': 2, 'date_str': 'Feb 4'}
    messages = [{'name': 'Amy', 'preview': 'Dinner tonight?'}]
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

    # Render base screen
    print("\n📺 Rendering base screen...")
    display.render(weather_data, time_data, messages, album_data)
    display.show()
    time.sleep(2)

    # Create animator with callback
    def render_frame(ascii_art, message):
        # Re-render base screen
        display.render(weather_data, time_data, messages, album_data)
        # Draw reaction overlay
        display.draw_reaction_overlay(ascii_art, message)
        # Show frame
        display.show()

    animator = ReactionAnimator(render_frame)

    # Demo each reaction type
    reactions = [
        ('love', 'Amy', '❤️'),
        ('fire', 'Ben', '🔥'),
        ('haha', 'Sarah', '😂'),
        ('wave', 'Mike', '👋')
    ]

    print("\n" + "=" * 50)
    print("Playing animations...")
    print("=" * 50)

    for reaction_type, sender, emoji in reactions:
        print(f"\n{emoji} Playing {reaction_type} reaction from {sender}...")
        success = animator.play_reaction(reaction_type, sender)
        if success:
            print(f"   ✓ {reaction_type.capitalize()} animation complete")
        time.sleep(1)  # Pause between demos

    print("\n" + "=" * 50)
    print("✅ Demo complete!")
    print("=" * 50)
    print("\n💡 Check /tmp/leeloo_preview.png for the final frame")
    print()


if __name__ == '__main__':
    demo_all_reactions()
