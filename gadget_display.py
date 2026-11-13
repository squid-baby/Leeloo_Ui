#!/usr/bin/env python3
"""
LEELOO Display Renderer
Renders the tiptop-style terminal UI for the LEELOO music sharing device
"""

from PIL import Image, ImageDraw, ImageFont
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime

from text_scroller import truncate_text, center_text_in_box

# Import hang data module (with fallback if not available)
try:
    from gadget_data import format_countdown_display
except ImportError:
    def format_countdown_display():
        return {'date_str': '_/__', 'time_str': '__:__', 'countdown_str': '__:__', 'slider_boxes': 0}

def get_moon_phase():
    """Calculate moon phase (0-6 scale for slider)
    0 = new moon, 6 = full moon
    """
    known_new_moon = datetime(2025, 1, 29)  # Known new moon date
    days_since = (datetime.now() - known_new_moon).days
    phase_position = (days_since % 29.53) / 29.53  # 0-1 cycle
    # 0->0.5 = waxing (0->6 boxes), 0.5->1 = waning (6->0 boxes)
    if phase_position <= 0.5:
        return int(phase_position * 12)  # 0->6
    else:
        return int((1 - phase_position) * 12)  # 6->0


def _leeloo_home():
    """Install directory on the device (override with LEELOO_HOME)."""
    return os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui")


# Prepared album art / placeholder images kept per display (decoded + resized)
_ART_CACHE_MAX = 4

# Color palette - matches React UI exactly
COLORS = {
    'bg': '#1A1D2E',            # leeloo-navy (dark background)
    'green': '#7beec0',          # leeloo-green (album box) - updated to mint/teal
    'purple': '#9C93DD',         # leeloo-purple (time box)
    'rose': '#D6697F',           # leeloo-rose (pushed by text)
    'tan': '#C2995E',            # leeloo-tan (weather box)
    'lavender': '#d978f9',       # leeloo-lavender (messages box) - updated to purple-pink
    'white': '#FFFFFF',
    'orange': '#FF8800',         # Spotify code / UV high
    'yellow': '#D4A84B',         # UV moderate (muted yellow)
}

# The built-in palette, never changed. COLORS is the palette in use: a pack
# theme is applied by updating COLORS IN PLACE (other modules hold the same
# dict object through `from gadget_display import COLORS`).
DEFAULT_COLORS = dict(COLORS)

# Guards COLORS: a theme never changes in the middle of a render().
_theme_lock = threading.RLock()


def _valid_hex_color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != '#':
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def _hex_to_rgb(hex_color):
    h = hex_color.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _plain_text(value):
    """Pack text for one row: a string without control characters."""
    if value is None:
        return ''
    text = str(value)
    if not text.isprintable():
        text = ''.join(ch if ch.isprintable() else ' ' for ch in text)
    return text


