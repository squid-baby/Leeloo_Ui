#!/usr/bin/env python3
"""
Gadget Display Renderer
Renders the tiptop-style terminal UI for the Gadget music sharing device
"""

from PIL import Image, ImageDraw, ImageFont
import time
from datetime import datetime

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

# Color palette - matches React UI exactly
COLORS = {
    'bg': '#1A1D2E',            # gadget-navy (dark background)
    'green': '#719253',          # gadget-green (album box)
    'purple': '#9C93DD',         # gadget-purple (time box)
    'rose': '#D6697F',           # gadget-rose (pushed by text)
    'tan': '#C2995E',            # gadget-tan (weather box)
    'lavender': '#A7AFD4',       # gadget-lavender (messages box)
    'white': '#FFFFFF',
    'orange': '#FF8800',         # Spotify code / UV high
    'yellow': '#D4A84B',         # UV moderate (muted yellow)
}

class GadgetDisplay:
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
            self.font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)  # For Gadget/tap to talk
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

    def _dim_color(self, hex_color):
        """
        Return a dimmed version of the color (30% opacity effect)

        Args:
            hex_color: Hex color string like '#719253'
        Returns:
            Dimmed hex color string
        """
        # Parse hex color
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        # Dim by mixing with background (30% original, 70% background)
        bg_r, bg_g, bg_b = 0x1A, 0x1D, 0x2E  # gadget-navy
        dim_r = int(r * 0.3 + bg_r * 0.7)
        dim_g = int(g * 0.3 + bg_g * 0.7)
        dim_b = int(b * 0.3 + bg_b * 0.7)

        return f'#{dim_r:02x}{dim_g:02x}{dim_b:02x}'

    def draw_slider(self, x, y, value, max_val, color, num_dots=20):
        """
        Draw a tiptop-style dot slider using ■ and □ characters

        Args:
            x, y: Position
            value: Current value
            max_val: Maximum value
            color: Slider color (for filled dots)
            num_dots: Number of dots in slider (default 20)
        """
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

        self.draw.text((x, y), filled_str, font=self.font_tiny, fill=color)
        # Calculate width of filled portion for positioning empty part
        if filled > 0:
            try:
                filled_bbox = self.font_tiny.getbbox(filled_str)
                filled_width = filled_bbox[2] - filled_bbox[0]
            except:
                filled_width = filled * 6
            self.draw.text((x + filled_width, y), empty_str, font=self.font_tiny, fill=self._dim_color(color))

    def draw_weather_slider(self, right_edge, y, filled, color, num_boxes=12, padding=5, deplete_left=False):
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
        filled = min(num_boxes, max(0, int(filled)))

        # Get width of one box character (using larger slider font)
        try:
            char_bbox = self.font_slider.getbbox("■")
            char_width = char_bbox[2] - char_bbox[0]
        except:
            char_width = 10

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

            self.draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_left_slider(self, left_edge, y, filled, color, num_boxes=6, padding=0):
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
        filled = min(num_boxes, max(0, int(filled)))

        # Get width of one box character (using larger slider font)
        try:
            char_bbox = self.font_slider.getbbox("■")
            char_width = char_bbox[2] - char_bbox[0]
        except:
            char_width = 10

        box_x = left_edge + padding
        for i in range(num_boxes):
            if i < filled:
                char = "■"
                box_color = color
            else:
                char = "□"
                box_color = self._dim_color(color)

            self.draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_uv_slider(self, right_edge, y, uv_value, padding=5):
        """
        Draw a 12-box UV slider with color-coded levels, right-justified

        UV Index colors:
        - 1-2: Green (Low)
        - 3-5: Yellow (Moderate)
        - 6-7: Orange (High)
        - 8-10: Rose/Red (Very High)
        - 11-12: Purple (Extreme)
        """
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
        try:
            char_bbox = self.font_slider.getbbox("■")
            char_width = char_bbox[2] - char_bbox[0]
        except:
            char_width = 10

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

            self.draw.text((box_x, y), char, font=self.font_slider, fill=box_color)
            box_x += char_width

    def draw_left_column(self, weather_data, time_data, contacts, album_data, right_edge=158):
        """
        Draw the left column with all info panels - MATCHES REACT UI EXACTLY

        Args:
            weather_data: dict with 'temp_f', 'sun', 'rain' (0-10 scale for sun/rain)
            time_data: dict with 'time_str', 'date_str', 'seconds' (0-60)
            contacts: list of contact names (just strings, no previews)
            album_data: dict with 'artist', 'track', 'bpm', 'listeners', 'pushed_by'
            right_edge: right edge x position for the info panel (default 158)
        """
        box_right = right_edge - 5  # Inner box right edge with padding

        # Main container border with header
        self.draw.rectangle([2, 4, right_edge, 318], outline=COLORS['lavender'], width=1)

        # Header text - breaks the frame
        header_y = 4
        # Draw background behind header text to "break" the frame
        self.draw.rectangle([6, header_y - 5, 155, header_y + 7], fill=COLORS['bg'])
        self.draw.text((8, header_y - 4), "Gadget v1.0", font=self.font_header, fill=COLORS['lavender'])
        self.draw.text((90, header_y - 4), "tap to talk", font=self.font_header, fill=COLORS['lavender'])

        # ===== WEATHER BOX ===== (tan border)
        y = 16
        box_height = 59
        self.draw.rectangle([5, y, box_right, y + box_height], outline=COLORS['tan'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = "weather"
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 10
        self.draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        self.draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['tan'])

        # Temperature: 0°F = 0 boxes, 100°F = 12 boxes
        temp_f = weather_data.get('temp_f', 72)
        temp_boxes = min(12, max(0, int(temp_f * 12 / 100)))
        self.draw.text((12, y + 8), f"{temp_f}°F", font=self.font_tiny, fill=COLORS['tan'])
        self.draw_weather_slider(box_right, y + 8, temp_boxes, COLORS['tan'])

        # UV index with color-coded slider (0-11+ scale)
        uv_raw = weather_data.get('uv_raw', 5)
        self.draw.text((12, y + 24), "ultra V", font=self.font_tiny, fill=COLORS['tan'])
        self.draw_uv_slider(box_right, y + 24, uv_raw)

        # Rain: 0" = 0 boxes, 6"+ = 12 boxes (24hr total)
        rain_inches = weather_data.get('rain_24h_inches', 0)
        rain_boxes = min(12, int(rain_inches * 12 / 6))
        self.draw.text((12, y + 40), "rain", font=self.font_tiny, fill=COLORS['tan'])
        self.draw_weather_slider(box_right, y + 40, rain_boxes, COLORS['tan'])

        # ===== TIME BOX ===== (purple border) - includes hang countdown
        y = 83
        box_height = 71  # Was 40, added 31px for hang rows (+3px taller)
        self.draw.rectangle([5, y, box_right, y + box_height], outline=COLORS['purple'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = "time"
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 10
        self.draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        self.draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['purple'])

        # Row 1: Time on left, date centered, "moon" on right
        self.draw.text((12, y + 8), time_data['time_str'], font=self.font_tiny, fill=COLORS['purple'])
        # Date centered
        date_str = time_data['date_str']
        try:
            date_width = self.font_tiny.getlength(date_str)
        except:
            date_width = len(date_str) * 7
        date_x = (box_right + 12) // 2 - date_width // 2
        self.draw.text((date_x, y + 8), date_str, font=self.font_tiny, fill=COLORS['purple'])
        # "moon" label on right
        self.draw.text((box_right - 38, y + 8), "moon", font=self.font_tiny, fill=COLORS['purple'])

        # Row 2: Dual 6-box sliders (time left, moon right)
        seconds = time_data.get('seconds', 0)
        seconds_boxes = min(6, int(seconds / 10))
        self.draw_left_slider(12, y + 22, seconds_boxes, COLORS['purple'], num_boxes=6)
        moon_phase = get_moon_phase()
        self.draw_weather_slider(box_right, y + 22, moon_phase, COLORS['purple'], num_boxes=6)

        # Row 3: Hang date/time (moved down 3px)
        hang = format_countdown_display()
        hang_str = f"nxt hang  {hang['date_str']} {hang['time_str']}"
        self.draw.text((12, y + 39), hang_str, font=self.font_tiny, fill=COLORS['purple'])

        # Row 4: Countdown text + 9-box slider (moved down 3px)
        self.draw.text((12, y + 53), f"t minus {hang['countdown_str']}", font=self.font_tiny, fill=COLORS['purple'])
        # Slider starts full (9 boxes) and empties from left as time elapses
        slider_boxes_9 = min(9, int(hang['slider_boxes'] * 9 / 12))
        self.draw_weather_slider(box_right, y + 53, slider_boxes_9, COLORS['purple'], num_boxes=9, deplete_left=True)

        # ===== MESSAGES BOX ===== (lavender border) - shifted down for hang rows
        y = 162  # Was 131, shifted +31px
        box_height = 28
        self.draw.rectangle([5, y, box_right, y + box_height], outline=COLORS['lavender'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = "messages"
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 10
        self.draw.rectangle([label_x - 2, y - 1, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        self.draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['lavender'])

        # Show contact names with unread count badges in front of each name
        # Layout: "○Amy  ②Ben" - white circle for 0, circled numbers for counts
        self.draw.text((12, y + 7), "○", font=self.font_symbol, fill=COLORS['lavender'])
        self.draw.text((25, y + 8), "Amy  ", font=self.font_tiny, fill=COLORS['white'])
        self.draw.text((68, y + 7), "②", font=self.font_symbol, fill=COLORS['lavender'])
        self.draw.text((81, y + 8), "Ben", font=self.font_tiny, fill=COLORS['white'])

        # ===== ALBUM BOX ===== (green border) - shifted down for hang rows
        y = 198  # Was 167, shifted +31px
        box_height = 108  # Was 142, reduced 34px to fit
        self.draw.rectangle([5, y, box_right, y + box_height], outline=COLORS['green'], width=2)
        # Draw background behind label to "break" the frame, centered
        label = "album"
        label_width = self.font_tiny.getlength(label) if hasattr(self.font_tiny, 'getlength') else len(label) * 7
        label_x = 10
        self.draw.rectangle([label_x - 2, y - 6, label_x + label_width + 2, y + 6], fill=COLORS['bg'])
        self.draw.text((label_x, y - 5), label, font=self.font_tiny, fill=COLORS['green'])

        # Artist name (large, bold green)
        self.draw.text((8, y + 10), album_data['artist'], font=self.font_large, fill=COLORS['green'])

        # Song name (large, same size as artist)
        self.draw.text((8, y + 28), f'"{album_data["track"]}"', font=self.font_large, fill=COLORS['green'])

        # BPM (smaller)
        self.draw.text((8, y + 48), f"{album_data['bpm']} BPM", font=self.font_tiny, fill=COLORS['green'])

        # Monthly listeners
        self.draw.text((8, y + 62), f"{album_data['listeners']} monthly listeners", font=self.font_tiny, fill=COLORS['green'])

        # pushed by (rose color)
        self.draw.text((8, y + 78), f"pushed by {album_data['pushed_by']}", font=self.font_tiny, fill=COLORS['rose'])
    
    def draw_album_art(self, album_art_path=None):
        """
        Draw the album art on the right side - maintains aspect ratio, with frame

        Args:
            album_art_path: Path to album artwork image (optional)

        Returns:
            album_x: The x position where album art starts (so info panel knows where to stop)
        """
        # Frame padding (same as info boxes have to main frame)
        frame_padding = 5
        frame_y = 4  # Same as main container top
        frame_height = 318 - frame_y  # Same as main container

        album_y = frame_y + frame_padding
        album_height = frame_height - (frame_padding * 2)

        # Try to load album art and maintain proportions
        if album_art_path:
            try:
                album_img = Image.open(album_art_path)
                # Calculate width to maintain aspect ratio
                aspect_ratio = album_img.size[0] / album_img.size[1]
                album_width = int(album_height * aspect_ratio)

                # Frame position (right-aligned)
                frame_width = album_width + (frame_padding * 2)
                frame_x = self.width - frame_width - 2

                album_x = frame_x + frame_padding

                # Draw frame first (2px thick like other boxes)
                self.draw.rectangle([frame_x, frame_y, frame_x + frame_width, frame_y + frame_height],
                                   outline=COLORS['green'], width=2)

                # Resize and paste album art
                album_img = album_img.resize((album_width, album_height), Image.Resampling.LANCZOS)
                self.image.paste(album_img, (album_x, album_y))
                return frame_x
            except Exception as e:
                print(f"⚠️  Could not load album art: {e}")

        # Fallback: assume square album art
        album_width = album_height
        frame_width = album_width + (frame_padding * 2)
        frame_x = self.width - frame_width - 2
        album_x = frame_x + frame_padding

        # Draw frame
        self.draw.rectangle([frame_x, frame_y, frame_x + frame_width, frame_y + frame_height],
                           outline=COLORS['green'], width=2)
        self._draw_placeholder_gradient(album_x, album_y, album_width, album_height)
        return frame_x

    def _draw_placeholder_gradient(self, x, y, width, height):
        """Draw a gradient placeholder for album art"""
        for i in range(height):
            color_val = int(255 * (i / height))
            self.draw.rectangle(
                [x, y + i, x + width, y + i + 1],
                fill=(color_val, 80, 255 - color_val)
            )
    
    def render(self, weather_data, time_data, contacts, album_data, album_art_path=None):
        """
        Render the complete display - MATCHES REACT UI EXACTLY

        Args:
            weather_data: dict with 'temp_f', 'sun', 'rain' (0-10 scale)
            time_data: dict with 'time_str', 'date_str', 'seconds'
            contacts: list of contact name strings (e.g., ['Amy', 'Ben'])
            album_data: dict with 'artist', 'track', 'bpm', 'listeners', 'pushed_by'
            album_art_path: Path to album artwork image (optional)
        """
        # Clear previous image
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)

        # Draw album art first (right side) - returns x position where it starts
        album_x = self.draw_album_art(album_art_path=album_art_path)

        # Draw left column panels, extending to meet album art (with small gap)
        info_panel_right = album_x - 5
        self.draw_left_column(weather_data, time_data, contacts, album_data, right_edge=info_panel_right)

        return self.image
    
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
            fill=(26, 29, 46, 200)  # bg color with alpha
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

    def draw_expanded_message(self, sender_name, message_text, right_edge=158):
        """
        Draw expanded message view - replaces the left column info panels
        Shows incoming message text (no GIF - that's for reactions)

        Args:
            sender_name: Name of message sender (e.g., "Amy")
            message_text: The message content
            right_edge: Right edge of the panel (default 158)
        """
        box_right = right_edge - 5
        frame_top = 4
        frame_bottom = 318

        # Clear and draw main frame
        self.draw.rectangle([2, frame_top, right_edge, frame_bottom],
                           fill=COLORS['bg'], outline=COLORS['lavender'], width=1)

        # Header - "message from [name]"
        header_y = frame_top
        self.draw.rectangle([6, header_y - 5, right_edge - 6, header_y + 7], fill=COLORS['bg'])
        header_text = f"message from {sender_name}"
        self.draw.text((8, header_y - 4), header_text, font=self.font_header, fill=COLORS['lavender'])

        # Inner message box (lavender border)
        msg_box_top = 16
        msg_box_height = 290  # Most of the frame
        self.draw.rectangle([5, msg_box_top, box_right, msg_box_top + msg_box_height],
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
            self.draw.text((text_x, text_y + i * 18), line,
                          font=self.font_small, fill=COLORS['white'])

        # Reaction hints at bottom
        hint_y = msg_box_top + msg_box_height - 35
        hint_text = "double tap = love"
        try:
            hint_width = self.font_tiny.getlength(hint_text)
        except:
            hint_width = len(hint_text) * 7
        hint_x = (box_right + 5) // 2 - hint_width // 2
        self.draw.text((hint_x, hint_y), hint_text,
                      font=self.font_tiny, fill=self._dim_color(COLORS['lavender']))

        hint_text2 = "triple tap = fire"
        try:
            hint_width2 = self.font_tiny.getlength(hint_text2)
        except:
            hint_width2 = len(hint_text2) * 7
        hint_x2 = (box_right + 5) // 2 - hint_width2 // 2
        self.draw.text((hint_x2, hint_y + 14), hint_text2,
                      font=self.font_tiny, fill=self._dim_color(COLORS['lavender']))

    def draw_reaction_received(self, sender_name, gif_frame, right_edge=158):
        """
        Draw reaction received view - shows "Ben -" with large GIF
        This is what the SENDER sees when someone reacts to their message

        Args:
            sender_name: Name of person who sent the reaction (e.g., "Ben")
            gif_frame: PIL Image of current GIF frame (heart or fire)
            right_edge: Right edge of the panel (default 158)
        """
        box_right = right_edge - 5
        frame_top = 4
        frame_bottom = 318

        # Clear and draw main frame
        self.draw.rectangle([2, frame_top, right_edge, frame_bottom],
                           fill=COLORS['bg'], outline=COLORS['lavender'], width=1)

        # Header - "reaction from [name]"
        header_y = frame_top
        self.draw.rectangle([6, header_y - 5, right_edge - 6, header_y + 7], fill=COLORS['bg'])
        header_text = f"reaction from {sender_name}"
        self.draw.text((8, header_y - 4), header_text, font=self.font_header, fill=COLORS['lavender'])

        # Inner box (lavender border)
        msg_box_top = 16
        msg_box_height = 290
        self.draw.rectangle([5, msg_box_top, box_right, msg_box_top + msg_box_height],
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
            gif_x = 5 + (gif_area_width - new_width) // 2
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

            self.image.paste(resized, (gif_x, gif_y))

    def render_message_view(self, sender_name, message_text, album_art_path=None):
        """
        Render the expanded message view (replaces normal display)
        Shows incoming message - user has 30 seconds to react

        Args:
            sender_name: Name of message sender
            message_text: The message content
            album_art_path: Path to album artwork (still shown on right)
        """
        # Clear previous image
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)

        # Draw album art on right side (same as normal view)
        album_x = self.draw_album_art(album_art_path=album_art_path)

        # Draw expanded message view on left
        info_panel_right = album_x - 5
        self.draw_expanded_message(sender_name, message_text, right_edge=info_panel_right)

        return self.image

    def render_reaction_view(self, sender_name, gif_frame, album_art_path=None):
        """
        Render the reaction received view
        Shows "Ben -" with the reaction GIF (fire/heart)

        Args:
            sender_name: Name of person who reacted
            gif_frame: PIL Image of current GIF frame
            album_art_path: Path to album artwork (still shown on right)
        """
        # Clear previous image
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)

        # Draw album art on right side
        album_x = self.draw_album_art(album_art_path=album_art_path)

        # Draw reaction view on left
        info_panel_right = album_x - 5
        self.draw_reaction_received(sender_name, gif_frame, right_edge=info_panel_right)

        return self.image

    def show(self):
        """Display the image (preview mode shows in window, hardware mode pushes to display)"""
        if self.preview_mode:
            # Save and show preview
            self.image.save('/tmp/gadget_preview.png')
            print("💾 Preview saved to /tmp/gadget_preview.png")
            try:
                self.image.show()
            except:
                print("⚠️  Could not open image viewer. Check /tmp/gadget_preview.png")
        else:
            # Push to hardware display
            if self.display_device:
                # Code for Waveshare display would go here
                # self.display_device.display(self.display_device.getbuffer(self.image))
                pass
            else:
                print("⚠️  No hardware display available")


def demo():
    """Demo function with real weather data from Open-Meteo API"""
    import os
    from datetime import datetime

    # Try to import weather module for real data
    try:
        from gadget_weather import get_weather
        weather_data = get_weather()
        print(f"🌡️  Live weather: {weather_data['temp_f']}°F, UV: {weather_data['uv_raw']}, Rain: {weather_data['rain_24h_inches']:.2f}\"")
    except ImportError:
        print("⚠️  Weather module not found, using fallback data")
        weather_data = {
            'temp_f': 72,
            'uv_raw': 5,
            'rain_24h_inches': 0.5,
        }

    display = GadgetDisplay(preview_mode=True)

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
