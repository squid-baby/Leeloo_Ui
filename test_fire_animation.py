#!/usr/bin/env python3
"""
Quick test: Fire animation in expanded message frame
"""

import sys
import time
import asyncio
sys.path.insert(0, '/home/pi/leeloo-ui')

from display.ascii_reactions import ASCIIReactions
from display.frame_animator import FrameType, FrameAnimator
from gadget_display import LeelooDisplay, COLORS
from PIL import Image, ImageDraw, ImageFont

FB_PATH = '/dev/fb1'
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320

def write_to_framebuffer(img, fb_path=FB_PATH):
    """Write PIL image to framebuffer (fast row-by-row)"""
    import numpy as np
    arr = np.array(img)
    r = arr[:, :, 0].astype(np.uint16)
    g = arr[:, :, 1].astype(np.uint16)
    b = arr[:, :, 2].astype(np.uint16)
    rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

    with open(fb_path, 'wb') as fb:
        for row in range(SCREEN_HEIGHT):
            row_data = rgb565[row, :].astype(np.uint16).tobytes()
            fb.write(row_data)

async def test_fire_animation():
    """Show fire animation in expanded message frame"""

    # Initialize display
    display = LeelooDisplay()

    # Create base image
    img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
    draw = ImageDraw.Draw(img)

    # Load font
    try:
        font_normal = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 20)
    except:
        font_normal = ImageFont.load_default()
        font_large = ImageFont.load_default()

    # Expand the message frame
    print("[TEST] Expanding message frame...")
    box_right = 250  # Typical position
    animator = FrameAnimator(display, box_right=box_right, fb_path=FB_PATH)
    await animator.async_expand(FrameType.MESSAGES)

    # Get fire animation frames
    fire_frames = ASCIIReactions.get_frames('fire')
    print(f"[TEST] Loaded {len(fire_frames)} fire animation frames")

    # Animate fire in the expanded frame
    print("[TEST] Playing fire animation...")

    content_x = 7 + 10
    content_y = 16 + 25

    # Show message text first
    msg_img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
    msg_draw = ImageDraw.Draw(msg_img)

    # Draw expanded message frame border (lavender)
    msg_draw.rectangle([7, 16, box_right, SCREEN_HEIGHT - 16], outline=COLORS['lavender'], width=2)

    # Message header
    msg_draw.text((content_x, content_y), "that's fire!", font=font_large, fill=COLORS['lavender'])

    write_to_framebuffer(msg_img)
    await asyncio.sleep(1)

    # Animate fire (loop 3 times)
    for loop in range(3):
        for frame_idx, frame_ascii in enumerate(fire_frames):
            # Redraw frame
            frame_img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
            frame_draw = ImageDraw.Draw(frame_img)

            # Draw border
            frame_draw.rectangle([7, 16, box_right, SCREEN_HEIGHT - 16], outline=COLORS['lavender'], width=2)

            # Message text
            frame_draw.text((content_x, content_y), "that's fire!", font=font_large, fill=COLORS['lavender'])

            # Fire ASCII art
            y_offset = content_y + 40
            for line in frame_ascii.strip().split('\n'):
                frame_draw.text((content_x + 20, y_offset), line, font=font_normal, fill=COLORS['rose'])
                y_offset += 18

            write_to_framebuffer(frame_img)
            await asyncio.sleep(0.2)  # 200ms per frame

    print("[TEST] Animation complete, holding...")
    await asyncio.sleep(2)

    # Collapse
    print("[TEST] Collapsing message frame...")
    await animator.async_collapse(FrameType.MESSAGES)

    print("[TEST] Done!")

if __name__ == "__main__":
    asyncio.run(test_fire_animation())
