"""
LEELOO Display Module

Components for rendering and animating the LEELOO device display.
"""

from .frame_animator import (
    FrameAnimator,
    FrameType,
    FrameGeometry,
    FRAME_GEOMETRIES,
    EXPANDED_GEOMETRY,
    ease_in_out_cubic,
    frame_to_rgb565,
    write_region_to_framebuffer,
)

__all__ = [
    'FrameAnimator',
    'FrameType',
    'FrameGeometry',
    'FRAME_GEOMETRIES',
    'EXPANDED_GEOMETRY',
    'ease_in_out_cubic',
    'frame_to_rgb565',
    'write_region_to_framebuffer',
]
