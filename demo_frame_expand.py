#!/usr/bin/env python3
"""
Demo: Frame Expansion Animation

Shows:
1. Standard LEELOO UI
2. Tap Weather -> expands to show detailed forecast
3. Collapses back
4. Tap Messages -> expands to show message thread
5. Collapses back
6. Tap Album -> expands to show more song details
7. Collapses back

Run on Pi: python3 demo_frame_expand.py
"""

import sys
import time
sys.path.insert(0, '/home/pi')

from gadget_display import LeelooDisplay, COLORS
from display.frame_animator import FrameAnimator, FrameType, FRAME_GEOMETRIES, EXPANDED_GEOMETRY
from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime


def write_to_framebuffer(image, fb_path='/dev/fb1'):
    """Write PIL image to framebuffer"""
    img = image.convert('RGB')
    pixels = img.load()
    width, height = img.size

    data = bytearray(width * height * 2)
    idx = 0

    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data[idx] = rgb565 & 0xFF
            data[idx + 1] = (rgb565 >> 8) & 0xFF
            idx += 2

    with open(fb_path, 'wb') as fb:
        fb.write(bytes(data))


def show_standard_ui(display, album_art_path):
    """Show the standard LEELOO UI"""
    weather_data = {
        'temp_f': 72,
        'uv_raw': 5,
        'rain_24h_inches': 0,
    }

    now = datetime.now()
    time_data = {
        'time_str': now.strftime('%-I:%M %p'),
        'date_str': now.strftime('%b %-d'),
        'seconds': now.second,
    }

    contacts = ['Amy', 'Ben']

    album_data = {
        'artist': 'Cinnamon Chasers',
        'track': 'Doorways',
        'bpm': 120,
        'listeners': '262K',
        'pushed_by': 'Amy',
    }

    display.render(weather_data, time_data, contacts, album_data, album_art_path=album_art_path)
    write_to_framebuffer(display.image)


def draw_expanded_weather_content(display, geometry, region_x, region_y, image):
    """Draw expanded weather content within the given geometry"""
    draw = ImageDraw.Draw(image)

    # Offset for region coordinates
    x = geometry.x - region_x + 8
    y = geometry.y - region_y + 15

    color = COLORS['tan']

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18)
    except:
        font = ImageFont.load_default()
        font_large = font

    # Large temperature
    draw.text((x, y), "72", font=font_large, fill=color)
    draw.text((x + 35, y + 2), "F", font=font, fill=color)

    # Detailed forecast
    y += 30
    draw.text((x, y), "feels like 75F", font=font, fill=color)
    y += 16
    draw.text((x, y), "humidity 45%", font=font, fill=color)
    y += 16
    draw.text((x, y), "wind 8 mph SW", font=font, fill=color)
    y += 24

    # Today's forecast
    draw.text((x, y), "TODAY", font=font, fill=color)
    y += 16
    draw.text((x, y), "high 78 / low 62", font=font, fill=color)
    y += 16
    draw.text((x, y), "partly cloudy", font=font, fill=color)
    y += 24

    # Tomorrow
    draw.text((x, y), "TOMORROW", font=font, fill=color)
    y += 16
    draw.text((x, y), "high 75 / low 58", font=font, fill=color)
    y += 16
    draw.text((x, y), "sunny", font=font, fill=color)


def draw_expanded_messages_content(display, geometry, region_x, region_y, image):
    """Draw expanded messages content"""
    draw = ImageDraw.Draw(image)

    x = geometry.x - region_x + 8
    y = geometry.y - region_y + 15

    color = COLORS['lavender']
    white = COLORS['white']

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
    except:
        font = ImageFont.load_default()
        font_bold = font

    # Message thread
    draw.text((x, y), "Amy", font=font_bold, fill=color)
    y += 14
    draw.text((x, y), "Check out this song!", font=font, fill=white)
    y += 20

    draw.text((x, y), "Ben", font=font_bold, fill=color)
    y += 14
    draw.text((x, y), "Great pick! Love the", font=font, fill=white)
    y += 12
    draw.text((x, y), "vibe", font=font, fill=white)
    y += 20

    draw.text((x, y), "Amy", font=font_bold, fill=color)
    y += 14
    draw.text((x, y), "Right? Makes me want", font=font, fill=white)
    y += 12
    draw.text((x, y), "to go on a road trip", font=font, fill=white)
    y += 20

    draw.text((x, y), "Ben", font=font_bold, fill=color)
    y += 14
    draw.text((x, y), "Let's plan one for", font=font, fill=white)
    y += 12
    draw.text((x, y), "next month!", font=font, fill=white)


