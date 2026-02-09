#!/usr/bin/env python3
"""
Frame Expansion Animation System for LEELOO

Animates info panels (Weather, Time, Messages, Album) expanding from their
collapsed state to fill the left panel area over 2 seconds with smooth easing.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Tuple, Optional, Callable
import time
from PIL import Image, ImageDraw, ImageFont

# Import the display module for colors and fonts
try:
    import sys
    sys.path.insert(0, '/Users/nathanmills/Desktop/TipTop UI')
    from gadget_display import COLORS
except ImportError:
    COLORS = {
        'bg': '#1A1D2E',
        'green': '#719253',
        'purple': '#9C93DD',
        'rose': '#D6697F',
        'tan': '#C2995E',
        'lavender': '#A7AFD4',
        'white': '#FFFFFF',
    }


class FrameType(Enum):
    """Available frame types that can be expanded"""
    WEATHER = "weather"
    TIME = "time"
    MESSAGES = "messages"
    ALBUM = "album"


@dataclass
class FrameGeometry:
    """Defines a frame's position and dimensions"""
    x: int
    y: int
    width: int
    height: int
    color: str  # Border color hex
    label: str  # Frame label text

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


# Frame geometries in collapsed state (based on gadget_display.py)
# Note: These are relative to a right_edge of 158, box_right = 153
FRAME_GEOMETRIES = {
    FrameType.WEATHER: FrameGeometry(
        x=5, y=16, width=148, height=59,
        color=COLORS['tan'], label="weather"
    ),
    FrameType.TIME: FrameGeometry(
        x=5, y=83, width=148, height=71,
        color=COLORS['purple'], label="time"
    ),
    FrameType.MESSAGES: FrameGeometry(
        x=5, y=162, width=148, height=28,
        color=COLORS['lavender'], label="messages"
    ),
    FrameType.ALBUM: FrameGeometry(
        x=5, y=198, width=148, height=108,
        color=COLORS['green'], label="album"
    ),
}

# Expanded state - all frames expand to same target
EXPANDED_GEOMETRY = FrameGeometry(
    x=5, y=16, width=148, height=290,
    color=COLORS['lavender'], label=""  # Color will be overridden by frame type
)


def ease_in_out_cubic(t: float) -> float:
    """
    Cubic ease-in-out function for smooth animation

    Args:
        t: Progress value from 0.0 to 1.0

    Returns:
        Eased value from 0.0 to 1.0
    """
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2


def interpolate_value(start: int, end: int, t: float) -> int:
    """Linearly interpolate between two values"""
    return int(start + (end - start) * t)


def interpolate_geometry(start: FrameGeometry, end: FrameGeometry, t: float) -> FrameGeometry:
    """
    Interpolate between two frame geometries

    Args:
        start: Starting geometry
        end: Ending geometry
        t: Progress value from 0.0 to 1.0 (already eased)

    Returns:
        Interpolated FrameGeometry
    """
    return FrameGeometry(
        x=interpolate_value(start.x, end.x, t),
        y=interpolate_value(start.y, end.y, t),
        width=interpolate_value(start.width, end.width, t),
        height=interpolate_value(start.height, end.height, t),
        color=start.color,  # Keep original color
        label=start.label,
    )


def frame_to_rgb565(frame: Image.Image) -> bytes:
    """
    Convert PIL frame to RGB565 bytes for framebuffer

    Args:
        frame: PIL Image in RGB mode

    Returns:
        bytes in RGB565 format
    """
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


def write_region_to_framebuffer(
    frame_bytes: bytes,
    x: int,
    y: int,
    width: int,
    height: int,
    fb_path: str = '/dev/fb1',
    screen_width: int = 480
):
    """
    Write pre-computed RGB565 bytes to a region of framebuffer

    Args:
        frame_bytes: RGB565 bytes
        x, y: Top-left position
        width, height: Region dimensions
        fb_path: Framebuffer device path
        screen_width: Total screen width
    """
    with open(fb_path, 'r+b') as fb:
        for row in range(height):
            offset = ((y + row) * screen_width + x) * 2
            fb.seek(offset)
            row_start = row * width * 2
            row_end = row_start + width * 2
            fb.write(frame_bytes[row_start:row_end])