class LeelooDisplay:
    def __init__(self, width=480, height=320, preview_mode=True):
        """
        Initialize the display
        
        Args:
            width: Display width in pixels (default 480)
            height: Display height in pixels (default 320)
            preview_mode: If True, renders to image file. If False, renders to hardware display
        """
        self.width = width
        self.height = height
        self.preview_mode = preview_mode
        
        # Load fonts - increased sizes (except header and band/song)
        try:
            self.font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)  # For LEELOO/tap to talk
            self.font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)   # main text
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 13)
            self.font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
            self.font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14) # Keep same for band/song
            self.font_slider = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16) # Larger slider boxes
            self.font_symbol = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13) # For circled numbers
        except OSError:
            print("⚠️  Could not load fonts, using default")
            self.font_header = ImageFont.load_default()
            self.font_tiny = ImageFont.load_default()
            self.font_small = ImageFont.load_default()
            self.font_med = ImageFont.load_default()
            self.font_large = ImageFont.load_default()
            self.font_symbol = ImageFont.load_default()
            self.font_slider = ImageFont.load_default()
        
        # Create base image
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)
        
        # Hardware display setup (only if not in preview mode)
        self.display_device = None
        if not preview_mode:
            try:
                # Import will happen when actually deploying to Pi
                # from waveshare_epd import epd3in5
                # self.display_device = epd3in5.EPD()
                pass
            except ImportError:
                print("⚠️  Waveshare library not found - running in preview mode")
                self.preview_mode = True

        # Overlay state for reaction animations
        self.overlay_active = False

        # Prepared-art cache: key -> dict(sig, frame_x, frame_width, img, pos)
        # Guarded by a lock: render() runs in a worker thread while
        # album_frame_x() may be called from the event loop thread.
        self._art_cache = OrderedDict()
        self._art_lock = threading.RLock()
        self._dim_cache = {}
        self._theme_bg = COLORS['bg']
        self._slider_char_w = None
        self._art_warned = None

    def _slider_char_width(self):
        """Width of one slider box glyph (measured once)."""
        w = self._slider_char_w
        if w is None:
            try:
                char_bbox = self.font_slider.getbbox("■")
                w = char_bbox[2] - char_bbox[0]
            except Exception:
                w = 10
            self._slider_char_w = w
        return w

    def _dim_color(self, hex_color):
        """
        Return a dimmed version of the color (30% opacity effect)

        Args:
            hex_color: Hex color string like '#719253'
        Returns:
            Dimmed hex color string
        """
        # The result depends on the background of the active theme
        bg = COLORS['bg']
        key = (hex_color, bg)
        cached = self._dim_cache.get(key)
        if cached is not None:
            return cached

        # Parse hex color
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        # Dim by mixing with background (30% original, 70% background)
        bg_r, bg_g, bg_b = _hex_to_rgb(bg)  # leeloo-navy unless a pack theme is active
        dim_r = int(r * 0.3 + bg_r * 0.7)
        dim_g = int(g * 0.3 + bg_g * 0.7)
        dim_b = int(b * 0.3 + bg_b * 0.7)

        result = f'#{dim_r:02x}{dim_g:02x}{dim_b:02x}'
        if len(self._dim_cache) > 256:
            self._dim_cache.clear()
        self._dim_cache[key] = result
        return result

    def apply_theme(self, colors=None):
        """
        Use pack colours on top of the DEFAULT palette.

        Args:
            colors: dict colour name -> '#RRGGBB' (names as in COLORS).
                    None or {} restores the default palette. Unknown names
                    and bad values are ignored.
        Returns:
            True if the palette changed.

        COLORS is updated in place (same dict object). Calling this again
        with the same colours only compares two small dicts. Call it from
        the thread that renders (it waits for a render() in progress).
        """
        palette = dict(DEFAULT_COLORS)
        if isinstance(colors, dict):
            for name, value in colors.items():
                if name in DEFAULT_COLORS and _valid_hex_color(value):
                    palette[name] = value
        with _theme_lock:
            changed = palette != COLORS
            if changed:
                COLORS.update(palette)
            # Caches that hold palette colours (they are also keyed by the
            # background, so a change made through another display is safe)
            if changed or self._theme_bg != palette['bg']:
                self._theme_bg = palette['bg']
                self._dim_cache.clear()
                with self._art_lock:
                    for key in [k for k in self._art_cache if k[0] == 'gradient']:
                        del self._art_cache[key]
        return changed

    def draw_slider(self, x, y, value, max_val, color, num_dots=20, draw=None):
        """
        Draw a tiptop-style dot slider using ■ and □ characters

        Args:
            x, y: Position
            value: Current value
            max_val: Maximum value
            color: Slider color (for filled dots)
            num_dots: Number of dots in slider (default 20)
        """
        draw = draw if draw is not None else self.draw
        filled = int((value / max_val) * num_dots) if max_val > 0 else 0

        # Build slider string with ■ for filled and □ for empty
        slider_str = ""
        for i in range(num_dots):
            if i < filled:
                slider_str += "■"
            else:
                slider_str += "□"

        # Draw filled portion in color
        filled_str = "■" * filled
        empty_str = "□" * (num_dots - filled)

        draw.text((x, y), filled_str, font=self.font_tiny, fill=color)
        # Calculate width of filled portion for positioning empty part
        if filled > 0:
            try:
                filled_bbox = self.font_tiny.getbbox(filled_str)
                filled_width = filled_bbox[2] - filled_bbox[0]
            except:
                filled_width = filled * 6
            draw.text((x + filled_width, y), empty_str, font=self.font_tiny, fill=self._dim_color(color))

    def draw_weather_slider(self, right_edge, y, filled, color, num_boxes=12, padding=5, deplete_left=False, draw=None):
        """
        Draw a box slider with filled ■ and empty □, right-justified

        Args:
            right_edge: Right edge of the containing box
            y: Y position
            filled: Number of filled boxes (0-num_boxes)
            color: Color for filled boxes
            num_boxes: Total number of boxes (default 12)
            padding: Padding from right edge (default 5)
            deplete_left: If True, empty boxes appear on left (countdown style)
        """
        draw = draw if draw is not None else self.draw
        filled = min(num_boxes, max(0, int(filled)))

        # Get width of one box character (using larger slider font)
        char_width = self._slider_char_width()

        # Calculate starting x position (right-justified)
        total_width = num_boxes * char_width
        box_x = right_edge - padding - total_width

        # Draw each box individually
        for i in range(num_boxes):
            if deplete_left:
                # Countdown style: filled boxes on RIGHT, empty on LEFT
                # As time passes, boxes empty from left to right
                is_filled = i >= (num_boxes - filled)
            else:
                # Normal style: filled boxes on LEFT
                is_filled = i < filled

            if is_filled:
                char = "■"
                box_color = color
            else:
                char = "□"
                box_color = self._dim_color(color)

            draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_left_slider(self, left_edge, y, filled, color, num_boxes=6, padding=0, draw=None):
        """
        Draw a left-justified box slider

        Args:
            left_edge: Left edge position
            y: Y position
            filled: Number of filled boxes (0-num_boxes)
            color: Color for filled boxes
            num_boxes: Total number of boxes (default 6)
            padding: Padding from left edge (default 0)
        """
        draw = draw if draw is not None else self.draw
        filled = min(num_boxes, max(0, int(filled)))

        # Get width of one box character (using larger slider font)
        char_width = self._slider_char_width()

        box_x = left_edge + padding
        for i in range(num_boxes):
            if i < filled:
                char = "■"
                box_color = color
            else:
                char = "□"
                box_color = self._dim_color(color)

            draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_uv_slider(self, right_edge, y, uv_value, padding=5, draw=None):
        """
        Draw a 12-box UV slider with color-coded levels, right-justified

        UV Index colors:
        - 1-2: Green (Low)
        - 3-5: Yellow (Moderate)
        - 6-7: Orange (High)
        - 8-10: Rose/Red (Very High)
        - 11-12: Purple (Extreme)
        """
        draw = draw if draw is not None else self.draw
        UV_COLORS = [
            COLORS['green'],   # 1
            COLORS['green'],   # 2
            COLORS['yellow'],  # 3
            COLORS['yellow'],  # 4
            COLORS['yellow'],  # 5
            COLORS['orange'],  # 6
            COLORS['orange'],  # 7
            COLORS['rose'],    # 8
            COLORS['rose'],    # 9
            COLORS['rose'],    # 10
            COLORS['purple'],  # 11
            COLORS['purple'],  # 12
        ]

        filled = min(12, max(0, int(uv_value)))

        # Get width of one box character (using larger slider font)
        char_width = self._slider_char_width()

        # Calculate starting x position (right-justified)
        total_width = 12 * char_width
        box_x = right_edge - padding - total_width

        # Draw each box with appropriate color
        for i in range(12):
            if i < filled:
                char = "■"
                box_color = UV_COLORS[i]
            else:
                char = "□"
                # Dim based on what color this box WOULD be
                box_color = self._dim_color(UV_COLORS[i])

            draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_left_column(self, weather_data, time_data, contacts, album_data, right_edge=158, unread_counts=None,
                         status_text=None, hang=None, draw=None, header=None, labels=None, custom_panel=None):
        """
        Draw the left column with all info panels - MATCHES REACT UI EXACTLY

        Args:
            weather_data: dict with 'temp_f', 'sun', 'rain' (0-10 scale for sun/rain) - or None for empty state
            time_data: dict with 'time_str', 'date_str', 'seconds' (0-60)
            contacts: list of contact names (just strings, no previews) - or empty list
            album_data: dict with 'artist', 'track', 'bpm', 'listeners', 'pushed_by' - or None for empty state
            right_edge: right edge x position for the info panel (default 158)
            unread_counts: dict mapping contact name to unread count
            status_text: replaces "tap to talk" in the header when set
            hang: dict as returned by gadget_data.format_countdown_display()
                  (None = read it here)
            draw: ImageDraw to draw on (None = self.draw)
            header: replaces "LEELOO v1.0" when set (truncated to fit)
            labels: dict with any of weather/time/messages/album -> box label
            custom_panel: dict from PackManager.current_panel(); replaces the
                          content of the weather box (weather_data is not drawn)
        """
        draw = draw if draw is not None else self.draw
        box_right = right_edge - 5  # Inner box right edge with padding
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(custom_panel, dict):
            custom_panel = None

        def box_label(name, default):
            # Pack label, cut so that it stays on the top edge of its box
            text = _plain_text(labels.get(name)).strip()
            if name == 'weather' and custom_panel is not None:
                text = _plain_text(custom_panel.get('title')).strip() or text
            if not text:
                return default
            return truncate_text(text, self.font_tiny, max(10, box_right - 13 - 8))

        # Main container border with header
        draw.rectangle([2, 4, right_edge, 318], outline=COLORS['lavender'], width=1)

        # Header text - breaks the frame
        header_y = 4
        # Draw background behind header text to "break" the frame
        draw.rectangle([6, header_y - 5, 155, header_y + 7], fill=COLORS['bg'])
        header_text = _plain_text(header).strip()
        if header_text:
            # "tap to talk" / the status starts at x=90
            header_text = truncate_text(header_text, self.font_header, 78)
        else:
            header_text = "LEELOO v1.0"
        draw.text((8, header_y - 4), header_text, font=self.font_header, fill=COLORS['lavender'])
        if status_text:
            # Status replaces "tap to talk"; keep it inside the outer frame
            status_max = max(10, right_edge - 8 - 90)
            status = truncate_text(str(status_text), self.font_header, status_max)
            try:
                status_w = self.font_header.getlength(status)
            except Exception:
                status_w = len(status) * 6
            if 90 + status_w + 3 > 155:
                # Longer than the default header gap: widen the frame break
                draw.rectangle([150, header_y - 5, min(right_edge - 2, int(90 + status_w + 3)), header_y + 7],
                               fill=COLORS['bg'])
            draw.text((90, header_y - 4), status, font=self.font_header, fill=COLORS['lavender'])
        else:
            draw.text((90, header_y - 4), "tap to talk", font=self.font_header, fill=COLORS['lavender'])

        # ===== WEATHER BOX ===== (tan border)
        y = 16
        box_height = 59
        draw.rectangle([7, y, box_right, y + box_height], outline=COLORS['tan'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = box_label('weather', "weather")
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 13  # 3px right from frame edge
        draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['tan'])

        weather_error = bool(weather_data) and bool(weather_data.get('error'))
        if custom_panel is not None:
            self._draw_custom_panel(draw, custom_panel, y, box_right)
        elif weather_data is None or weather_error:
            # Empty state - no location configured / weather fetch failed
            empty_text = "weather offline" if weather_error else "set location"
            try:
                text_width = self.font_tiny.getlength(empty_text)
            except:
                text_width = len(empty_text) * 7
            text_x = 7 + (box_right - 7) // 2 - text_width // 2
            draw.text((text_x, y + 22), empty_text, font=self.font_tiny, fill=self._dim_color(COLORS['tan']))
        else:
            # Temperature: 0°F = 0 boxes, 100°F = 12 boxes
            temp_f = weather_data.get('temp_f', 72)
            temp_boxes = min(12, max(0, int(temp_f * 12 / 100)))
            draw.text((12, y + 8), f"{temp_f}°F", font=self.font_tiny, fill=COLORS['tan'])
            self.draw_weather_slider(box_right, y + 8, temp_boxes, COLORS['tan'], draw=draw)

            # UV index with color-coded slider (0-11+ scale)
            uv_raw = weather_data.get('uv_raw', 5)
            draw.text((12, y + 24), "ultra V", font=self.font_tiny, fill=COLORS['tan'])
            self.draw_uv_slider(box_right, y + 24, uv_raw, draw=draw)

            # Rain: 0" = 0 boxes, 3"+ = 12 boxes (24hr total)
            rain_inches = weather_data.get('rain_24h_inches', 0)
            rain_boxes = min(12, int(rain_inches * 12 / 3))
            draw.text((12, y + 40), "rain", font=self.font_tiny, fill=COLORS['tan'])
            self.draw_weather_slider(box_right, y + 40, rain_boxes, COLORS['tan'], draw=draw)

        # ===== TIME BOX ===== (purple border) - includes hang countdown
        y = 83
        box_height = 71  # Was 40, added 31px for hang rows (+3px taller)
        draw.rectangle([7, y, box_right, y + box_height], outline=COLORS['purple'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = box_label('time', "time")
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 13  # 3px right from frame edge
        draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['purple'])

        # Row 1: Time on left, date centered, "moon" on right
        draw.text((12, y + 8), time_data['time_str'], font=self.font_tiny, fill=COLORS['purple'])
        # Date centered
        date_str = time_data['date_str']
        try:
            date_width = self.font_tiny.getlength(date_str)
        except:
            date_width = len(date_str) * 7
        date_x = (box_right + 12) // 2 - date_width // 2
        draw.text((date_x, y + 8), date_str, font=self.font_tiny, fill=COLORS['purple'])
        # "moon" label on right
        draw.text((box_right - 38, y + 8), "moon", font=self.font_tiny, fill=COLORS['purple'])

        # Row 2: Dual 6-box sliders (time left, moon right)
        seconds = time_data.get('seconds', 0)
        seconds_boxes = min(6, int(seconds / 10))
        self.draw_left_slider(12, y + 22, seconds_boxes, COLORS['purple'], num_boxes=6, draw=draw)
        moon_phase = get_moon_phase()
        self.draw_weather_slider(box_right, y + 22, moon_phase, COLORS['purple'], num_boxes=6, draw=draw)

        # Row 3: Hang date/time (moved down 3px)
        if hang is None:
            hang = format_countdown_display()
        hang_str = f"nxt hang  {hang.get('date_str', '_/__')} {hang.get('time_str', '__:__')}"
        draw.text((12, y + 39), hang_str, font=self.font_tiny, fill=COLORS['purple'])

        # Row 4: Countdown text + 9-box slider (moved down 3px)
        draw.text((12, y + 53), f"t minus {hang.get('countdown_str', '__:__')}", font=self.font_tiny, fill=COLORS['purple'])
        # Slider starts full (9 boxes) and empties from left as time elapses
        slider_boxes_9 = min(9, int((hang.get('slider_boxes') or 0) * 9 / 12))
        self.draw_weather_slider(box_right, y + 53, slider_boxes_9, COLORS['purple'], num_boxes=9, deplete_left=True, draw=draw)

        # ===== MESSAGES BOX ===== (lavender border) - shifted down for hang rows
        y = 162  # Was 131, shifted +31px
        box_height = 28
        draw.rectangle([7, y, box_right, y + box_height], outline=COLORS['lavender'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = box_label('messages', "messages")
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 13  # 3px right from frame edge
        draw.rectangle([label_x - 2, y - 1, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['lavender'])

        if not contacts:
            # Empty state - no crew configured
            empty_text = "no crew yet"
            try:
                text_width = self.font_tiny.getlength(empty_text)
            except:
                text_width = len(empty_text) * 7
            text_x = 7 + (box_right - 7) // 2 - text_width // 2
            draw.text((text_x, y + 8), empty_text, font=self.font_tiny, fill=self._dim_color(COLORS['lavender']))
        else:
            # Show contact names with unread count badges in front of each name
            # Layout: "○Amy  ②Ben" - white circle for 0, circled numbers for counts
            counts = unread_counts or {}
            x_pos = 12
            for i, contact in enumerate(contacts[:4]):  # Max 4 contacts shown
                # Contacts are name strings; dict items carry the name in 'name'
                if isinstance(contact, dict):
                    contact = contact.get('name') or ''
                contact = str(contact)
                try:
                    n = int(counts.get(contact, 0) or 0)
                except (TypeError, ValueError):
                    n = 0
                n = max(0, n)
                if n == 0:
                    badge = "○"
                    badge_color = COLORS['lavender']
                elif n <= 9:
                    badge = chr(0x2460 + n - 1)  # ①–⑨
                    badge_color = COLORS['purple']
                else:
                    badge = "⑩"
                    badge_color = COLORS['purple']
                draw.text((x_pos, y + 7), badge, font=self.font_symbol, fill=badge_color)
                x_pos += 13
                draw.text((x_pos, y + 8), contact, font=self.font_tiny, fill=COLORS['white'])
                try:
                    name_width = self.font_tiny.getlength(contact)
                except:
                    name_width = len(contact) * 7
                x_pos += int(name_width) + 8

        # ===== ALBUM BOX ===== (green border) - shifted down for hang rows
        y = 198  # Was 167, shifted +31px
        box_height = 110  # Extended 2px for more content room
        draw.rectangle([7, y, box_right, y + box_height], outline=COLORS['green'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = box_label('album', "album")
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 13  # 3px right from frame edge
        draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['green'])

        # Render album info with text truncation for long names
        # Fixed 195px text box constraint - all text must fit within this
        text_box_width = 195
        # Calculate left edge of text box (centered within the album frame)
        box_width = box_right - 7  # Total available width
        text_box_left = 7 + (box_width - text_box_width) // 2

        if album_data is None:
            # Empty state - no music shared yet
            empty_text = "awaiting music"
            try:
                text_width = self.font_tiny.getlength(empty_text)
            except:
                text_width = len(empty_text) * 7
            text_x = text_box_left + (text_box_width - text_width) // 2
            text_y = y + box_height // 2 - 6
            draw.text((text_x, text_y), empty_text, font=self.font_tiny, fill=self._dim_color(COLORS['green']))
        else:
            content_y = y + 8

            # Artist name - CENTERED within text box, truncated if too long
            artist_text = album_data.get('artist', '')
            if artist_text:
                artist_truncated = truncate_text(artist_text, self.font_large, text_box_width)
                artist_x = text_box_left + center_text_in_box(artist_truncated, self.font_large, text_box_width)
                draw.text((artist_x, content_y), artist_truncated, font=self.font_large, fill=COLORS['green'])
                content_y += 16

            # Album name - CENTERED within text box, truncated if too long
            album_text = album_data.get('album', '')
            if album_text:
                album_truncated = truncate_text(album_text, self.font_tiny, text_box_width)
                album_x = text_box_left + center_text_in_box(album_truncated, self.font_tiny, text_box_width)
                draw.text((album_x, content_y), album_truncated, font=self.font_tiny, fill=COLORS['green'])
                content_y += 14

            # Track name - CENTERED within text box, in quotes, truncated if too long
            track_text = album_data.get('track', '')
            if track_text:
                track_display = f'"{track_text}"'
                track_truncated = truncate_text(track_display, self.font_large, text_box_width)
                track_x = text_box_left + center_text_in_box(track_truncated, self.font_large, text_box_width)
                draw.text((track_x, content_y), track_truncated, font=self.font_large, fill=COLORS['green'])
                content_y += 16

            # Monthly listeners - LEFT-ALIGNED, 4px from album frame edge, in yellow
            listeners = album_data.get('listeners')
            if listeners:
                listeners_text = f"{listeners} monthly listeners"
                listeners_truncated = truncate_text(listeners_text, self.font_tiny, text_box_width)
                listeners_x = 7 + 4  # 4px buffer from album frame left edge
                draw.text((listeners_x, content_y), listeners_truncated, font=self.font_tiny, fill=COLORS['yellow'])
                content_y += 14

            # "pushed by" - LEFT-ALIGNED, 4px from album frame edge
            pushed_by = album_data.get('pushed_by')
            if pushed_by:
                pushed_text = f"pushed by {pushed_by}"
                pushed_truncated = truncate_text(pushed_text, self.font_tiny, text_box_width)
                pushed_x = 7 + 4  # 4px buffer from album frame left edge
                draw.text((pushed_x, content_y), pushed_truncated, font=self.font_tiny, fill=COLORS['rose'])
                content_y += 14

            # "ask for more info" - LEFT-ALIGNED, 4px from album frame edge, in lavender (messages purple)
            ask_text = "ask for more info"
            ask_truncated = truncate_text(ask_text, self.font_tiny, text_box_width)
            ask_x = 7 + 4  # 4px buffer from album frame left edge
            draw.text((ask_x, content_y), ask_truncated, font=self.font_tiny, fill=COLORS['lavender'])

    def _draw_custom_panel(self, draw, panel, y, box_right):
        """
        Pack panel inside the weather box: up to 3 rows at the y positions of
        the temperature / UV / rain rows. Each row has `left` text and either
        `right` text (right-aligned) or a 12-box slider filled to `bar` (0..1).
        All text is cut to fit: nothing is drawn outside the box.
        """
        color = COLORS['tan']
        font = self.font_tiny
        left_x = 12
        right_x = box_right - 5          # same right padding as the sliders
        inner = right_x - left_x
        gap = 6
        rows = panel.get('rows')
        if not isinstance(rows, (list, tuple)) or inner <= 0:
            return

        def width(text):
            try:
                return font.getlength(text)
            except Exception:
                return len(text) * 7

        for i, row in enumerate(rows[:3]):
            if not isinstance(row, dict):
                continue
            row_y = y + 8 + i * 16
            left = _plain_text(row.get('left'))
            left_max = inner

            bar = row.get('bar')
            right = row.get('right')
            if bar is not None:
                try:
                    fraction = float(bar)
                except (TypeError, ValueError):
                    fraction = 0.0
                if not 0.0 <= fraction <= 1.0:      # also catches NaN
                    fraction = 1.0 if fraction > 1.0 else 0.0
                self.draw_weather_slider(box_right, row_y, int(fraction * 12 + 1e-6), color, draw=draw)
                left_max = inner - 12 * self._slider_char_width() - gap
            elif right is not None and _plain_text(right) != '':
                right = _plain_text(right)
                # The right text gets what the left text leaves, at least half
                right_max = max(inner - width(left) - gap, inner // 2)
                right = truncate_text(right, font, right_max)
                right_w = width(right)
                if right_w <= inner:
                    draw.text((right_x - int(right_w + 0.999), row_y), right, font=font, fill=color)
                    left_max = inner - right_w - gap

            if left and left_max > 0:
                left = truncate_text(left, font, left_max)
                if width(left) <= left_max:
                    draw.text((left_x, row_y), left, font=font, fill=color)

    # ------------------------------------------------------------------
    # Album art (right side) - prepared once per file, then just pasted
    # ------------------------------------------------------------------

    _FRAME_PADDING = 5   # same padding the info boxes have to the main frame
    _FRAME_Y = 4         # same as main container top

    @staticmethod
    def _file_sig(path):
        """(mtime_ns, size) of a file, or None if it cannot be stat'ed."""
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            return None
        return (st.st_mtime_ns, st.st_size)

    def _art_cache_get(self, key, sig):
        entry = self._art_cache.get(key)
        if entry is not None and entry['sig'] == sig:
            self._art_cache.move_to_end(key)
            return entry
        return None

    def _art_cache_put(self, key, entry):
        self._art_cache[key] = entry
        self._art_cache.move_to_end(key)
        while len(self._art_cache) > _ART_CACHE_MAX:
            self._art_cache.popitem(last=False)

    def _prepare_art(self, album_art_path):
        """Decoded + resized album art with its frame geometry (cached by
        path, mtime and size). Returns None if the file is unusable.
        Caller must hold self._art_lock."""
        key = ('art', album_art_path)
        sig = self._file_sig(album_art_path)
        if sig is None:
            self._art_cache.pop(key, None)
            if self._art_warned != album_art_path:  # once, not every frame
                self._art_warned = album_art_path
                print(f"Could not load album art: {album_art_path} not readable")
            return None
        entry = self._art_cache_get(key, sig)
        if entry is None:
            pad = self._FRAME_PADDING
            frame_height = 318 - self._FRAME_Y
            album_height = frame_height - pad * 2
            entry = {'sig': sig, 'img': None}
            try:
                with Image.open(album_art_path) as album_img:
                    # Width follows the art's aspect ratio
                    aspect_ratio = album_img.size[0] / album_img.size[1]
                    album_width = int(album_height * aspect_ratio)
                    img = album_img.resize((album_width, album_height), Image.Resampling.LANCZOS)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                frame_width = album_width + pad * 2
                frame_x = self.width - frame_width - 2  # right-aligned
                entry.update(img=img, frame_x=frame_x, frame_width=frame_width,
                             pos=(frame_x + pad, self._FRAME_Y + pad))
            except Exception as e:
                # Remember the failure so a corrupt file is not re-decoded every frame
                print(f"Could not load album art: {e}")
            self._art_cache_put(key, entry)
        return entry if entry['img'] is not None else None

    def _prepare_placeholder(self):
        """Empty-state image (scancode placeholder, else gradient) with the
        fixed empty-state frame geometry. Caller must hold self._art_lock."""
        pad = self._FRAME_PADDING
        frame_height = 318 - self._FRAME_Y
        album_y = self._FRAME_Y + pad
        album_height = frame_height - pad * 2

        # Scancode is 800x1000 (0.8 aspect ratio), so when scaled to 304px height
        # it becomes ~243px wide. Add some padding for the frame.
        album_width = 250
        frame_width = album_width + pad * 2
        frame_x = self.width - frame_width - 2
        album_x = frame_x + pad

        # Falls back to leeloo_for_sharing.png if the dedicated scancode image doesn't exist
        home = _leeloo_home()
        here = os.path.dirname(os.path.abspath(__file__))
        scancode_paths = [
            os.path.join(home, 'leeloo_empty_scancode.png'),
            os.path.join(here, 'leeloo_empty_scancode.png'),
            'leeloo_empty_scancode.png',
            os.path.join(home, 'leeloo_for_sharing.png'),
            os.path.join(here, 'leeloo_for_sharing.png'),
        ]

        for scancode_path in scancode_paths:
            sig = self._file_sig(scancode_path)
            if sig is None:
                continue
            key = ('placeholder', scancode_path)
            entry = self._art_cache_get(key, sig)
            if entry is None:
                entry = {'sig': sig, 'img': None}
                try:
                    with Image.open(scancode_path) as src:
                        scancode_img = src.convert('RGB')
                    orig_w, orig_h = scancode_img.size
                    aspect_ratio = orig_w / orig_h

                    # Fit within the frame, maintaining aspect ratio
                    if aspect_ratio > (album_width / album_height):
                        new_width = album_width
                        new_height = int(new_width / aspect_ratio)
                    else:
                        new_height = album_height
                        new_width = int(new_height * aspect_ratio)

                    img = scancode_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    entry.update(img=img, frame_x=frame_x, frame_width=frame_width,
                                 pos=(album_x + (album_width - new_width) // 2,
                                      album_y + (album_height - new_height) // 2))
                except Exception as e:
                    print(f"Could not load scancode placeholder: {e}")
                self._art_cache_put(key, entry)
            if entry['img'] is not None:
                return entry

        # Final fallback: gradient (rendered once)
        key = ('gradient', COLORS['bg'])
        entry = self._art_cache_get(key, None)
        if entry is None:
            img = Image.new('RGB', (album_width + 1, album_height + 1), COLORS['bg'])
            self._draw_placeholder_gradient(0, 0, album_width, album_height, draw=ImageDraw.Draw(img))
            entry = {'sig': None, 'img': img, 'frame_x': frame_x, 'frame_width': frame_width,
                     'pos': (album_x, album_y)}
            self._art_cache_put(key, entry)
        return entry

    def _art_layout(self, album_art_path=None):
        """Prepared image + geometry for the right-hand art frame."""
        with self._art_lock:
            entry = self._prepare_art(album_art_path) if album_art_path else None
            if entry is None:
                entry = self._prepare_placeholder()
            return entry

    def album_frame_x(self, album_art_path=None) -> int:
        """
        Left x of the album art frame - exactly what draw_album_art() returns
        for the same path, without drawing anything. Thread-safe.
        """
        return self._art_layout(album_art_path)['frame_x']

    def draw_album_art(self, album_art_path=None, show_empty_scancode=False, draw=None, image=None):
        """
        Draw the album art on the right side - maintains aspect ratio, with frame

        Args:
            album_art_path: Path to album artwork image (optional)
            show_empty_scancode: (unused, kept for compatibility)
            draw, image: target ImageDraw / Image (None = self.draw / self.image)

        Returns:
            album_x: The x position where album art starts (so info panel knows where to stop)
        """
        draw = draw if draw is not None else self.draw
        image = image if image is not None else self.image

        entry = self._art_layout(album_art_path)
        frame_x = entry['frame_x']
        frame_y = self._FRAME_Y
        frame_height = 318 - frame_y

        # Frame first (2px thick like other boxes), then the prepared image
        draw.rectangle([frame_x, frame_y, frame_x + entry['frame_width'], frame_y + frame_height],
                       outline=COLORS['green'], width=2)
        image.paste(entry['img'], entry['pos'])
        return frame_x

    def _draw_placeholder_gradient(self, x, y, width, height, draw=None):
        """Draw a gradient placeholder for album art"""
        draw = draw if draw is not None else self.draw
        for i in range(height):
            color_val = int(255 * (i / height))
            draw.rectangle(
                [x, y + i, x + width, y + i + 1],
                fill=(color_val, 80, 255 - color_val)
            )

    def _new_frame(self):
        image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        return image, ImageDraw.Draw(image)

    def _publish_frame(self, image, draw):
        """Swap in a finished frame (readers never see a half-built one)."""
        self.draw = draw
        self.image = image
        return image

    def render(self, weather_data, time_data, contacts, album_data, album_art_path=None, unread_counts=None,
               status_text=None, hang=None, header=None, labels=None, custom_panel=None):
        """
        Render the complete display - MATCHES REACT UI EXACTLY

        Args:
            weather_data: dict with 'temp_f', 'uv_raw', 'rain_24h_inches';
                          None = "set location"; {'error': ...} = "weather offline"
            time_data: dict with 'time_str', 'date_str', 'seconds'
            contacts: list of contact names (strings, or dicts with a 'name' key)
            album_data: dict with 'artist', 'track', 'bpm', 'listeners', 'pushed_by'
            album_art_path: Path to album artwork image (optional)
            unread_counts: dict mapping sender name to unread count (e.g., {'Amy': 2})
            status_text: shown in the header instead of "tap to talk" when set
            hang: dict from gadget_data.format_countdown_display() (None = read it here)
            header: replaces the text "LEELOO v1.0" (pack header)
            labels: dict with any of weather/time/messages/album -> box label text
            custom_panel: dict from PackManager.current_panel(). Replaces the
                          content of the weather box; weather_data is not drawn

        Safe to call from a worker thread: the frame is built off to the side
        and only assigned to self.image / self.draw when complete.
        """
        with _theme_lock:       # one palette for the whole frame
            image, draw = self._new_frame()

            # Draw album art first (right side) - returns x position where it starts
            album_x = self.draw_album_art(album_art_path=album_art_path, draw=draw, image=image)

            # Draw left column panels, extending to meet album art (with small gap)
            info_panel_right = album_x - 5
            self.draw_left_column(weather_data, time_data, contacts, album_data, right_edge=info_panel_right,
                                  unread_counts=unread_counts, status_text=status_text, hang=hang, draw=draw,
                                  header=header, labels=labels, custom_panel=custom_panel)

            return self._publish_frame(image, draw)

    def draw_reaction_overlay(self, ascii_art: str, message: str):
        """
        Draw ASCII reaction animation overlay on top of current display

        Args:
            ascii_art: Multi-line ASCII art string
            message: Text like "Amy loved this"
        """
        # Create semi-transparent overlay
        overlay = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        # Draw darkened background (80% opacity)
        overlay_draw.rectangle(
            [0, 0, self.width, self.height],
            fill=_hex_to_rgb(COLORS['bg']) + (200,)  # bg color with alpha
        )

        # Draw ASCII art centered
        art_lines = ascii_art.strip().split('\n')
        # Calculate center position
        art_height = len(art_lines) * 12  # line height
        art_y = (self.height - art_height) // 2 - 30

        for i, line in enumerate(art_lines):
            # Draw each line centered
            try:
                text_bbox = self.font_med.getbbox(line)
                text_width = text_bbox[2] - text_bbox[0]
            except:
                # Fallback for older Pillow versions
                text_width = len(line) * 6
            x = (self.width - text_width) // 2
            y = art_y + i * 12

            overlay_draw.text(
                (x, y),
                line,
                font=self.font_med,
                fill=(255, 255, 255, 255)
            )

        # Draw message below art
        msg_y = art_y + len(art_lines) * 12 + 20
        try:
            msg_bbox = self.font_med.getbbox(message)
            msg_width = msg_bbox[2] - msg_bbox[0]
        except:
            msg_width = len(message) * 6
        msg_x = (self.width - msg_width) // 2

        overlay_draw.text(
            (msg_x, msg_y),
            message,
            font=self.font_med,
            fill=(185, 169, 201, 255)  # purple color
        )

        # Composite overlay onto main image
        base = self.image.convert('RGBA')
        composited = Image.alpha_composite(base, overlay)
        self.image = composited.convert('RGB')
        self.overlay_active = True

    def clear_overlay(self):
        """Remove reaction overlay"""
        self.overlay_active = False
        # In real implementation with real-time updates,
        # would restore from backup of previous screen

    def draw_expanded_message(self, sender_name, message_text, right_edge=158, draw=None):
        """
        Draw expanded message view - replaces the left column info panels
        Shows incoming message text (no GIF - that's for reactions)

        Args:
            sender_name: Name of message sender (e.g., "Amy")
            message_text: The message content
            right_edge: Right edge of the panel (default 158)
        """
        draw = draw if draw is not None else self.draw
        box_right = right_edge - 5
        frame_top = 4
        frame_bottom = 318

        # Clear and draw main frame
        draw.rectangle([2, frame_top, right_edge, frame_bottom],
                           fill=COLORS['bg'], outline=COLORS['lavender'], width=1)

        # Header - "message from [name]"
        header_y = frame_top
        draw.rectangle([6, header_y - 5, right_edge - 6, header_y + 7], fill=COLORS['bg'])
        header_text = f"message from {sender_name}"
        draw.text((8, header_y - 4), header_text, font=self.font_header, fill=COLORS['lavender'])

        # Inner message box (lavender border)
        msg_box_top = 16
        msg_box_height = 290  # Most of the frame
        draw.rectangle([7, msg_box_top, box_right, msg_box_top + msg_box_height],
                           outline=COLORS['lavender'], width=2)

        # Message text area - use most of the box
        text_y = msg_box_top + 20
        text_x = 12
        max_width = box_right - text_x - 5

        # Word wrap the message text
        words = message_text.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            try:
                test_width = self.font_small.getlength(test_line)
            except:
                test_width = len(test_line) * 8
            if test_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)

        # Draw message lines (more lines now since no GIF area)
        for i, line in enumerate(lines[:12]):
            draw.text((text_x, text_y + i * 18), line,
                          font=self.font_small, fill=COLORS['white'])

        # Reaction hints at bottom
        hint_y = msg_box_top + msg_box_height - 35
        hint_text = "double tap = love"
        try:
            hint_width = self.font_tiny.getlength(hint_text)
        except:
            hint_width = len(hint_text) * 7
        hint_x = (box_right + 7) // 2 - hint_width // 2
        draw.text((hint_x, hint_y), hint_text,
                      font=self.font_tiny, fill=self._dim_color(COLORS['lavender']))

        hint_text2 = "triple tap = fire"
        try:
            hint_width2 = self.font_tiny.getlength(hint_text2)
        except:
            hint_width2 = len(hint_text2) * 7
        hint_x2 = (box_right + 7) // 2 - hint_width2 // 2
        draw.text((hint_x2, hint_y + 14), hint_text2,
                      font=self.font_tiny, fill=self._dim_color(COLORS['lavender']))

    def draw_reaction_received(self, sender_name, gif_frame, right_edge=158, draw=None, image=None):
        """
        Draw reaction received view - shows "Ben -" with large GIF
        This is what the SENDER sees when someone reacts to their message

        Args:
            sender_name: Name of person who sent the reaction (e.g., "Ben")
            gif_frame: PIL Image of current GIF frame (heart or fire)
            right_edge: Right edge of the panel (default 158)
        """
        draw = draw if draw is not None else self.draw
        image = image if image is not None else self.image
        box_right = right_edge - 5
        frame_top = 4
        frame_bottom = 318

        # Clear and draw main frame
        draw.rectangle([2, frame_top, right_edge, frame_bottom],
                           fill=COLORS['bg'], outline=COLORS['lavender'], width=1)

        # Header - "reaction from [name]"
        header_y = frame_top
        draw.rectangle([6, header_y - 5, right_edge - 6, header_y + 7], fill=COLORS['bg'])
        header_text = f"reaction from {sender_name}"
        draw.text((8, header_y - 4), header_text, font=self.font_header, fill=COLORS['lavender'])

        # Inner box (lavender border)
        msg_box_top = 16
        msg_box_height = 290
        draw.rectangle([7, msg_box_top, box_right, msg_box_top + msg_box_height],
                           outline=COLORS['lavender'], width=2)

        # GIF takes up most of the box (no name text inside, it's in header)
        gif_area_top = msg_box_top + 10
        gif_area_bottom = msg_box_top + msg_box_height - 10
        gif_area_height = gif_area_bottom - gif_area_top
        gif_area_width = box_right - 10  # Almost full width

        # Calculate GIF dimensions maintaining aspect ratio
        if gif_frame:
            orig_w, orig_h = gif_frame.size
            aspect = orig_w / orig_h

            # Fit within available space
            if aspect > (gif_area_width / gif_area_height):
                # Width constrained
                new_width = gif_area_width
                new_height = int(new_width / aspect)
            else:
                # Height constrained
                new_height = gif_area_height
                new_width = int(new_height * aspect)

            # Center the GIF
            gif_x = 7 + (gif_area_width - new_width) // 2
            gif_y = gif_area_top + (gif_area_height - new_height) // 2

            # Resize and paste
            resized = gif_frame.copy()
            resized = resized.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Handle transparency/palette
            if resized.mode == 'RGBA':
                bg = Image.new('RGB', (new_width, new_height), COLORS['bg'])
                bg.paste(resized, mask=resized.split()[3])
                resized = bg
            elif resized.mode == 'P':
                resized = resized.convert('RGB')
            elif resized.mode != 'RGB':
                resized = resized.convert('RGB')

            image.paste(resized, (gif_x, gif_y))

    def render_message_view(self, sender_name, message_text, album_art_path=None):
        """
        Render the expanded message view (replaces normal display)
        Shows incoming message - user has 30 seconds to react

        Args:
            sender_name: Name of message sender
            message_text: The message content
            album_art_path: Path to album artwork (still shown on right)
        """
        image, draw = self._new_frame()

        # Draw album art on right side (same as normal view)
        album_x = self.draw_album_art(album_art_path=album_art_path, draw=draw, image=image)

        # Draw expanded message view on left
        info_panel_right = album_x - 5
        self.draw_expanded_message(sender_name, message_text, right_edge=info_panel_right, draw=draw)

        return self._publish_frame(image, draw)

    def render_reaction_view(self, sender_name, gif_frame, album_art_path=None):
        """
        Render the reaction received view
        Shows "Ben -" with the reaction GIF (fire/heart)

        Args:
            sender_name: Name of person who reacted
            gif_frame: PIL Image of current GIF frame
            album_art_path: Path to album artwork (still shown on right)
        """
        image, draw = self._new_frame()

        # Draw album art on right side
        album_x = self.draw_album_art(album_art_path=album_art_path, draw=draw, image=image)

        # Draw reaction view on left
        info_panel_right = album_x - 5
        self.draw_reaction_received(sender_name, gif_frame, right_edge=info_panel_right, draw=draw, image=image)

        return self._publish_frame(image, draw)

    def show(self):
        """Display the image (preview mode shows in window, hardware mode pushes to display)"""
        if self.preview_mode:
            # Save and show preview
            self.image.save('/tmp/leeloo_preview.png')
            print("Preview saved to /tmp/leeloo_preview.png")
            try:
                self.image.show()
            except:
                print("Could not open image viewer. Check /tmp/leeloo_preview.png")
        else:
            # Push to hardware display
            if self.display_device:
                # Code for Waveshare display would go here
                # self.display_device.display(self.display_device.getbuffer(self.image))
                pass
            else:
                print("⚠️  No hardware display available")


# Backwards compatibility alias
GadgetDisplay = LeelooDisplay


def demo():
    """Demo function with real weather data from Open-Meteo API"""
    import os
    from datetime import datetime

    # Real weather when the network is available (None -> empty state)
    try:
        from gadget_weather import get_weather
        weather_data = get_weather()
    except ImportError:
        weather_data = None
    if weather_data:
        print(f"Live weather: {weather_data['temp_f']}°F, UV: {weather_data['uv_raw']}")
    else:
        print("Weather not available, showing the empty state")

    display = LeelooDisplay(preview_mode=True)

    # Time data - use real time!
    now = datetime.now()
    time_data = {
        'time_str': now.strftime('%-I:%M %p'),
        'date_str': now.strftime('%b %-d'),
        'seconds': now.second,
    }

    # Contacts - just names, no message previews (matches React)
    contacts = ['Amy', 'Ben']

    # Album data (matches React: artist, track·BPM, listeners, pushed by)
    album_data = {
        'artist': 'Cinnamon Chasers',
        'track': 'Doorways',
        'bpm': 120,
        'listeners': '262K',
        'pushed_by': 'Amy',
    }

    # Try to find album art
    script_dir = os.path.dirname(os.path.abspath(__file__))
    album_art_path = os.path.join(script_dir, 'Retro-Music-Panel', 'client', 'public', 'doorways-album.jpg')

    if not os.path.exists(album_art_path):
        print(f"⚠️  Album art not found at {album_art_path}, using placeholder")
        album_art_path = None

    # Render and show
    display.render(weather_data, time_data, contacts, album_data, album_art_path=album_art_path)
    display.show()

    print("✅ Display rendered successfully!")


if __name__ == "__main__":
    demo()
