#!/usr/bin/env python3
"""
LEELOO Device Client
Connects to relay server and handles crew messaging.
"""

import asyncio
import json
import time
import os
from typing import Optional, Callable
from dataclasses import dataclass

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'websockets'])
    import websockets


@dataclass
class CrewConfig:
    """Device's crew configuration"""
    device_id: str = ""
    crew_id: str = ""
    crew_code: str = ""
    display_name: str = "LEELOO"


class LeelooClient:
    """Client for connecting LEELOO device to relay server"""

    def __init__(self,
                 relay_url: str = "ws://localhost:8765",
                 config_path: str = "/home/pi/leeloo-ui/crew_config.json"):
        self.relay_url = relay_url
        self.config_path = config_path
        self.config = CrewConfig()
        self.websocket = None
        self.connected = False
        self.running = False

        # Callbacks for UI
        self.on_message: Optional[Callable] = None
        self.on_reaction: Optional[Callable] = None
        self.on_song_push: Optional[Callable] = None
        self.on_member_joined: Optional[Callable] = None
        self.on_member_offline: Optional[Callable] = None
        self.on_nudge: Optional[Callable] = None
        self.on_hang_propose: Optional[Callable] = None
        self.on_hang_confirm: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_crew_joined: Optional[Callable] = None

        self._load_config()

    def _load_config(self):
        """Load saved configuration (handles both old and new formats)"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    data = json.load(f)

                # Handle existing crew format: {name, invite_code, is_creator, members}
                if 'invite_code' in data:
                    self.config.crew_code = data.get('invite_code', '')
                    self.config.display_name = data.get('name', 'LEELOO')
                    self.config.device_id = data.get('device_id', '')
                    self.config.crew_id = data.get('crew_id', '')
                    print(f"[CLIENT] Loaded config: crew={self.config.crew_code} ({self.config.display_name})")
                elif 'crew_code' in data:
                    # New format
                    self.config = CrewConfig(**{k: v for k, v in data.items()
                                              if k in CrewConfig.__dataclass_fields__})
                    print(f"[CLIENT] Loaded config: crew={self.config.crew_code}")
                else:
                    print("[CLIENT] Config format not recognized")
        except Exception as e:
            print(f"[CLIENT] Could not load config: {e}")

    def _save_config(self):
        """Save configuration"""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump({
                    'device_id': self.config.device_id,
                    'crew_id': self.config.crew_id,
                    'crew_code': self.config.crew_code,
                    'display_name': self.config.display_name
                }, f)
            print(f"[CLIENT] Saved config")
        except Exception as e:
            print(f"[CLIENT] Could not save config: {e}")

    async def connect(self):
        """Connect to relay server"""
        print(f"[CLIENT] Connecting to {self.relay_url}...")

        try:
            self.websocket = await websockets.connect(self.relay_url)
            self.connected = True
            print("[CLIENT] Connected!")

            if self.on_connected:
                self.on_connected()

            return True
        except Exception as e:
            print(f"[CLIENT] Connection failed: {e}")
            return False

    async def create_crew(self, display_name: str = "LEELOO") -> Optional[str]:
        """Create a new crew and return the crew code.
        If we already have a crew_code (from portal setup), pass it to the server
        so it registers with the same code instead of generating a new one."""
        if not self.websocket:
            return None

        self.config.display_name = display_name

        msg = {
            'type': 'create_crew',
            'device_id': self.config.device_id or None,
            'display_name': display_name
        }
        # Pass existing crew code if we have one (from captive portal setup)
        if self.config.crew_code:
            msg['crew_code'] = self.config.crew_code

        await self.websocket.send(json.dumps(msg))

        # Wait for response
        response = await self.websocket.recv()
        data = json.loads(response)

        if data.get('type') == 'crew_created':
            self.config.device_id = data['device_id']
            self.config.crew_id = data.get('crew_id', data.get('crew_code', ''))
            self.config.crew_code = data['crew_code']
            self._save_config()

            print(f"[CLIENT] Created crew: {self.config.crew_code}")

            if self.on_crew_joined:
                self.on_crew_joined(self.config.crew_code)

            return self.config.crew_code

        return None

    async def join_crew(self, crew_code: str, display_name: str = "LEELOO") -> bool:
        """Join an existing crew by code"""
        if not self.websocket:
            return False

        self.config.display_name = display_name

        await self.websocket.send(json.dumps({
            'type': 'join_crew',
            'crew_code': crew_code,
            'device_id': self.config.device_id or None,
            'display_name': display_name
        }))

        # Wait for response
        response = await self.websocket.recv()
        data = json.loads(response)

        if data.get('type') == 'crew_joined':
            self.config.device_id = data.get('device_id', self.config.device_id)
            self.config.crew_id = data.get('crew_id', data.get('crew_code', self.config.crew_id))
            self.config.crew_code = data.get('crew_code', self.config.crew_code)
            self._save_config()

            print(f"[CLIENT] Joined crew: {self.config.crew_code} ({data.get('crew_members', 1)} members)")

            if self.on_crew_joined:
                self.on_crew_joined(self.config.crew_code)

            return True

        elif data.get('type') == 'error':
            print(f"[CLIENT] Error joining crew: {data.get('message')}")
            return False

        return False

    async def rejoin_crew(self) -> bool:
        """Rejoin previously saved crew"""
        if not self.config.crew_code:
            print("[CLIENT] No saved crew to rejoin")
            return False

        return await self.join_crew(
            self.config.crew_code,
            self.config.display_name
        )

    async def send_message(self, text: str):
        """Send a text message to crew"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'text',
            'payload': {
                'text': text
            }
        }))
        return True

    async def send_reaction(self, reaction_type: str, to_device: str = None):
        """Send a reaction (love, fire) to crew"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'reaction',
            'payload': {
                'reaction': reaction_type,  # 'love' or 'fire'
                'to_device': to_device
            }
        }))
        return True

    async def push_song(self, spotify_uri: str, artist: str = "", track: str = "",
                        album: str = "", album_art_url: str = "", note: str = ""):
        """Push a song to crew"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'song_push',
            'payload': {
                'artist': artist,
                'track': track,
                'album': album,
                'spotify_uri': spotify_uri,
                'album_art_url': album_art_url,
                'note': note
            }
        }))
        return True

    async def send_nudge(self):
        """Send a nudge/wink to crew"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'nudge',
            'payload': {}
        }))
        return True

    async def send_hang_propose(self, datetime_str: str, description: str = ""):
        """Propose a hangout time to crew"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'hang_propose',
            'payload': {
                'datetime': datetime_str,
                'description': description
            }
        }))
        return True

    async def send_hang_confirm(self):
        """Confirm a proposed hangout"""
        if not self.websocket or not self.config.crew_id:
            return False

        await self.websocket.send(json.dumps({
            'type': 'message',
            'msg_type': 'hang_confirm',
            'payload': {}
        }))
        return True

    async def listen(self):
        """Listen for incoming messages"""
        if not self.websocket:
            return

        self.running = True

        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._handle_message(data)

        except websockets.exceptions.ConnectionClosed:
            print("[CLIENT] Connection closed")
            self.connected = False
            if self.on_disconnected:
                self.on_disconnected()

        except Exception as e:
            print(f"[CLIENT] Error: {e}")
            self.connected = False

        self.running = False

    async def _handle_message(self, data: dict):
        """Handle incoming message from relay"""
        msg_type = data.get('type')

        if msg_type == 'message':
            payload = data.get('payload', {})
            inner_type = data.get('msg_type', 'text')
            from_name = data.get('from_name', 'Someone')

            if inner_type == 'text':
                print(f"[MSG] {from_name}: {payload.get('text', '')}")
                if self.on_message:
                    self.on_message(from_name, payload.get('text', ''))

            elif inner_type == 'reaction':
                reaction = payload.get('reaction', 'love')
                print(f"[REACTION] {from_name} sent {reaction}!")
                if self.on_reaction:
                    self.on_reaction(from_name, reaction)

            elif inner_type == 'song_push':
                print(f"[SONG] {from_name} pushed: {payload.get('artist')} - {payload.get('track')}")
                if self.on_song_push:
                    self.on_song_push(from_name, payload)

            elif inner_type == 'nudge':
                print(f"[NUDGE] {from_name} sent a nudge!")
                if self.on_nudge:
                    self.on_nudge(from_name)

            elif inner_type == 'hang_propose':
                print(f"[HANG] {from_name} proposed: {payload.get('datetime')}")
                if self.on_hang_propose:
                    self.on_hang_propose(from_name, payload.get('datetime', ''), payload.get('description', ''))

            elif inner_type == 'hang_confirm':
                print(f"[HANG] {from_name} confirmed!")
                if self.on_hang_confirm:
                    self.on_hang_confirm(from_name)

        elif msg_type == 'member_joined':
            name = data.get('display_name', 'Someone')
            print(f"[CREW] {name} joined!")
            if self.on_member_joined:
                self.on_member_joined(name)

        elif msg_type == 'member_offline':
            name = data.get('display_name', 'Someone')
            print(f"[CREW] {name} went offline")
            if self.on_member_offline:
                self.on_member_offline(name)

        elif msg_type == 'pong':
            pass  # Keepalive response

    async def keepalive(self, interval: int = 30):
        """Send periodic pings to keep connection alive"""
        while self.running and self.websocket:
            try:
                await self.websocket.send(json.dumps({'type': 'ping'}))
                await asyncio.sleep(interval)
            except:
                break

    async def disconnect(self):
        """Disconnect from relay"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        self.connected = False
        print("[CLIENT] Disconnected")

    def is_configured(self) -> bool:
        """Check if device has a crew configured"""
        return bool(self.config.crew_code)


# Demo usage
async def demo():
    """Demo the client"""
    client = LeelooClient(relay_url="ws://localhost:8765")

    # Set up callbacks
    client.on_message = lambda name, text: print(f">>> {name}: {text}")
    client.on_reaction = lambda name, reaction: print(f">>> {name} reacted with {reaction}!")
    client.on_song_push = lambda name, song: print(f">>> {name} pushed: {song['artist']} - {song['track']}")

    # Connect
    if not await client.connect():
        print("Failed to connect!")
        return

    # Create or join crew
    if client.is_configured():
        print(f"Rejoining crew: {client.config.crew_code}")
        await client.rejoin_crew()
    else:
        print("Creating new crew...")
        crew_code = await client.create_crew("Nathan's LEELOO")
        print(f"Created crew: {crew_code}")

    # Start listening and keepalive
    listen_task = asyncio.create_task(client.listen())
    keepalive_task = asyncio.create_task(client.keepalive())

    # Send a test message after 2 seconds
    await asyncio.sleep(2)
    await client.send_message("Hello from my LEELOO!")

    # Run until interrupted
    try:
        await listen_task
    except KeyboardInterrupt:
        print("\nShutting down...")
        await client.disconnect()


if __name__ == '__main__':
    asyncio.run(demo())
