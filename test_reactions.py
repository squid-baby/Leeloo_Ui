#!/usr/bin/env python3
"""
Unit tests for ASCII reaction animations
"""

import unittest
from display.ascii_reactions import ASCIIReactions
from display.reaction_animator import ReactionAnimator
from gadget_display import LEELOODisplay


class TestASCIIReactions(unittest.TestCase):
    """Test ASCII art frame definitions"""

    def test_love_frames_exist(self):
        """Love reaction has 3 frames"""
        frames = ASCIIReactions.LOVE_FRAMES
        self.assertEqual(len(frames), 3)
        self.assertIn(':::', frames[0])  # Has heart pattern

    def test_fire_frames_exist(self):
        """Fire reaction has 3 frames"""
        frames = ASCIIReactions.FIRE_FRAMES
        self.assertEqual(len(frames), 3)
        self.assertIn(')', frames[0])  # Has flame pattern

    def test_haha_frames_exist(self):
        """Haha reaction has 3 frames"""
        frames = ASCIIReactions.HAHA_FRAMES
        self.assertEqual(len(frames), 3)
        self.assertIn('_____', frames[0])  # Has face outline

    def test_wave_frames_exist(self):
        """Wave reaction has 3 frames"""
        frames = ASCIIReactions.WAVE_FRAMES
        self.assertEqual(len(frames), 3)
        self.assertIn('|', frames[0])  # Has hand/arm pattern

    def test_all_reaction_types(self):
        """All 4 reaction types defined"""
        reactions = ['love', 'fire', 'haha', 'wave']
        for reaction in reactions:
            frames = ASCIIReactions.get_frames(reaction)
            self.assertGreater(len(frames), 0, f"{reaction} has no frames")
            self.assertEqual(len(frames), 3, f"{reaction} should have 3 frames")

    def test_unknown_reaction_type(self):
        """Unknown reaction type returns empty list"""
        frames = ASCIIReactions.get_frames('unknown')
        self.assertEqual(frames, [])

    def test_get_all_reaction_types(self):
        """Get all reaction types"""
        types = ASCIIReactions.get_all_reaction_types()
        self.assertEqual(types, ['love', 'fire', 'haha', 'wave'])


class TestReactionOverlay(unittest.TestCase):
    """Test reaction overlay rendering"""

    def test_reaction_overlay(self):
        """Overlay renders without error"""
        display = LEELOODisplay(preview_mode=True)

        # Should not crash
        display.draw_reaction_overlay(
            ASCIIReactions.LOVE_FRAMES[0],
            "Test message"
        )

        self.assertTrue(display.overlay_active)

    def test_clear_overlay(self):
        """Clear overlay sets flag"""
        display = LEELOODisplay(preview_mode=True)

        display.draw_reaction_overlay(
            ASCIIReactions.LOVE_FRAMES[0],
            "Test"
        )
        self.assertTrue(display.overlay_active)

        display.clear_overlay()
        self.assertFalse(display.overlay_active)


class TestReactionAnimator(unittest.TestCase):
    """Test animation playback"""

    def test_animation_callback(self):
        """Animator calls render callback"""
        call_count = 0

        def mock_render(ascii_art, message):
            nonlocal call_count
            call_count += 1

        animator = ReactionAnimator(mock_render)
        animator.frame_duration = 0.01  # Speed up for tests
        animator.hold_duration = 0.01

        success = animator.play_reaction('love', 'TestUser')

        # Should call render for each frame (3 frames × 2 cycles = 6 times, + 1 hold)
        self.assertTrue(success)
        self.assertGreater(call_count, 5)

    def test_unknown_reaction_returns_false(self):
        """Unknown reaction type returns False"""
        def mock_render(ascii_art, message):
            pass

        animator = ReactionAnimator(mock_render)
        success = animator.play_reaction('unknown', 'TestUser')

        self.assertFalse(success)

    def test_message_variations(self):
        """Different reactions have different messages"""
        messages_received = []

        def mock_render(ascii_art, message):
            messages_received.append(message)

        animator = ReactionAnimator(mock_render)
        animator.frame_duration = 0.01
        animator.hold_duration = 0.01

        animator.play_reaction('love', 'Amy')
        self.assertIn('Amy loved this', messages_received[0])

        messages_received.clear()
        animator.play_reaction('fire', 'Ben')
        self.assertIn('Ben thinks this is fire', messages_received[0])


if __name__ == '__main__':
    unittest.main()
