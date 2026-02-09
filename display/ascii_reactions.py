"""
ASCII Art Reaction Animations for LEELOO
Frame-based animation definitions for love, fire, haha, and wave reactions
"""

class ASCIIReactions:
    """Frame-based ASCII art for reaction animations"""

    # Love reaction - 3 frames (pulsing heart)
    LOVE_FRAMES = [
        # Frame 1: small heart
        """
  .:::.
 ::::::
 ::::::
  ::::
   ::
    :""",
        # Frame 2: medium heart
        """
   .::.
  ::::::
 :::::::
  :::::
   :::
    :""",
        # Frame 3: large heart
        """
  .:::.
 :::::::
 :::::::
  :::::
   :::
    :"""
    ]

    # Fire reaction - 3 frames (dancing flames)
    FIRE_FRAMES = [
        # Frame 1: right lean
        """
    )
   ) \\
  (   )
   ) (
  (   )
 __)  (__""",
        # Frame 2: center
        """
    (
   ( /
  )   (
   ( )
  )   (
 __)   (__""",
        # Frame 3: left lean
        """
    )
   ) \\
  (   )
   ) (
  (   )
 __)  (__"""
    ]

    # Haha reaction - 3 frames (bouncing laughing face)
    HAHA_FRAMES = [
        # Frame 1: normal eyes
        """
  _____
 /     \\
| ^   ^ |
|   >   |
|  ___  |
 \\_____/""",
        # Frame 2: squint eyes
        """
  _____
 /     \\
| >   < |
|   >   |
| \\___/ |
 \\_____/""",
        # Frame 3: normal eyes
        """
  _____
 /     \\
| ^   ^ |
|   >   |
|  ___  |
 \\_____/"""
    ]

    # Wave reaction - 3 frames (waving hand)
    WAVE_FRAMES = [
        # Frame 1: hand down
        """

   _
  | |
  | |
  |_|""",
        # Frame 2: hand mid
        """
     \\
    _|
   |  \\
    |  |
    |__|""",
        # Frame 3: hand up (full wave)
        """
  \\  |  /
 _ \\|/ _
|       |
 |  |  |
 |__|__|"""
    ]

    @staticmethod
    def get_frames(reaction_type: str) -> list[str]:
        """
        Get animation frames for a reaction type

        Args:
            reaction_type: One of 'love', 'fire', 'haha', 'wave'

        Returns:
            List of ASCII art frames (strings)
        """
        mapping = {
            'love': ASCIIReactions.LOVE_FRAMES,
            'fire': ASCIIReactions.FIRE_FRAMES,
            'haha': ASCIIReactions.HAHA_FRAMES,
            'wave': ASCIIReactions.WAVE_FRAMES
        }
        return mapping.get(reaction_type, [])

    @staticmethod
    def get_all_reaction_types() -> list[str]:
        """Get list of all supported reaction types"""
        return ['love', 'fire', 'haha', 'wave']
