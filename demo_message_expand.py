#!/usr/bin/env python3
"""
Demo: Expanded Message View + Reaction GIF
Shows:
1. Incoming message view (what recipient sees)
2. Reaction view with Fire GIF animation (what sender sees after recipient reacts)
3. After 6 seconds, returns to standard UI
"""

import sys
import time
sys.path.insert(0, '/home/pi')

from gadget_display import LEELOODisplay
from PIL import Image, ImageDraw
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


def frame_to_rgb565(frame):
    """Convert PIL frame to RGB565 bytes"""
    img = frame.convert('RGB')
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
    return bytes(data)


def write_region_to_framebuffer(frame_bytes, x, y, width, height, fb_path='/dev/fb1', screen_width=480):
    """Write pre-computed RGB565 bytes to a region of framebuffer"""
    with open(fb_path, 'r+b') as fb:
        for row in range(height):
            offset = ((y + row) * screen_width + x) * 2
            fb.seek(offset)
            row_start = row * width * 2
            row_end = row_start + width * 2
            fb.write(frame_bytes[row_start:row_end])


def show_standard_ui(display, album_art_path):
    """Show the standard LEELOO UI"""
    # Weather data (mock)
    weather_data = {
        'temp_f': 72,
        'uv_raw': 5,
        'rain_24h_inches': 0,
    }

    # Time data
    now = datetime.now()
    time_data = {
        'time_str': now.strftime('%-I:%M %p'),
        'date_str': now.strftime('%b %-d'),
        'seconds': now.second,
    }

    # Contacts
    contacts = ['Amy', 'Ben']

    # Album data
    album_data = {
        'artist': 'Cinnamon Chasers',
        'track': 'Doorways',
        'bpm': 120,
        'listeners': '262K',
        'pushed_by': 'Amy',
    }

    display.render(weather_data, time_data, contacts, album_data, album_art_path=album_art_path)
    write_to_framebuffer(display.image)


def play_reaction_gif(display, gif_path, sender_name, album_art_path, duration=6.0):
    """Play a reaction GIF animation"""
    # Set up the display to get actual box dimensions
    display.image = Image.new('RGB', (display.width, display.height), color='#1A1D2E')
    display.draw = ImageDraw.Draw(display.image)
    album_x = display.draw_album_art(album_art_path=album_art_path)
    info_panel_right = album_x - 5
    box_right = info_panel_right - 5

    # Draw the frame/header
    display.draw.rectangle([2, 4, info_panel_right, 318], fill='#1A1D2E', outline='#A7AFD4', width=1)
    display.draw.rectangle([6, -1, info_panel_right - 6, 11], fill='#1A1D2E')
    display.draw.text((8, 0), f"reaction from {sender_name}", font=display.font_header, fill='#A7AFD4')
    display.draw.rectangle([5, 16, box_right, 306], outline='#A7AFD4', width=2)

    # Calculate ACTUAL inner box dimensions (inside the 2px border)
    gif_area_width = box_right - 7 - 2
    gif_area_height = 304 - 18

    gif_x = 7
    gif_y = 18

    # Load the GIF
    gif = Image.open(gif_path)
    n_frames = getattr(gif, 'n_frames', 1)
    print(f"    GIF: {gif.size[0]}x{gif.size[1]}, {n_frames} frames")

    # Pre-process all GIF frames
    print("    Pre-processing frames...")
    processed_frames = []

    orig_w, orig_h = gif.size
    scale_w = gif_area_width / orig_w
    scale_h = gif_area_height / orig_h
    scale = max(scale_w, scale_h)

    new_width = int(orig_w * scale)
    new_height = int(orig_h * scale)

    bg_color = (26, 29, 46)  # #1A1D2E

    for i in range(n_frames):
        gif.seek(i)
        frame = gif.copy()
        frame = frame.resize((new_width, new_height), Image.Resampling.LANCZOS)

        if frame.mode == 'P':
            frame = frame.convert('RGBA')
        if frame.mode == 'RGBA':
            bg = Image.new('RGB', frame.size, bg_color)
            bg.paste(frame, mask=frame.split()[3])
            frame = bg
        elif frame.mode != 'RGB':
            frame = frame.convert('RGB')

        left = (new_width - gif_area_width) // 2
        top = (new_height - gif_area_height) // 2
        frame = frame.crop((left, top, left + gif_area_width, top + gif_area_height))
        processed_frames.append(frame_to_rgb565(frame))

    print(f"    Processed {len(processed_frames)} frames at {gif_area_width}x{gif_area_height}")

    # Play animation
    start_time = time.time()
    frame_num = 0
    target_fps = 20
    frame_time = 1.0 / target_fps

    write_to_framebuffer(display.image)

    print("    Playing animation...")
    while time.time() - start_time < duration:
        current_frame_bytes = processed_frames[frame_num % n_frames]
        write_region_to_framebuffer(current_frame_bytes, gif_x, gif_y, gif_area_width, gif_area_height)

        elapsed = time.time() - start_time
        next_frame_time = (frame_num + 1) * frame_time
        sleep_time = next_frame_time - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

        frame_num += 1

    print(f"    Played {frame_num} frames in {duration} seconds ({frame_num/duration:.1f} fps)")


def demo_message_then_reaction():
    """Demo the full flow: message -> reaction -> back to standard UI"""
    print("Starting message + reaction demo...")

    display = LEELOODisplay(preview_mode=False)

    # Find album art
    album_art_path = '/home/pi/doorways-album.jpg'
    if not os.path.exists(album_art_path):
        album_art_path = None

    # Step 1: Show incoming message (what recipient sees)
    print("\n[1] Showing incoming message from Amy...")
    message = "Hey! Check out this song, it's so good. Made me think of our road trip last summer!"
    display.render_message_view("Amy", message, album_art_path=album_art_path)
    write_to_framebuffer(display.image)
    print("    (User has 30 seconds to react with double/triple tap)")
    time.sleep(4)

    # Step 2: Show FIRE reaction (triple tap)
    print("\n[2] Ben triple-tapped! Showing FIRE reaction (6 seconds)...")
    fire_gif = '/home/pi/assets/Fire.gif'
    if os.path.exists(fire_gif):
        play_reaction_gif(display, fire_gif, "Ben", album_art_path, duration=6.0)
    else:
        print(f"    Fire GIF not found at {fire_gif}")

    # Step 3: Show another message
    print("\n[3] Showing another incoming message...")
    message2 = "OMG yes! Remember when we got lost trying to find that taco place?"
    display.render_message_view("Ben", message2, album_art_path=album_art_path)
    write_to_framebuffer(display.image)
    time.sleep(4)

    # Step 4: Show LOVE reaction (double tap)
    print("\n[4] Amy double-tapped! Showing LOVE reaction (6 seconds)...")
    heart_gif = '/home/pi/assets/Heart.gif'
    if os.path.exists(heart_gif):
        play_reaction_gif(display, heart_gif, "Amy", album_art_path, duration=6.0)
    else:
        print(f"    Heart GIF not found at {heart_gif}")

    # Step 5: Return to standard UI
    print("\n[5] Closing reaction, returning to standard UI...")
    show_standard_ui(display, album_art_path)

    print("\nDemo complete! Standard UI is now showing.")
    time.sleep(5)


if __name__ == "__main__":
    demo_message_then_reaction()
