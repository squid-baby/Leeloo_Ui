#!/usr/bin/env python3
"""
Text Scrolling Utilities for LEELOO Display
Handles text that's too long to fit in a fixed-width box
"""

from PIL import Image, ImageDraw, ImageFont
import time


def get_text_width(text, font):
    """Get the width of text in pixels"""
    try:
        return font.getlength(text)
    except:
        # Fallback for older PIL versions
        return len(text) * 7


def render_scrolling_text(text, font, max_width, color='#FFFFFF', bg_color='#1A1D2E', scroll_speed=2):
    """
    Render text that scrolls if it's too wide.

    Args:
        text: Text to render
        font: PIL ImageFont
        max_width: Maximum width in pixels
        color: Text color
        bg_color: Background color
        scroll_speed: Pixels to scroll per frame (for animation)

    Returns:
        PIL Image of the text (scrollable if needed)
    """
    text_width = get_text_width(text, font)

    if text_width <= max_width:
        # Text fits - just render it centered
        img = Image.new('RGB', (max_width, 20), color=bg_color)
        draw = ImageDraw.Draw(img)
        x = (max_width - text_width) // 2
        draw.text((x, 0), text, font=font, fill=color)
        return img
    else:
        # Text is too long - create scrolling image
        # Add padding on both sides for smooth scrolling loop
        padding = max_width
        total_width = int(text_width + padding * 2)

        img = Image.new('RGB', (total_width, 20), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Draw text twice for seamless loop
        draw.text((padding, 0), text, font=font, fill=color)
        draw.text((padding + text_width + padding, 0), text, font=font, fill=color)

        return img


def truncate_text(text, font, max_width, ellipsis='...'):
    """
    Truncate text with ellipsis if too long.

    Args:
        text: Text to truncate
        font: PIL ImageFont
        max_width: Maximum width in pixels
        ellipsis: String to append when truncated

    Returns:
        Truncated text string
    """
    text_width = get_text_width(text, font)

    if text_width <= max_width:
        return text

    # Binary search for best fit
    ellipsis_width = get_text_width(ellipsis, font)
    available_width = max_width - ellipsis_width

    # Start with rough estimate
    chars_per_px = len(text) / text_width
    estimated_chars = int(available_width * chars_per_px)

    # Fine-tune
    for i in range(estimated_chars, 0, -1):
        test_text = text[:i] + ellipsis
        if get_text_width(test_text, font) <= max_width:
            return test_text

    return ellipsis


def center_text_in_box(text, font, box_width):
    """
    Calculate x position to center text in a box.

    Args:
        text: Text to center
        font: PIL ImageFont
        box_width: Width of the box

    Returns:
        x position for drawing text
    """
    text_width = get_text_width(text, font)
    return (box_width - text_width) // 2


if __name__ == '__main__':
    # Test the functions
    from PIL import ImageFont

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except:
        font = ImageFont.load_default()

    # Test short text
    short = "Song Title"
    print(f"Short text '{short}': {get_text_width(short, font)}px")

    # Test long text
    long = "This Is A Very Long Song Title That Won't Fit"
    print(f"Long text '{long}': {get_text_width(long, font)}px")
    print(f"Truncated: '{truncate_text(long, font, 150)}'")

    # Test centering
    print(f"Center position for '{short}' in 200px box: {center_text_in_box(short, font, 200)}px")
