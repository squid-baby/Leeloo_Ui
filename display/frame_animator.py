#!/usr/bin/env python3
"""
Frame Expansion Animation System for LEELOO - FIXED VERSION

Key change: Uses row-by-row file I/O instead of numpy memmap
to avoid tearing. This matches the approach used in the working
reaction GIF demo.

The tradeoff: Slightly slower, but NO TEARING.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Tuple, Optional, Callable, List
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Import colors
try:
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

# Screen constants
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320


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
    color: str
    label: str

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def get_frame_geometries(box_right: int = 153):
    """Get frame geometries based on actual box_right value"""
    left_padding = 7  # 2px padding from outer frame edge (5) = 7
    width = box_right - left_padding
    return {
        FrameType.WEATHER: FrameGeometry(x=left_padding, y=16, width=width, height=59, color=COLORS['tan'], label="weather"),
        FrameType.TIME: FrameGeometry(x=left_padding, y=83, width=width, height=71, color=COLORS['purple'], label="time"),
        FrameType.MESSAGES: FrameGeometry(x=left_padding, y=162, width=width, height=28, color=COLORS['lavender'], label="messages"),
        FrameType.ALBUM: FrameGeometry(x=left_padding, y=198, width=width, height=108, color=COLORS['green'], label="album"),
    }


def get_expanded_geometry(box_right: int = 153, color: str = '#A7AFD4', label: str = ""):
    """Get expanded frame geometry"""
    left_padding = 7  # 2px padding from outer frame edge (5) = 7
    width = box_right - left_padding
    return FrameGeometry(x=left_padding, y=16, width=width, height=290, color=color, label=label)


def ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in-out for smooth animation"""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2


def interpolate_value(start: int, end: int, t: float) -> int:
    """Linearly interpolate between two values"""
    return int(start + (end - start) * t)


def interpolate_geometry(start: FrameGeometry, end: FrameGeometry, t: float) -> FrameGeometry:
    """Interpolate between two frame geometries"""
    return FrameGeometry(
        x=interpolate_value(start.x, end.x, t),
        y=interpolate_value(start.y, end.y, t),
        width=interpolate_value(start.width, end.width, t),
        height=interpolate_value(start.height, end.height, t),
        color=start.color,
        label=start.label,
    )


def rgb_to_rgb565_fast(image: Image.Image) -> np.ndarray:
    """
    Convert PIL RGB image to RGB565 numpy array using vectorized operations
    """
    if image.mode != 'RGB':
        image = image.convert('RGB')

    img_array = np.array(image, dtype=np.uint16)

    r = (img_array[:, :, 0] >> 3) << 11
    g = (img_array[:, :, 1] >> 2) << 5
    b = img_array[:, :, 2] >> 3

    rgb565 = (r | g | b).astype(np.uint16)
    return rgb565


def write_region_to_framebuffer_rowbyrow(
    rgb565_array: np.ndarray,
    x: int,
    y: int,
    fb_path: str = '/dev/fb1',
    screen_width: int = SCREEN_WIDTH
):
    """
    Write RGB565 array to framebuffer using row-by-row file I/O.

    This is the KEY FIX - matches the working GIF approach.
    Slower than memmap, but no tearing!
    """
    height, width = rgb565_array.shape

    with open(fb_path, 'r+b') as fb:
        for row in range(height):
            # Calculate byte offset for this row
            offset = ((y + row) * screen_width + x) * 2

            # Get row data as bytes
            row_data = rgb565_array[row, :].tobytes()

            # Seek and write
            fb.seek(offset)
            fb.write(row_data)