def draw_expanded_album_content(display, geometry, region_x, region_y, image):
    """Draw expanded album content"""
    draw = ImageDraw.Draw(image)

    x = geometry.x - region_x + 8
    y = geometry.y - region_y + 15

    color = COLORS['green']
    rose = COLORS['rose']
    white = COLORS['white']

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except:
        font = ImageFont.load_default()
        font_large = font

    # Artist and track
    draw.text((x, y), "Cinnamon Chasers", font=font_large, fill=color)
    y += 20
    draw.text((x, y), '"Doorways"', font=font_large, fill=color)
    y += 28

    # Album info
    draw.text((x, y), "Album: Luv Deluxe", font=font, fill=color)
    y += 14
    draw.text((x, y), "Year: 2010", font=font, fill=color)
    y += 14
    draw.text((x, y), "Genre: Nu-Disco", font=font, fill=color)
    y += 20

    # Stats
    draw.text((x, y), "120 BPM", font=font, fill=color)
    y += 14
    draw.text((x, y), "262K monthly listeners", font=font, fill=color)
    y += 20

    # Pushed by
    draw.text((x, y), "pushed by Amy", font=font, fill=rose)
    y += 20

    # Similar artists
    draw.text((x, y), "Similar artists:", font=font, fill=color)
    y += 14
    draw.text((x, y), "Breakbot, Kavinsky,", font=font, fill=white)
    y += 12
    draw.text((x, y), "Miami Horror", font=font, fill=white)


def demo_frame_expansion():
    """Demo frame expansion animations"""
    print("Frame Expansion Animation Demo")
    print("=" * 40)

    display = LeelooDisplay(preview_mode=False)

    # Find album art
    album_art_path = '/home/pi/doorways-album.jpg'
    if not os.path.exists(album_art_path):
        album_art_path = None

    # Show standard UI first
    print("\n[1] Showing standard UI...")
    show_standard_ui(display, album_art_path)
    time.sleep(2)

    # Create animator
    animator = FrameAnimator(display, fb_path='/dev/fb1')

    # Weather expansion
    print("\n[2] Expanding WEATHER panel...")

    def weather_content_drawer(region_image, geometry, region_x, region_y):
        draw_expanded_weather_content(display, geometry, region_x, region_y, region_image)

    animator.expand(FrameType.WEATHER, content_drawer=weather_content_drawer)
    print("    Weather expanded - showing detailed forecast")
    time.sleep(3)

    print("    Collapsing weather...")
    animator.collapse(FrameType.WEATHER)
    time.sleep(1)

    # Restore standard UI
    show_standard_ui(display, album_art_path)
    time.sleep(1)

    # Messages expansion
    print("\n[3] Expanding MESSAGES panel...")

    def messages_content_drawer(region_image, geometry, region_x, region_y):
        draw_expanded_messages_content(display, geometry, region_x, region_y, region_image)

    animator.expand(FrameType.MESSAGES, content_drawer=messages_content_drawer)
    print("    Messages expanded - showing thread")
    time.sleep(3)

    print("    Collapsing messages...")
    animator.collapse(FrameType.MESSAGES)
    time.sleep(1)

    # Restore standard UI
    show_standard_ui(display, album_art_path)
    time.sleep(1)

    # Album expansion
    print("\n[4] Expanding ALBUM panel...")

    def album_content_drawer(region_image, geometry, region_x, region_y):
        draw_expanded_album_content(display, geometry, region_x, region_y, region_image)

    animator.expand(FrameType.ALBUM, content_drawer=album_content_drawer)
    print("    Album expanded - showing details")
    time.sleep(3)

    print("    Collapsing album...")
    animator.collapse(FrameType.ALBUM)
    time.sleep(1)

    # Final standard UI
    print("\n[5] Returning to standard UI...")
    show_standard_ui(display, album_art_path)

    print("\nDemo complete!")


if __name__ == "__main__":
    demo_frame_expansion()
