#!/usr/bin/env python3
"""
Animation Player - Play GIF animations on the LCD framebuffer
For LEELOO tap reactions (Love/Fire)
"""

import os
import time
from PIL import Image

# Display constants (Waveshare 3.5" LCD)
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 320

def rgb_to_rgb565(r, g, b):
    """Convert RGB888 to RGB565 format for framebuffer"""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def image_to_rgb565_bytes(img):
    """Convert PIL Image to RGB565 bytes for framebuffer"""
    img = img.convert('RGB')
    pixels = img.load()
    width, height = img.size

    data = bytearray(width * height * 2)
    idx = 0

    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            rgb565 = rgb_to_rgb565(r, g, b)
            # Little-endian byte order
            data[idx] = rgb565 & 0xFF
            data[idx + 1] = (rgb565 >> 8) & 0xFF
            idx += 2

    return bytes(data)


def render_frame_to_region(frame, fb_path, x, y, bg_color=(26, 29, 46)):
    """
    Render a single frame to a specific region of the framebuffer

    Args:
        frame: PIL Image (will be converted to RGB)
        fb_path: Path to framebuffer device
        x, y: Top-left position on screen
        bg_color: Background color for transparent areas (RGB tuple)
    """
    frame = frame.convert('RGBA')
    width, height = frame.size

    # Composite onto background color for transparency
    background = Image.new('RGB', (width, height), bg_color)
    background.paste(frame, mask=frame.split()[3] if frame.mode == 'RGBA' else None)

    # Convert to RGB565
    frame_bytes = image_to_rgb565_bytes(background)

    # Write to framebuffer at offset
    with open(fb_path, 'r+b') as fb:
        for row in range(height):
            if y + row >= DISPLAY_HEIGHT:
                break
            # Calculate offset in framebuffer
            offset = ((y + row) * DISPLAY_WIDTH + x) * 2
            fb.seek(offset)

            # Write one row of the frame
            row_start = row * width * 2
            row_end = row_start + min(width, DISPLAY_WIDTH - x) * 2
            fb.write(frame_bytes[row_start:row_end])


def play_gif_animation(gif_path, fb_path='/dev/fb1', x=None, y=None,
                       duration=2.0, loops=None, center=True):
    """
    Play a GIF animation on the LCD framebuffer

    Args:
        gif_path: Path to GIF file
        fb_path: Path to framebuffer device
        x, y: Top-left position (if center=False)
        duration: How long to play in seconds (ignored if loops is set)
        loops: Number of times to loop the animation (overrides duration)
        center: If True, center the animation on screen

    Returns:
        True if played successfully, False on error
    """
    if not os.path.exists(gif_path):
        print(f"GIF not found: {gif_path}")
        return False

    try:
        gif = Image.open(gif_path)
    except Exception as e:
        print(f"Error opening GIF: {e}")
        return False

    # Get frame count
    try:
        n_frames = gif.n_frames
    except AttributeError:
        n_frames = 1

    width, height = gif.size

    # Calculate position
    if center:
        x = (DISPLAY_WIDTH - width) // 2
        y = (DISPLAY_HEIGHT - height) // 2
    else:
        x = x or 0
        y = y or 0

    # Clamp to screen bounds
    x = max(0, min(x, DISPLAY_WIDTH - 1))
    y = max(0, min(y, DISPLAY_HEIGHT - 1))

    print(f"Playing {gif_path}: {n_frames} frames, {width}x{height} at ({x}, {y})")

    start_time = time.time()
    loop_count = 0

    while True:
        for frame_num in range(n_frames):
            gif.seek(frame_num)
            frame = gif.copy()

            # Render frame
            render_frame_to_region(frame, fb_path, x, y)

            # Get frame duration from GIF metadata (default 100ms)
            frame_duration = gif.info.get('duration', 100) / 1000.0
            time.sleep(frame_duration)

            # Check exit conditions
            if loops is None:
                if time.time() - start_time >= duration:
                    return True

        loop_count += 1
        if loops is not None and loop_count >= loops:
            return True

    return True


def play_reaction(reaction_type, fb_path='/dev/fb1', y_offset=60):
    """
    Play a reaction animation (convenience function)

    Args:
        reaction_type: 'love' or 'fire'
        fb_path: Path to framebuffer
        y_offset: Y position from top of screen
    """
    assets_dir = '/home/pi/assets'

    if reaction_type == 'love':
        gif_path = os.path.join(assets_dir, 'heart_animation.gif')
    elif reaction_type == 'fire':
        gif_path = os.path.join(assets_dir, 'fire_animation.gif')
    else:
        print(f"Unknown reaction type: {reaction_type}")
        return False

    # Center horizontally, position at y_offset from top
    return play_gif_animation(
        gif_path,
        fb_path=fb_path,
        x=None,  # Will be centered
        y=y_offset,
        center=False,  # Only center X
        duration=1.5,
        loops=1
    )


# Test function
def test_animation():
    """Test animation playback with a simple generated GIF"""
    from PIL import ImageDraw

    # Create a simple test animation (pulsing circle)
    frames = []
    for i in range(10):
        size = 50 + int(20 * abs((i - 5) / 5))  # Pulse size
        img = Image.new('RGBA', (100, 100), (26, 29, 46, 255))
        draw = ImageDraw.Draw(img)

        # Draw heart-like shape (two circles + triangle)
        cx, cy = 50, 50
        r = size // 3

        # Red color
        color = (200, 50, 80, 255)

        # Simple heart approximation
        draw.ellipse([cx - r - 5, cy - r, cx + 5, cy + r], fill=color)
        draw.ellipse([cx - 5, cy - r, cx + r + 5, cy + r], fill=color)
        draw.polygon([(cx - r - 5, cy), (cx + r + 5, cy), (cx, cy + r + 10)], fill=color)

        frames.append(img)

    # Save test GIF
    test_path = '/tmp/test_heart.gif'
    frames[0].save(
        test_path,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0
    )

    print(f"Created test GIF: {test_path}")

    # Play it
    play_gif_animation(test_path, duration=3.0)


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        # Play specified GIF
        gif_path = sys.argv[1]
        duration = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
        play_gif_animation(gif_path, duration=duration)
    else:
        # Run test
        print("Usage: python3 animation_player.py <gif_path> [duration]")
        print("Running test animation...")
        test_animation()
