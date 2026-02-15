#!/usr/bin/env python3
"""
LEELOO Album Art Utility
Centralized album art handling - ensures ALL album art is created at correct size (243x304)
"""
import os
import hashlib
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# Album art dimensions (LEELOO standard size)
ALBUM_ART_WIDTH = 243
ALBUM_ART_HEIGHT = 244  # Top portion for album art
SCANCODE_HEIGHT = 60    # Bottom bar height
TOTAL_HEIGHT = 304      # Total: 244 + 60 = 304

# Path to custom "Now Playing" bar image
LEELOO_HOME = "/home/pi/leeloo-ui"
NOW_PLAYING_PNG = os.path.join(LEELOO_HOME, "nowplaying.png")


def get_album_art_path(spotify_uri, album_art_dir):
    """
    Get the expected path for cached album art based on Spotify URI
    
    Args:
        spotify_uri: Spotify URI (e.g., 'spotify:track:...')
        album_art_dir: Directory where album art is cached
    
    Returns:
        Path to cached album art file
    """
    uri_hash = hashlib.md5(spotify_uri.encode()).hexdigest()[:12]
    return os.path.join(album_art_dir, f"{uri_hash}.jpg")


def create_now_playing_image(album_art_img):
    """
    Create 243x304 image with album art + "Now Playing" bar.
    Uses nowplaying.png if available, falls back to text.
    
    Args:
        album_art_img: PIL Image object of album art
    
    Returns:
        PIL Image (243x304) with "Now Playing" bar
    """
    # Resize album art to 243x244
    img_resized = album_art_img.resize((ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT), Image.Resampling.LANCZOS)
    
    # Create full image (243x304)
    full_img = Image.new('RGB', (ALBUM_ART_WIDTH, TOTAL_HEIGHT), color=(26, 29, 46))
    
    # Paste album art at top
    full_img.paste(img_resized, (0, 0))
    
    bar_top = ALBUM_ART_HEIGHT
    
    # Try to use the custom nowplaying.png
    if os.path.exists(NOW_PLAYING_PNG):
        try:
            np_img = Image.open(NOW_PLAYING_PNG).convert('RGBA')
            # Stretch to fill the entire scancode box (243x60)
            np_resized = np_img.resize((ALBUM_ART_WIDTH, SCANCODE_HEIGHT), Image.Resampling.LANCZOS)
            # Paste directly at the bar position — fills the whole box
            full_img.paste(np_resized, (0, bar_top), np_resized if np_resized.mode == 'RGBA' else None)
            return full_img
        except Exception as e:
            print(f"   ⚠️  Could not load nowplaying.png: {e}, falling back to text")
    
    # Fallback: draw text
    draw = ImageDraw.Draw(full_img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
        font = ImageFont.load_default()
    
    text = "Now Playing"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    text_x = (ALBUM_ART_WIDTH - text_width) // 2
    text_y = bar_top + (SCANCODE_HEIGHT - text_height) // 2
    
    draw.text((text_x, text_y), text, fill=(167, 175, 212), font=font)
    
    return full_img


def create_shared_image(album_art_img, scancode_img):
    """
    Create 243x304 image with album art + scancode bar
    
    Args:
        album_art_img: PIL Image object of album art
        scancode_img: PIL Image object of scancode (will be resized to 243x60)
    
    Returns:
        PIL Image (243x304) with scancode at bottom
    """
    # Resize album art to 243x244
    img_resized = album_art_img.resize((ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT), Image.Resampling.LANCZOS)
    
    # Create full image (243x304)
    full_img = Image.new('RGB', (ALBUM_ART_WIDTH, TOTAL_HEIGHT), color=(26, 29, 46))
    
    # Paste album art at top
    full_img.paste(img_resized, (0, 0))
    
    # Resize and paste scancode at bottom
    scancode_resized = scancode_img.resize((ALBUM_ART_WIDTH, SCANCODE_HEIGHT), Image.Resampling.LANCZOS)
    full_img.paste(scancode_resized, (0, ALBUM_ART_HEIGHT))
    
    return full_img


def download_and_create_album_art(album_art_url, spotify_uri, album_art_dir, source='currently_playing', scancode_url=None):
    """
    Download album art and create properly sized image (243x304)
    
    Args:
        album_art_url: URL to album art image
        spotify_uri: Spotify URI for caching
        album_art_dir: Directory to save cached images
        source: 'currently_playing' or 'shared'
        scancode_url: Optional scancode URL (for shared music)
    
    Returns:
        Path to created image file, or None on failure
    """
    if not album_art_url:
        return None
    
    # Create directory if needed
    os.makedirs(album_art_dir, exist_ok=True)
    
    # Get cache path
    art_path = get_album_art_path(spotify_uri, album_art_dir)
    
    # Check if already cached
    if os.path.exists(art_path):
        return art_path
    
    try:
        # Download album art
        response = requests.get(album_art_url, timeout=10)
        if response.status_code != 200:
            return None
        
        album_art_img = Image.open(BytesIO(response.content))
        
        # Create appropriate image based on source
        if source == 'currently_playing':
            # Currently playing: album art + "Now Playing" bar
            final_img = create_now_playing_image(album_art_img)
        else:
            # Shared music: album art + scancode
            if scancode_url:
                scancode_response = requests.get(scancode_url, timeout=10)
                if scancode_response.status_code == 200:
                    scancode_img = Image.open(BytesIO(scancode_response.content))
                    final_img = create_shared_image(album_art_img, scancode_img)
                else:
                    # Fallback: no scancode, use "Now Playing" bar
                    final_img = create_now_playing_image(album_art_img)
            else:
                # No scancode provided, use "Now Playing" bar
                final_img = create_now_playing_image(album_art_img)
        
        # Save to cache
        final_img.save(art_path, "JPEG", quality=95)
        print(f"   Created album art: {art_path} ({source})")
        return art_path
        
    except Exception as e:
        print(f"   Failed to create album art: {e}")
        return None


if __name__ == "__main__":
    print("LEELOO Album Art Utility")
    print(f"Standard size: {ALBUM_ART_WIDTH}x{TOTAL_HEIGHT}")
    print(f"  Album art: {ALBUM_ART_WIDTH}x{ALBUM_ART_HEIGHT}")
    print(f"  Bottom bar: {ALBUM_ART_WIDTH}x{SCANCODE_HEIGHT}")
    print(f"  Now Playing PNG: {NOW_PLAYING_PNG} (exists: {os.path.exists(NOW_PLAYING_PNG)})")
