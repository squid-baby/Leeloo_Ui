#!/usr/bin/env python3
"""
Quick script to display an image on the framebuffer.
Usage: python3 show_on_display.py <image_path>
"""

import sys
import os
from PIL import Image

def write_to_framebuffer(image_path, fb_device='/dev/fb1'):
    """Write an image to the framebuffer."""

    # Load and resize image to 480x320
    img = Image.open(image_path)
    img = img.resize((480, 320), Image.Resampling.LANCZOS)
    img = img.convert('RGB')

    # Convert to raw RGB565 format
    with open(fb_device, 'wb') as fb:
        # Write row by row to avoid tearing
        for y in range(320):
            for x in range(480):
                r, g, b = img.getpixel((x, y))
                # Convert RGB888 to RGB565
                r5 = (r >> 3) & 0x1F
                g6 = (g >> 2) & 0x3F
                b5 = (b >> 3) & 0x1F
                rgb565 = (r5 << 11) | (g6 << 5) | b5
                # Write as little-endian 16-bit
                fb.write(rgb565.to_bytes(2, byteorder='little'))

    print(f"✅ Displayed {image_path} on {fb_device}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 show_on_display.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]

    if not os.path.exists(image_path):
        print(f"❌ Image not found: {image_path}")
        sys.exit(1)

    write_to_framebuffer(image_path)
