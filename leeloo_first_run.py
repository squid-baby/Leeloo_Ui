#!/usr/bin/env python3
"""
LEELOO First Run Setup Screen
Shows welcome message and WiFi setup instructions.
Right side shows the scancode placeholder instead of album art.
"""

import os
import sys
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Screen dimensions
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320
FB_PATH = '/dev/fb1'

# Colors - match the main UI
COLORS = {
    'bg': '#1A1D2E',
    'lavender': '#A7AFD4',
    'purple': '#9C93DD',
    'green': '#719253',
    'tan': '#C2995E',
    'white': '#FFFFFF',
    'rose': '#D6697F',
}


def rgb_to_rgb565(img):
    """Convert PIL image to RGB565"""
    arr = np.array(img)
    r = (arr[:, :, 0] >> 3).astype(np.uint16)
    g = (arr[:, :, 1] >> 2).astype(np.uint16)
    b = (arr[:, :, 2] >> 3).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def write_to_fb(img, fb_path=FB_PATH):
    """Write image to framebuffer"""
    rgb565 = rgb_to_rgb565(img)
    with open(fb_path, 'wb') as fb:
        fb.write(rgb565.tobytes())


def get_ap_ssid():
    """Get the AP SSID this device will broadcast"""
    try:
        from wifi_manager import get_device_id
        device_id = get_device_id()
        return f"LEE-{device_id}"
    except Exception:
        return "LEE-XXXX"


def create_first_run_screen(wifi_name=None):
    """Create the first-run setup screen"""
    if wifi_name is None or wifi_name == "leeloo":
        wifi_name = get_ap_ssid()

    img = Image.new('RGB', (SCREEN_WIDTH, SCREEN_HEIGHT), COLORS['bg'])
    draw = ImageDraw.Draw(img)

    # Load fonts
    try:
        font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except:
        font_header = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_med = ImageFont.load_default()
        font_large = ImageFont.load_default()

    # Try to load the scancode placeholder image for right side
    scancode_paths = [
        '/home/pi/leeloo-ui/leeloo_empty_scancode.png',
        os.path.join(os.path.dirname(__file__), 'leeloo_empty_scancode.png'),
        'leeloo_empty_scancode.png'
    ]

    # Calculate right panel dimensions (same as album art area)
    frame_padding = 5
    frame_y = 4
    frame_height = 318 - frame_y

    scancode_loaded = False
    scancode_x = SCREEN_WIDTH  # Will be set when image loads

    for scancode_path in scancode_paths:
        if os.path.exists(scancode_path):
            try:
                scancode_img = Image.open(scancode_path).convert('RGB')
                # Scale to fit height like album art
                aspect_ratio = scancode_img.width / scancode_img.height
                scancode_height = frame_height - (frame_padding * 2)
                scancode_width = int(scancode_height * aspect_ratio)

                # Resize
                scancode_img = scancode_img.resize((scancode_width, scancode_height), Image.Resampling.LANCZOS)

                # Position (right-aligned with frame)
                frame_width = scancode_width + (frame_padding * 2)
                frame_x = SCREEN_WIDTH - frame_width - 2
                scancode_x = frame_x + frame_padding

                # Draw frame
                draw.rectangle([frame_x, frame_y, frame_x + frame_width, frame_y + frame_height],
                               outline=COLORS['green'], width=2)

                # Paste scancode image
                img.paste(scancode_img, (scancode_x, frame_y + frame_padding))
                scancode_loaded = True

                # Set left panel right edge
                info_panel_right = frame_x - 5
                break
            except Exception as e:
                print(f"Could not load scancode image: {e}")

    if not scancode_loaded:
        # Fallback - just draw empty green frame on right
        frame_width = 160
        frame_x = SCREEN_WIDTH - frame_width - 2
        draw.rectangle([frame_x, frame_y, frame_x + frame_width, frame_y + frame_height],
                       outline=COLORS['green'], width=2)
        info_panel_right = frame_x - 5

    # ========== LEFT PANEL ==========
    box_right = info_panel_right - 5

    # Main container border with header
    draw.rectangle([2, 4, info_panel_right, 318], outline=COLORS['lavender'], width=1)

    # Header text
    header_y = 4
    draw.rectangle([6, header_y - 5, 155, header_y + 7], fill=COLORS['bg'])
    draw.text((8, header_y - 4), "LEELOO v1.0", font=font_header, fill=COLORS['lavender'])
    draw.text((90, header_y - 4), "first run", font=font_header, fill=COLORS['green'])

    # ===== WELCOME BOX ===== (takes up most of left panel)
    y = 16
    box_height = 290
    draw.rectangle([7, y, box_right, y + box_height], outline=COLORS['lavender'], width=2)

    # Label
    label = "welcome"
    try:
        label_width = font_small.getlength(label)
    except:
        label_width = len(label) * 7
    label_x = 13
    draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
    draw.text((label_x, y - 5), label, font=font_small, fill=COLORS['lavender'])

    # Welcome content
    content_x = 15
    content_y = y + 20

    # "Hi, I'm Leeloo."
    draw.text((content_x, content_y), "Hi, I'm Leeloo.", font=font_large, fill=COLORS['white'])
    content_y += 30

    # "Let's get set up."
    draw.text((content_x, content_y), "Let's get set up.", font=font_med, fill=COLORS['lavender'])
    content_y += 40

    # Instructions
    draw.text((content_x, content_y), "On your phone:", font=font_med, fill=COLORS['tan'])
    content_y += 25

    draw.text((content_x, content_y), "1. Go to WiFi settings", font=font_small, fill=COLORS['white'])
    content_y += 20

    draw.text((content_x, content_y), "2. Connect to:", font=font_small, fill=COLORS['white'])
    content_y += 22

    # WiFi network name (highlighted)
    wifi_display = f'"{wifi_name}"'
    draw.text((content_x + 10, content_y), wifi_display, font=font_large, fill=COLORS['green'])
    content_y += 32

    draw.text((content_x, content_y), "3. A setup page will open", font=font_small, fill=COLORS['white'])
    content_y += 35

    # Waiting indicator
    draw.text((content_x, content_y), "Waiting for connection...", font=font_small, fill=COLORS['purple'])

    # Animated dots area (will be updated in animation loop)
    content_y += 20
    draw.text((content_x, content_y), "...", font=font_med, fill=COLORS['purple'])

    return img


def show_first_run(wifi_name=None):
    """Display the first run screen"""
    img = create_first_run_screen(wifi_name)
    write_to_fb(img)


def animate_waiting(wifi_name=None, duration=30):
    """Animate the waiting dots"""
    dots = [".", "..", "...", "....", ".....", "......"]
    dot_idx = 0

    start_time = time.time()
    while time.time() - start_time < duration:
        img = create_first_run_screen(wifi_name)
        draw = ImageDraw.Draw(img)

        # Redraw the dots in animation
        try:
            font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        except:
            font_med = ImageFont.load_default()

        # Position matches the "..." in create_first_run_screen
        draw.rectangle([15, 252, 100, 270], fill=COLORS['bg'])  # Clear old dots
        draw.text((15, 253), dots[dot_idx], font=font_med, fill=COLORS['purple'])

        write_to_fb(img)

        dot_idx = (dot_idx + 1) % len(dots)
        time.sleep(0.5)


if __name__ == '__main__':
    wifi_name = sys.argv[1] if len(sys.argv) > 1 else "leeloo"
    show_first_run(wifi_name)