class FrameAnimator:
    """
    Frame expansion animations - FIXED VERSION

    Uses row-by-row file I/O to avoid tearing.
    """

    DURATION = 1.5  # seconds (reduced from 2.0 for snappier feel)
    FPS = 24        # reduced from 30 - less frames = less chance of tearing
    FRAME_COUNT = int(DURATION * FPS)

    def __init__(self, display, box_right: int = 153, fb_path: str = '/dev/fb1'):
        """
        Initialize the frame animator

        Args:
            display: LeelooDisplay instance
            box_right: Right edge of info panel boxes
            fb_path: Framebuffer device path (None for preview mode)
        """
        self.display = display
        self.box_right = box_right
        self.fb_path = fb_path
        self.preview_mode = fb_path is None

        # Frame geometries
        self.frame_geometries = get_frame_geometries(box_right)
        self.expanded_geometry = get_expanded_geometry(box_right)

        # Pre-compute easing values
        self.easing_values = [
            ease_in_out_cubic(i / (self.FRAME_COUNT - 1))
            for i in range(self.FRAME_COUNT)
        ]

        # Load font
        try:
            self.font_tiny = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12
            )
        except OSError:
            self.font_tiny = ImageFont.load_default()

    def _preprocess_frames(
        self,
        start_geom: FrameGeometry,
        end_geom: FrameGeometry,
        region: Tuple[int, int, int, int],
        content_drawer: Optional[Callable] = None
    ) -> List[np.ndarray]:
        """
        Pre-process all animation frames to RGB565 numpy arrays
        """
        region_x, region_y, region_width, region_height = region
        processed_frames = []

        for frame_idx in range(self.FRAME_COUNT):
            t = self.easing_values[frame_idx]
            current_geom = interpolate_geometry(start_geom, end_geom, t)

            # Create frame image
            region_image = Image.new('RGB', (region_width, region_height), COLORS['bg'])
            region_draw = ImageDraw.Draw(region_image)

            # Calculate offset position within region
            offset_x = current_geom.x - region_x
            offset_y = current_geom.y - region_y

            # Draw the expanding/collapsing frame (filled background + border)
            rect_x1 = max(0, offset_x)
            rect_y1 = max(0, offset_y)
            rect_x2 = min(region_width - 1, offset_x + current_geom.width)
            rect_y2 = min(region_height - 1, offset_y + current_geom.height)

            # Fill interior with background color first
            region_draw.rectangle(
                [rect_x1, rect_y1, rect_x2, rect_y2],
                fill=COLORS['bg'],
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

                label_bg_y1 = max(0, offset_y - 6)
                label_bg_y2 = min(region_height - 1, offset_y + 6)
                region_draw.rectangle(
                    [label_x - 2, label_bg_y1, label_x + label_width + 2, label_bg_y2],
                    fill=COLORS['bg']
                )
                label_text_y = max(0, offset_y - 5)
                region_draw.text(
                    (label_x, label_text_y),
                    current_geom.label,
                    font=self.font_tiny,
                    fill=current_geom.color
                )

            # Draw content only on the LAST frame
            if content_drawer and frame_idx == self.FRAME_COUNT - 1:
                content_drawer(region_image, current_geom, region_x, region_y)

            # Convert to RGB565 numpy array
            rgb565 = rgb_to_rgb565_fast(region_image)
            processed_frames.append(rgb565)

        return processed_frames

    def expand(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        """Animate a frame expanding from collapsed to full size"""
        collapsed = self.frame_geometries[frame_type]
        expanded = FrameGeometry(
            x=self.expanded_geometry.x,
            y=self.expanded_geometry.y,
            width=self.expanded_geometry.width,
            height=self.expanded_geometry.height,
            color=collapsed.color,
            label=collapsed.label,
        )
        self._run_animation(collapsed, expanded, content_drawer, on_complete)

    def collapse(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        """Animate a frame collapsing from full size back to collapsed"""
        collapsed = self.frame_geometries[frame_type]
        expanded = FrameGeometry(
            x=self.expanded_geometry.x,
            y=self.expanded_geometry.y,
            width=self.expanded_geometry.width,
            height=self.expanded_geometry.height,
            color=collapsed.color,
            label=collapsed.label,
        )
        self._run_animation(expanded, collapsed, content_drawer, on_complete)

    def _run_animation(
        self,
        start_geom: FrameGeometry,
        end_geom: FrameGeometry,
        content_drawer: Optional[Callable],
        on_complete: Optional[Callable]
    ):
        """Run the animation with pre-processed frames"""
        # Animation region (7 = 2px padding from outer frame edge at 5)
        region = (7, 10, self.box_right - 7, 296)
        region_x, region_y, region_width, region_height = region

        # Pre-process all frames
        print(f"    Pre-processing {self.FRAME_COUNT} frames...")
        preprocess_start = time.time()
        processed_frames = self._preprocess_frames(start_geom, end_geom, region, content_drawer)
        preprocess_time = time.time() - preprocess_start
        print(f"    Pre-processing done in {preprocess_time*1000:.0f}ms")

        # Play animation using row-by-row writes (NO TEARING!)
        print(f"    Playing animation at {self.FPS}fps (row-by-row I/O)...")
        frame_time = 1.0 / self.FPS
        start_time = time.time()

        for frame_idx in range(self.FRAME_COUNT):
            frame_array = processed_frames[frame_idx]

            if not self.preview_mode:
                # KEY FIX: Use row-by-row file I/O instead of memmap
                write_region_to_framebuffer_rowbyrow(
                    frame_array,
                    region_x,
                    region_y,
                    self.fb_path
                )

            # Maintain timing
            elapsed = time.time() - start_time
            next_frame_time = (frame_idx + 1) * frame_time
            sleep_time = next_frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        actual_duration = time.time() - start_time
        actual_fps = self.FRAME_COUNT / actual_duration
        print(f"    Completed: {self.FRAME_COUNT} frames in {actual_duration:.2f}s ({actual_fps:.1f} fps)")

        if on_complete:
            on_complete()

    # =========================================================================
    # ASYNC versions — for use with leeloo_brain.py asyncio event loop
    # =========================================================================

    async def async_expand(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        """Async version of expand — yields to event loop between frames"""
        collapsed = self.frame_geometries[frame_type]
        expanded = FrameGeometry(
            x=self.expanded_geometry.x,
            y=self.expanded_geometry.y,
            width=self.expanded_geometry.width,
            height=self.expanded_geometry.height,
            color=collapsed.color,
            label=collapsed.label,
        )
        await self._async_run_animation(collapsed, expanded, content_drawer, on_complete)

    async def async_collapse(
        self,
        frame_type: FrameType,
        content_drawer: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        """Async version of collapse — yields to event loop between frames"""
        collapsed = self.frame_geometries[frame_type]
        expanded = FrameGeometry(
            x=self.expanded_geometry.x,
            y=self.expanded_geometry.y,
            width=self.expanded_geometry.width,
            height=self.expanded_geometry.height,
            color=collapsed.color,
            label=collapsed.label,
        )
        await self._async_run_animation(expanded, collapsed, content_drawer, on_complete)

    async def _async_run_animation(
        self,
        start_geom: FrameGeometry,
        end_geom: FrameGeometry,
        content_drawer: Optional[Callable],
        on_complete: Optional[Callable]
    ):
        """Run animation with async sleep for non-blocking playback"""
        import asyncio

        region = (7, 10, self.box_right - 7, 296)
        region_x, region_y, region_width, region_height = region

        # Pre-process all frames (CPU-bound, runs synchronously)
        processed_frames = self._preprocess_frames(start_geom, end_geom, region, content_drawer)

        # Play animation with async sleep between frames
        frame_time = 1.0 / self.FPS
        start_time = time.time()

        for frame_idx in range(self.FRAME_COUNT):
            frame_array = processed_frames[frame_idx]

            if not self.preview_mode:
                write_region_to_framebuffer_rowbyrow(
                    frame_array,
                    region_x,
                    region_y,
                    self.fb_path
                )

            # Maintain timing with async sleep (yields to event loop!)
            elapsed = time.time() - start_time
            next_frame_time = (frame_idx + 1) * frame_time
            sleep_time = next_frame_time - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        if on_complete:
            on_complete()


# For backwards compatibility
FRAME_GEOMETRIES = get_frame_geometries(153)
EXPANDED_GEOMETRY = get_expanded_geometry(153)
LEFT_PADDING = 7  # 2px padding from outer frame edge
