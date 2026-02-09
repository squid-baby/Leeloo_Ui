"""
Reaction Animation Player for LEELOO
Orchestrates frame playback with timing
"""

import time
from typing import Callable
from .ascii_reactions import ASCIIReactions


class ReactionAnimator:
    """Play ASCII reaction animations with timing"""

    def __init__(self, renderer_callback: Callable):
        """
        Initialize the animator

        Args:
            renderer_callback: Function to call with (ascii_art, message) for each frame
        """
        self.render = renderer_callback
        self.frame_duration = 0.15  # 150ms per frame
        self.hold_duration = 1.0    # Hold final frame 1 second

    def play_reaction(self, reaction_type: str, sender_name: str):
        """
        Play a reaction animation

        Args:
            reaction_type: 'love', 'fire', 'haha', or 'wave'
            sender_name: Name to show in message ("Amy loved this")

        Returns:
            True if animation played successfully, False otherwise
        """
        frames = ASCIIReactions.get_frames(reaction_type)
        if not frames:
            print(f"⚠️  Unknown reaction: {reaction_type}")
            return False

        # Message text variations - showing in message box format
        # Format: "Ben says -" to match the architecture spec
        messages = {
            'love': f"{sender_name} says -",
            'fire': f"{sender_name} says -",
            'haha': f"{sender_name} says -",
            'wave': f"{sender_name} says -"
        }
        message = messages.get(reaction_type, f"{sender_name} says -")

        # Play animation loop (2 full cycles)
        for cycle in range(2):
            for frame in frames:
                self.render(frame, message)
                time.sleep(self.frame_duration)

        # Hold final frame
        self.render(frames[-1], message)
        time.sleep(self.hold_duration)

        # Signal animation complete
        return True