class FrameAnimator:
    """
    Handles frame expansion/collapse animations

    Usage:
        animator = FrameAnimator(display)
        animator.expand(FrameType.WEATHER)  # Expand weather panel
        # ... show expanded content ...
        animator.collapse(FrameType.WEATHER)  # Collapse back
    """

    # Animation parameters
    DURATION = 2.0  # seconds
    FPS = 20  # frames per second
    FRAME_COUNT = int(DURATION * FPS)  # 40 frames

    def __init__(self, display, fb_path: str = '/dev/fb1'):
        """
        Initialize the frame animator

        Args:
            display: LeelooDisplay instance
            fb_path: Framebuffer device path (use None for preview mode)
        """
        self.display = display
        self.fb_path = fb_path
        self.preview_mode = fb_path is None

        # Pre-compute easing values for smooth animation
        self.easing_values = [
            ease_in_out_cubic(i / (self.FRAME_COUNT - 1))
            for i in range(self.FRAME_COUNT)
        ]

        # Load fonts (reuse from display if available)
        try:
            self.font_tiny = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12
            )
        except OSError:
            self.font_tiny = ImageFont.load_default()

    def _dim_color(self, hex_color: str) -> str:
        """Return a dimmed version of the color (30% opacity effect)"""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        bg_r, bg_g, bg_b = 0x1A, 0x1D, 0x2E
        dim_r = int(r * 0.3 + bg_r * 0.7)
        dim_g = int(g * 0.3 + bg_g * 0.7)
        dim_b = int(b * 0.3 + bg_b * 0.7)

        return f'#{dim_r:02x}{dim_g:02x}{dim_b:02x}'

    def _render_frame_border(
        self,
        image: Image.Image,
        geometry: FrameGeometry,
        opacity: float = 1.0
    ):
        """
        Render just the frame border (no content)

        Args:
            image: PIL Image to draw on
            geometry: Current frame geometry
            opacity: Border opacity (0.0 to 1.0)
        """
        draw = ImageDraw.Draw(image)

        # Clear the frame area with background color
        draw.rectangle(
            [geometry.x, geometry.y, geometry.right, geometry.bottom],
            fill=COLORS['bg']
        )

        # Draw border with appropriate color
        border_color = geometry.color if opacity >= 0.5 else self._dim_color(geometry.color)
        draw.rectangle(
            [geometry.x, geometry.y, geometry.right, geometry.bottom],
            outline=border_color,
            width=2
        )

        # Draw label at top of frame (breaking the border)
        if geometry.label:
            label_x = geometry.x + 5
            try:
                label_width = self.font_tiny.getlength(geometry.label)
            except:
                label_width = len(geometry.label) * 7

            # Clear area behind label
            draw.rectangle(
                [label_x - 2, geometry.y - 6, label_x + label_width + 2, geometry.y + 6],
                fill=COLORS['bg']
            )
            draw.text(
                (label_x, geometry.y - 5),
                geometry.label,
                font=self.font_tiny,
                fill=geometry.color
            )

    def _get_animation_region(
        self,
        frame_type: FrameType,
        expanding: bool
    ) -> Tuple[int, int, int, int]:
        """
        Calculate the region that will be affected by the animation

        Returns:
            Tuple of (x, y, width, height) covering the full animation area
        """
        collapsed = FRAME_GEOMETRIES[frame_type]
        expanded = EXPANDED_GEOMETRY

        # The region covers from smallest y to largest y+height
        min_y = min(collapsed.y, expanded.y)
        max_bottom = max(collapsed.bottom, expanded.bottom)

        return (
            collapsed.x,
            min_y,
            collapsed.width,
            max_bottom - min_y
        )

    def expand(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable[[Image.Image, FrameGeometry], None]] = None,
        on_complete: Optional[Callable[[], None]] = None
    ):
        """
        Animate a frame expanding from collapsed to full size

        Args:
            frame_type: Which frame to expand
            content_drawer: Optional callback to draw expanded content
                           Called with (image, geometry) for each frame
            on_complete: Optional callback when animation completes
        """
        collapsed = FRAME_GEOMETRIES[frame_type]
        expanded = FrameGeometry(
            x=EXPANDED_GEOMETRY.x,
            y=EXPANDED_GEOMETRY.y,
            width=EXPANDED_GEOMETRY.width,
            height=EXPANDED_GEOMETRY.height,
            color=collapsed.color,  # Keep original frame color
            label=collapsed.label,
        )

        self._animate(collapsed, expanded, frame_type, content_drawer, on_complete)

    def collapse(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable[[Image.Image, FrameGeometry], None]] = None,
        on_complete: Optional[Callable[[], None]] = None
    ):
        """
        Animate a frame collapsing from full size back to collapsed

        Args:
            frame_type: Which frame to collapse
            content_drawer: Optional callback to draw collapsed content
            on_complete: Optional callback when animation completes
        """
        collapsed = FRAME_GEOMETRIES[frame_type]
        expanded = FrameGeometry(
            x=EXPANDED_GEOMETRY.x,
            y=EXPANDED_GEOMETRY.y,
            width=EXPANDED_GEOMETRY.width,
            height=EXPANDED_GEOMETRY.height,
            color=collapsed.color,
            label=collapsed.label,
        )

        self._animate(expanded, collapsed, frame_type, content_drawer, on_complete)

    def _animate(
        self,
        start_geom: FrameGeometry,
        end_geom: FrameGeometry,
        frame_type: FrameType,
        content_drawer: Optional[Callable],
        on_complete: Optional[Callable]
    ):
        """
        Run the actual animation loop

        Args:
            start_geom: Starting geometry
            end_geom: Ending geometry
            frame_type: Type of frame being animated
            content_drawer: Content drawing callback
            on_complete: Completion callback
        """
        # Get the animation region for efficient updates
        region = self._get_animation_region(frame_type, start_geom.height < end_geom.height)
        region_x, region_y, region_width, region_height = region

        frame_time = 1.0 / self.FPS
        start_time = time.time()

        for frame_idx in range(self.FRAME_COUNT):
            frame_start = time.time()

            # Get eased progress
            t = self.easing_values[frame_idx]

            # Interpolate geometry
            current_geom = interpolate_geometry(start_geom, end_geom, t)

            # Create frame image for the animation region
            region_image = Image.new('RGB', (region_width, region_height), COLORS['bg'])
            region_draw = ImageDraw.Draw(region_image)

            # Offset geometry to region coordinates
            offset_geom = FrameGeometry(
                x=current_geom.x - region_x,
                y=current_geom.y - region_y,
                width=current_geom.width,
                height=current_geom.height,
                color=current_geom.color,
                label=current_geom.label,
            )

            # Draw the expanding/collapsing border
            region_draw.rectangle(
                [offset_geom.x, offset_geom.y,
                 offset_geom.x + offset_geom.width, offset_geom.y + offset_geom.height],
                outline=current_geom.color,
                width=2
            )

            # Draw label if past 20% of animation
            if t > 0.2 and current_geom.label:
                label_x = offset_geom.x + 5
                try:
                    label_width = self.font_tiny.getlength(current_geom.label)
                except:
                    label_width = len(current_geom.label) * 7

                region_draw.rectangle(
                    [label_x - 2, offset_geom.y - 6, label_x + label_width + 2, offset_geom.y + 6],
                    fill=COLORS['bg']
                )
                region_draw.text(
                    (label_x, offset_geom.y - 5),
                    current_geom.label,
                    font=self.font_tiny,
                    fill=current_geom.color
                )

            # Draw content in last 40% of animation
            if content_drawer and t > 0.6:
                content_opacity = (t - 0.6) / 0.4  # 0 to 1 over last 40%
                # Pass the actual geometry (not offset) for content drawing
                content_drawer(region_image, current_geom, region_x, region_y)

            # Write to framebuffer or save preview
            if not self.preview_mode:
                frame_bytes = frame_to_rgb565(region_image)
                write_region_to_framebuffer(
                    frame_bytes, region_x, region_y,
                    region_width, region_height,
                    self.fb_path
                )
            else:
                # In preview mode, update display image
                self.display.image.paste(region_image, (region_x, region_y))

            # Maintain frame timing
            elapsed = time.time() - frame_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Call completion callback
        if on_complete:
            on_complete()

    def expand_with_fade(
        self,
        frame_type: FrameType,
        collapsed_content: Image.Image,
        expanded_content: Image.Image,
        on_complete: Optional[Callable[[], None]] = None
    ):
        """
        Expand with content fade transition

        First 20%: Fade out collapsed content
        20%-60%: Border animates (no content visible)
        Last 40%: Fade in expanded content

        Args:
            frame_type: Which frame to expand
            collapsed_content: Image of collapsed state content
            expanded_content: Image of expanded state content
            on_complete: Optional callback when animation completes
        """
        collapsed = FRAME_GEOMETRIES[frame_type]
        expanded = FrameGeometry(
            x=EXPANDED_GEOMETRY.x,
            y=EXPANDED_GEOMETRY.y,
            width=EXPANDED_GEOMETRY.width,
            height=EXPANDED_GEOMETRY.height,
            color=collapsed.color,
            label=collapsed.label,
        )

        region = self._get_animation_region(frame_type, True)
        region_x, region_y, region_width, region_height = region

        frame_time = 1.0 / self.FPS

        for frame_idx in range(self.FRAME_COUNT):
            frame_start = time.time()

            t = self.easing_values[frame_idx]
            current_geom = interpolate_geometry(collapsed, expanded, t)

            # Create frame image
            region_image = Image.new('RGB', (region_width, region_height), COLORS['bg'])
            region_draw = ImageDraw.Draw(region_image)

            # Content handling based on progress
            if t < 0.2:
                # Fade out collapsed content (0-20%)
                fade_out = 1.0 - (t / 0.2)
                if collapsed_content and fade_out > 0:
                    # Blend collapsed content with background
                    pass  # TODO: Implement fade blending if needed

            elif t >= 0.6:
                # Fade in expanded content (60-100%)
                fade_in = (t - 0.6) / 0.4
                if expanded_content and fade_in > 0:
                    # Paste expanded content at correct position
                    content_x = current_geom.x - region_x + 2
                    content_y = current_geom.y - region_y + 10
                    # Crop/resize content to fit current geometry
                    pass  # TODO: Implement fade blending if needed

            # Draw border
            offset_x = current_geom.x - region_x
            offset_y = current_geom.y - region_y
            region_draw.rectangle(
                [offset_x, offset_y,
                 offset_x + current_geom.width, offset_y + current_geom.height],
                outline=current_geom.color,
                width=2
            )

            # Draw label
            if current_geom.label:
                label_x = offset_x + 5
                try:
                    label_width = self.font_tiny.getlength(current_geom.label)
                except:
                    label_width = len(current_geom.label) * 7

                region_draw.rectangle(
                    [label_x - 2, offset_y - 6, label_x + label_width + 2, offset_y + 6],
                    fill=COLORS['bg']
                )
                region_draw.text(
                    (label_x, offset_y - 5),
                    current_geom.label,
                    font=self.font_tiny,
                    fill=current_geom.color
                )

            # Write to display
            if not self.preview_mode:
                frame_bytes = frame_to_rgb565(region_image)
                write_region_to_framebuffer(
                    frame_bytes, region_x, region_y,
                    region_width, region_height,
                    self.fb_path
                )
            else:
                self.display.image.paste(region_image, (region_x, region_y))

            # Timing
            elapsed = time.time() - frame_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        if on_complete:
            on_complete()


