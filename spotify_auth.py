#!/usr/bin/env python3
"""
Spotify OAuth flow for LEELOO
Generates auth URL and waits for tokens from relay server
"""
import asyncio
import websockets
import json
import sys
import uuid
import qrcode
from io import BytesIO

RELAY_URL = "wss://leeloobot.xyz/ws"
SPOTIFY_CLIENT_ID = "f8c3c0120e694af283d7d7f7c2f67d4c"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"

# Scopes needed for currently playing
SCOPES = "user-read-currently-playing user-read-playback-state"

def generate_auth_url(device_id):
    """Generate Spotify OAuth URL"""
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "https://leeloobot.xyz/spotify/callback",
        "scope": SCOPES,
        "state": device_id,  # Device ID passed back in callback
        "show_dialog": "true"
    }
    
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{SPOTIFY_AUTH_URL}?{query_string}"

def generate_qr_code(url):
    """Generate QR code for URL (for display on screen)"""
    qr = qrcode.QRCode(version=1, box_size=3, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    
    # Print ASCII QR code to terminal
    qr.print_ascii(invert=True)

async def wait_for_tokens(device_id):
    """Connect to relay and wait for Spotify tokens"""
    print(f"\n🔌 Connecting to relay server...")
    
    async with websockets.connect(RELAY_URL) as websocket:
        # Register device
        await websocket.send(json.dumps({
            "type": "register",
            "device_id": device_id,
            "device_name": "LEELOO"
        }))
        
        response = await websocket.recv()
        data = json.loads(response)
        print(f"✅ Connected: {data}")
        
        print("\n⏳ Waiting for you to authorize Spotify...")
        print("   (This will happen automatically when you approve on your phone)")
        
        # Wait for spotify_auth_complete message
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            
            if data['type'] == 'spotify_auth_complete':
                print("\n✅ Authorization complete!")
                return data['tokens']
            elif data['type'] == 'pong':
                # Ignore pings
                continue

def save_tokens(tokens):
    """Save Spotify tokens to config file"""
    with open('/home/pi/leeloo-ui/spotify_tokens.json', 'w') as f:
        json.dump(tokens, f, indent=2)
    print(f"\n💾 Tokens saved to: /home/pi/leeloo-ui/spotify_tokens.json")

async def main():
    # Generate unique device ID
    device_id = f"leeloo_{uuid.uuid4().hex[:8]}"
    
    # Generate auth URL
    auth_url = generate_auth_url(device_id)
    
    print("=" * 60)
    print("🎵 LEELOO Spotify Authorization")
    print("=" * 60)
    print("\n📱 Open this URL on your phone:\n")
    print(f"   {auth_url}\n")
    print("-" * 60)
    print("\n📷 Or scan this QR code:\n")
    
    # Generate QR code
    generate_qr_code(auth_url)
    
    print("\n" + "-" * 60)
    
    # Wait for tokens from relay
    tokens = await wait_for_tokens(device_id)
    
    # Save tokens
    save_tokens(tokens)
    
    print("\n✅ All done! LEELOO can now access your Spotify.")
    print("   Run: python3 spotify_currently_playing.py")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
        sys.exit(1)
