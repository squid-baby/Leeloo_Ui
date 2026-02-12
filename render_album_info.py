#!/usr/bin/env python3
"""
Render Album Info Box for LEELOO Display
Clean, centered layout with text truncation for long names
"""

from PIL import Image, ImageDraw, ImageFont
from text_scroller import truncate_text, center_text_in_box


def render_album_info_box(album_data, width=210, height=95, fonts=None, colors=None):
    """
    Render a clean album info box with band, track, and listener count.

    Args:
        album_data: dict with 'artist', 'track', 'listeners', 'pushed_by'
        width: Box width (default 210px to fit in left panel)
        height: Box height
        fonts: dict with 'large' and 'tiny' fonts
        colors: dict with 'green', 'rose', 'bg' colors

    Returns:
        PIL Image of the rendered box
    """
    # Default colors
    if colors is None:
        colors = {
            'bg': '#1A1D2E',
            'green': '#719253',
            'rose': '#D6697F',
            'white': '#FFFFFF'
        }

    # Default fonts
    if fonts is None:
        try:
            fonts = {
                'large': ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14),
                'tiny': ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            }
        except:
            fonts = {
                'large': ImageFont.load_default(),
                'tiny': ImageFont.load_default()
            }

    # Create image
    img = Image.new('RGB', (width, height), color=colors['bg'])
    draw = ImageDraw.Draw(img)

    y_pos = 8  # Starting y position

    if album_data is None:
        # Empty state
        empty_text = "awaiting music"
        x = center_text_in_box(empty_text, fonts['tiny'], width)
        draw.text((x, height // 2 - 6), empty_text, font=fonts['tiny'], fill=colors['green'])
        return img

    # Artist name (band) - centered, truncated if needed
    artist = album_data.get('artist', '')
    if artist:
        artist_truncated = truncate_text(artist, fonts['large'], width - 10)
        x = center_text_in_box(artist_truncated, fonts['large'], width)
        draw.text((x, y_pos), artist_truncated, font=fonts['large'], fill=colors['green'])
        y_pos += 16

    # Track name - centered, in quotes, truncated if needed
    track = album_data.get('track', '')
    if track:
        track_display = f'"{track}"'
        track_truncated = truncate_text(track_display, fonts['large'], width - 10)
        x = center_text_in_box(track_truncated, fonts['large'], width)
        draw.text((x, y_pos), track_truncated, font=fonts['large'], fill=colors['green'])
        y_pos += 16

    # Add some spacing
    y_pos += 4

    # Monthly listeners (if available) - centered
    listeners = album_data.get('listeners')
    if listeners:
        listeners_text = f"{listeners} monthly listeners"
        listeners_truncated = truncate_text(listeners_text, fonts['tiny'], width - 10)
        x = center_text_in_box(listeners_truncated, fonts['tiny'], width)
        draw.text((x, y_pos), listeners_truncated, font=fonts['tiny'], fill=colors['green'])
        y_pos += 14

    # "pushed by" text - centered
    pushed_by = album_data.get('pushed_by')
    if pushed_by:
        pushed_text = f"pushed by {pushed_by}"
        pushed_truncated = truncate_text(pushed_text, fonts['tiny'], width - 10)
        x = center_text_in_box(pushed_truncated, fonts['tiny'], width)
        draw.text((x, y_pos), pushed_truncated, font=fonts['tiny'], fill=colors['rose'])

    return img


if __name__ == '__main__':
    # Test with sample data
    test_data = {
        'artist': 'The Beatles',
        'track': 'Hey Jude',
        'listeners': '○○○',
        'pushed_by': 'Amy'
    }

    img = render_album_info_box(test_data)
    img.save('album_info_test.png')
    print("✅ Test image saved to album_info_test.png")

    # Test with long names
    long_data = {
        'artist': 'Florence + The Machine',
        'track': 'Shake It Out (MTV Unplugged Version)',
        'listeners': '○○○○○',
        'pushed_by': 'Christopher'
    }

    img2 = render_album_info_box(long_data)
    img2.save('album_info_long_test.png')
    print("✅ Long text test saved to album_info_long_test.png")