# Demo/test function
def demo():
    """Demo the frame expansion animation"""
    import sys
    sys.path.insert(0, '/Users/nathanmills/Desktop/TipTop UI')
    from gadget_display import LeelooDisplay

    print("Frame Expansion Animation Demo")
    print("=" * 40)

    # Create display in preview mode
    display = LeelooDisplay(preview_mode=True)

    # Render initial state
    weather_data = {'temp_f': 72, 'uv_raw': 5, 'rain_24h_inches': 0}
    time_data = {'time_str': '10:30 AM', 'date_str': 'Feb 8', 'seconds': 30}
    album_data = {
        'artist': 'Cinnamon Chasers',
        'track': 'Doorways',
        'bpm': 120,
        'listeners': '262K',
        'pushed_by': 'Amy',
    }

    display.render(weather_data, time_data, ['Amy', 'Ben'], album_data)
    display.image.save('/tmp/frame_expand_before.png')
    print("Initial state saved to /tmp/frame_expand_before.png")

    # Create animator in preview mode
    animator = FrameAnimator(display, fb_path=None)

    # Test weather expansion
    print("\nExpanding WEATHER frame...")
    animator.expand(FrameType.WEATHER)
    display.image.save('/tmp/frame_expand_weather.png')
    print("Expanded state saved to /tmp/frame_expand_weather.png")

    print("\nCollapsing WEATHER frame...")
    animator.collapse(FrameType.WEATHER)
    display.image.save('/tmp/frame_expand_after.png')
    print("Collapsed state saved to /tmp/frame_expand_after.png")

    print("\nDemo complete!")


if __name__ == "__main__":
    demo()
