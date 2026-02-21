#!/usr/bin/env python3
"""
Spotify OAuth with QR code in album art area
Fixed: Better connection handling and keepalive
"""
import asyncio
import websockets
import json
import sys
import uuid
import qrcode
from PIL import Image, ImageDraw, ImageFont
import os

RELAY_URL = "wss://leeloobot.xyz/ws"
SPOTIFY_CLIENT_ID = "f8c3c0120e694af283d7d7f7c2f67d4c"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SCOPES = "user-read-currently-playing user-read-playback-state"

def generate_auth_url(device_id):
    """Generate Spotify OAuth URL"""
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "https://leeloobot.xyz/spotify/callback",
        "scope": SCOPES,
        "state": device_id,
        "show_dialog": "true"
    }
    
    query_string = "&".join([f"{k}={v.replace(' ', '%20')}" for k, v in params.items()])
    return f"{SPOTIFY_AUTH_URL}?{query_string}"

def create_album_art_qr(auth_url):
    """Create QR code image for album art area (243x304)"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=3,
        border=1,
    )
    qr.add_data(auth_url)
    qr.make(fit=True)
    
    qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
    display = Image.new('RGB', (243, 304), color=(26, 29, 46))
    draw = ImageDraw.Draw(display)
    
    qr_width, qr_height = qr_img.size
    qr_x = (243 - qr_width) // 2
    qr_y = 40
    
    display.paste(qr_img, (qr_x, qr_y, qr_x + qr_width, qr_y + qr_height))
    
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except:
        font_title = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    title = "Scan to Connect"
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(((243 - title_width) // 2, 12), title, fill=(156, 147, 221), font=font_title)
    
    bottom_y = 244 + 10
    inst1 = "Open camera on phone"
    inst1_bbox = draw.textbbox((0, 0), inst1, font=font_small)
    inst1_width = inst1_bbox[2] - inst1_bbox[0]
    draw.text(((243 - inst1_width) // 2, bottom_y), inst1, fill=(167, 175, 212), font=font_small)
    
    inst2 = "Tap to authorize Spotify"
    inst2_bbox = draw.textbbox((0, 0), inst2, font=font_small)
    inst2_width = inst2_bbox[2] - inst2_bbox[0]
    draw.text(((243 - inst2_width) // 2, bottom_y + 18), inst2, fill=(167, 175, 212), font=font_small)
    
    return display

def save_as_album_art(image):
    """Save QR code as temporary album art"""
    qr_path = "/tmp/spotify_auth_qr.jpg"
    image.save(qr_path, "JPEG", quality=95)
    
    music_data = {
        "artist": "Spotify",
        "track": "Authorization Required",
        "album": "",
        "spotify_uri": "",
        "album_art_cached": qr_path,
        "pushed_by": "Scan QR code to connect",
        "source": "auth_qr"
    }
    
    with open('/home/pi/leeloo-ui/current_music.json', 'w') as f:
        json.dump(music_data, f, indent=2)
    
    print(f"   QR code saved as album art: {qr_path}")

async def wait_for_tokens(device_id):
    """Connect to relay and wait for Spotify tokens with keepalive"""
    async with websockets.connect(RELAY_URL, ping_interval=20, ping_timeout=10) as websocket:
        # Register device
        await websocket.send(json.dumps({
            "type": "register",
            "device_id": device_id,
            "device_name": "LEELOO"
        }))
        
        # Consume registration response
        response = await websocket.recv()
        print(f"   Connected: {json.loads(response).get('type')}")
        
        # Wait for spotify_auth_complete message (up to 5 minutes)
        timeout = 300  # 5 minutes
        start_time = asyncio.get_event_loop().time()
        
        while True:
            try:
                # Wait for message with timeout
                remaining = timeout - (asyncio.get_event_loop().time() - start_time)
                if remaining <= 0:
                    raise TimeoutError("Authorization timeout (5 minutes)")
                
                message = await asyncio.wait_for(websocket.recv(), timeout=min(remaining, 30))
                data = json.loads(message)
                
                if data['type'] == 'spotify_auth_complete':
                    return data['tokens']
                elif data['type'] == 'pong':
                    # Keepalive, continue waiting
                    continue
                    
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send(json.dumps({"type": "ping"}))
                continue

def save_tokens(tokens):
    """Save Spotify tokens to config file"""
    with open('/home/pi/leeloo-ui/spotify_tokens.json', 'w') as f:
        json.dump(tokens, f, indent=2)

def clear_auth_display():
    """Clear the QR code from display"""
    try:
        os.remove('/home/pi/leeloo-ui/current_music.json')
    except FileNotFoundError:
        pass

async def main():
    print("\n" + "=" * 60)
    print("🎵  LEELOO Spotify Authorization")
    print("=" * 60 + "\n")
    
    device_id = f"leeloo_{uuid.uuid4().hex[:8]}"
    auth_url = generate_auth_url(device_id)
    
    print(f"📱 Device ID: {device_id}\n")
    
    print("📷 Generating QR code...")
    qr_image = create_album_art_qr(auth_url)
    save_as_album_art(qr_image)
    print("   ✅ QR code displayed in album art area!\n")
    
    print("🔌 Connecting to relay server...")
    print("⏳ Waiting for authorization (up to 5 minutes)...\n")
    print("   📱 Look at LEELOO screen (album art area)")
    print("   📸 Scan QR code with phone camera")
    print("   🔗 Tap notification to open Spotify")
    print("   ✅ Click 'Agree' to authorize\n")
    
    tokens = await wait_for_tokens(device_id)
    save_tokens(tokens)
    clear_auth_display()
    
    print("=" * 60)
    print("✅  Spotify Connected Successfully!")
    print("=" * 60)
    print("\nTokens saved to: /home/pi/leeloo-ui/spotify_tokens.json\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user\n")
        clear_auth_display()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        clear_auth_display()
        sys.exit(1)
