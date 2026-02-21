#!/usr/bin/env python3
"""
LEELOO Splash Screen
Displays immediately on boot, before anything else loads.
This runs as early as possible to hide the Linux boot process.
"""

import os
import sys
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Screen dimensions for Waveshare 3.5" LCD
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320
FB_PATH = '/dev/fb1'

# Colors
COLORS = {
    'bg': '#1A1D2E',
    'lavender': '#A7AFD4',
    'purple': '#9C93DD',
    'green': '#719253',
    'white': '#FFFFFF',
}


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple"""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_rgb565(img):
    """Convert PIL image to RGB565 for framebuffer"""
    arr = np.array(img)
    r = (arr[:, :, 0] >> 3).astype(np.uint16)
    g = (arr[:, :, 1] >> 2).astype(np.uint16)
    b = (arr[:, :, 2] >> 3).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def write_to_fb(img, fb_path=FB_PATH):
    """Write image directly to framebuffer"""
    rgb565 = rgb_to_rgb565(img)
    with open(fb_path, 'wb') as fb:
        fb.write(rgb565.tobytes())


def create_splash_screen(message="Starting...", progress=0):
    """Create the LEELOO splash screen image using branded artwork"""
    img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
    draw = ImageDraw.Draw(img)

    # Try to load the boot splash image (LeeLoo_boot.png - fills max height)
    splash_paths = [
        '/home/pi/leeloo-ui/LeeLoo_boot.png',
        os.path.join(os.path.dirname(__file__), '..', 'LeeLoo_boot.png'),
        'LeeLoo_boot.png'
    ]

    splash_loaded = False
    for splash_path in splash_paths:
        if os.path.exists(splash_path):
            try:
                splash_img = Image.open(splash_path).convert('RGB')
                # Scale to fill entire screen (480x320)
                splash_img = splash_img.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
                # Paste at origin (fills entire screen)
                img.paste(splash_img, (0, 0))
                splash_loaded = True
                break
            except Exception as e:
                print(f"Could not load splash image: {e}")

    if not splash_loaded:
        # Text-based splash fallback
        _draw_text_splash(draw)

    # Load font for progress bar
    try:
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
    except:
        font_small = ImageFont.load_default()

    # Progress bar area (overlay at bottom)
    bar_y = SCREEN_HEIGHT - 35
    bar_width = 200
    bar_height = 8
    bar_x = (SCREEN_WIDTH - bar_width) // 2

    # Progress bar background
    draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
                   outline=COLORS['lavender'], width=1)

    # Progress bar fill
    if progress > 0:
        fill_width = int((bar_width - 4) * (progress / 100))
        if fill_width > 0:
            draw.rectangle([bar_x + 2, bar_y + 2, bar_x + 2 + fill_width, bar_y + bar_height - 2],
                           fill=COLORS['green'])

    # Status message
    try:
        msg_width = font_small.getlength(message)
    except:
        msg_width = len(message) * 7
    msg_x = (SCREEN_WIDTH - msg_width) // 2
    draw.text((msg_x, bar_y + 12), message, font=font_small, fill=COLORS['lavender'])

    return img


def _draw_text_splash(draw):
    """Draw text-based splash as fallback"""
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
    except:
        font_large = ImageFont.load_default()
        font_med = ImageFont.load_default()

    # Main LEELOO text - centered
    title = "LEELOO"
    try:
        title_bbox = font_large.getbbox(title)
        title_width = title_bbox[2] - title_bbox[0]
    except:
        title_width = len(title) * 30

    title_x = (SCREEN_WIDTH - title_width) // 2
    title_y = 80

    # Draw title with a subtle glow effect
    dim_lavender = '#3A3F5E'
    draw.text((title_x + 2, title_y + 2), title, font=font_large, fill=dim_lavender)
    draw.text((title_x, title_y), title, font=font_large, fill=COLORS['lavender'])

    # Tagline
    tagline = "music for your people"
    try:
        tag_width = font_med.getlength(tagline)
    except:
        tag_width = len(tagline) * 10
    tag_x = (SCREEN_WIDTH - tag_width) // 2
    draw.text((tag_x, title_y + 60), tagline, font=font_med, fill=COLORS['purple'])


def show_splash(message="Starting...", progress=0):
    """Display splash screen"""
    img = create_splash_screen(message, progress)
    write_to_fb(img)


def animate_boot():
    """Animate the boot sequence"""
    stages = [
        ("Initializing...", 10),
        ("Loading display...", 25),
        ("Checking WiFi...", 40),
        ("Connecting...", 60),
        ("Loading UI...", 80),
        ("Ready!", 100),
    ]

    for message, progress in stages:
        show_splash(message, progress)
        time.sleep(0.3)


def clear_screen():
    """Fill screen with background color (hide any boot text)"""
    img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
    write_to_fb(img)


if __name__ == '__main__':
    # When run directly, show splash immediately
    if len(sys.argv) > 1:
        if sys.argv[1] == 'clear':
            clear_screen()
        elif sys.argv[1] == 'animate':
            animate_boot()
        else:
            # Custom message
            msg = ' '.join(sys.argv[1:])
            show_splash(msg, 50)
    else:
        show_splash("Starting...", 0)
