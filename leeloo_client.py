#!/usr/bin/env python3
"""
LEELOO Device Client
Connects to relay server and handles crew messaging.
"""

import asyncio
import json
import time
import os
import traceback
from typing import Optional, Callable
from dataclasses import dataclass

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None
    print("[CLIENT] 'websockets' module not installed - crew relay disabled")

    class ConnectionClosed(Exception):
        """Placeholder so except clauses work without websockets"""


def _atomic_write_json(path: str, obj, indent=None):
    """Write JSON to a temp file next to `path`, then os.replace() it in"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(obj, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


@dataclass
class CrewConfig:
    """Device's crew configuration"""
    device_id: str = ""
    crew_id: str = ""
    crew_code: str = ""
    display_name: str = "LEELOO"


class LeelooClient:
    """Client for connecting LEELOO device to relay server"""

    CONNECT_TIMEOUT = 10.0   # seconds to open the socket
    REPLY_TIMEOUT = 10.0     # seconds to wait for crew_joined / crew_created
    CLOSE_TIMEOUT = 3.0      # seconds to wait for a clean close

    def __init__(self,
                 relay_url: str = "ws://localhost:8765",
                 config_path: str = "/home/pi/leeloo-ui/crew_config.json"):
        self.relay_url = relay_url
        self.config_path = config_path
        self.config = CrewConfig()
        self.websocket = None
        self.connected = False
        self.running = False

        # Callbacks for UI (all synchronous, called on the event loop)
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
        self.on_spotify_auth: Optional[Callable] = None
        # Full crew roster (list of names) on crew_joined
        self.on_roster: Optional[Callable[[list], None]] = None
        # Message stored while we were offline:
        # (from_name, msg_type, payload, timestamp_seconds)
        self.on_queued_message: Optional[Callable[[str, str, dict, float], None]] = None

        self._load_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self):
        """Load saved configuration (handles both old and new formats)"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    print("[CLIENT] Config format not recognized")
                # Handle existing crew format: {name, invite_code, is_creator, members}
                elif 'invite_code' in data:
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
        """Save configuration, keeping keys written by others (setup portal)"""
        try:
            existing = {}
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                print(f"[CLIENT] Existing config unreadable, rewriting: {e}")

            existing.update({
                'device_id': self.config.device_id,
                'crew_id': self.config.crew_id,
                'crew_code': self.config.crew_code,
                'display_name': self.config.display_name
            })
            _atomic_write_json(self.config_path, existing)
            print(f"[CLIENT] Saved config")
        except Exception as e:
            print(f"[CLIENT] Could not save config: {e}")

    # ------------------------------------------------------------------
    # Connection state
    # ------------------------------------------------------------------

    def _call(self, callback, *args):
        """Run a UI callback; an exception in it never reaches the socket code"""
        if not callback:
            return
        try:
            callback(*args)
        except Exception:
            print(f"[CLIENT] Callback {getattr(callback, '__name__', callback)} raised:")
            traceback.print_exc()

    def _mark_disconnected(self):
        """Flip to disconnected; fires on_disconnected once per disconnect"""
        was_connected = self.connected
        self.connected = False
        self.running = False
        if was_connected:
            self._call(self.on_disconnected)

    async def _close_socket(self, ws):
        """Best-effort close that never raises and never hangs"""
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.close(), self.CLOSE_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionClosed, OSError, RuntimeError) as e:
            print(f"[CLIENT] Close error: {e!r}")

    async def connect(self, timeout: Optional[float] = None) -> bool:
        """Connect to relay server (closes any previous socket first)"""
        if websockets is None:
            print("[CLIENT] Cannot connect: websockets module missing")
            return False

        old = self.websocket
        self.websocket = None
        if old is not None:
            self._mark_disconnected()
            await self._close_socket(old)

        if timeout is None:
            timeout = self.CONNECT_TIMEOUT
        print(f"[CLIENT] Connecting to {self.relay_url}...")

        try:
            # Library ping/pong stays at its defaults (enabled)
            self.websocket = await asyncio.wait_for(
                websockets.connect(self.relay_url), timeout)
        except asyncio.TimeoutError:
            print(f"[CLIENT] Connection failed: timeout after {timeout}s")
            return False
        except Exception as e:
            print(f"[CLIENT] Connection failed: {e}")
            return False

        self.connected = True
        print("[CLIENT] Connected!")
        self._call(self.on_connected)
        return True

    async def _send(self, obj: dict) -> bool:
        """Send one JSON frame. True only if handed to an open socket.
        Never raises; on failure the client is marked disconnected."""
        ws = self.websocket
        if ws is None or not self.connected:
            return False
        try:
            await ws.send(json.dumps(obj))
            return True
        except ConnectionClosed:
            print("[CLIENT] Send failed: connection closed")
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"[CLIENT] Send failed: {e!r}")
            await self._close_socket(ws)  # so listen() ends too
        if self.websocket is ws:
            self._mark_disconnected()
        return False

    async def _wait_for_reply(self, expected: tuple, timeout: float) -> Optional[dict]:
        """Wait for a message whose type is in `expected`. Anything else that
        arrives meanwhile is dispatched normally. None on timeout/close."""
        ws = self.websocket
        if ws is None:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                print(f"[CLIENT] No {expected[0]} reply within {timeout}s")
                return None
            try:
                raw = await asyncio.wait_for(ws.recv(), remaining)
            except asyncio.TimeoutError:
                print(f"[CLIENT] No {expected[0]} reply within {timeout}s")
                return None
            except ConnectionClosed:
                print("[CLIENT] Connection closed while waiting for reply")
                if self.websocket is ws:
                    self._mark_disconnected()
                return None
            except (OSError, RuntimeError) as e:
                print(f"[CLIENT] Receive failed: {e!r}")
                return None

            data = self._parse(raw)
            if data is None:
                continue
            if data.get('type') in expected:
                return data
            await self._dispatch(data)

    # ------------------------------------------------------------------
    # Crew
    # ------------------------------------------------------------------

    async def create_crew(self, display_name: str = "LEELOO",
                          timeout: Optional[float] = None) -> Optional[str]:
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

        if not await self._send(msg):
            return None

        data = await self._wait_for_reply(
            ('crew_created', 'error'),
            self.REPLY_TIMEOUT if timeout is None else timeout)
        if data is None:
            return None

        if data.get('type') == 'crew_created':
            crew_code = data.get('crew_code') or self.config.crew_code
            if not crew_code:
                print("[CLIENT] crew_created reply had no crew_code")
                return None
            self.config.device_id = data.get('device_id') or self.config.device_id
            self.config.crew_id = data.get('crew_id') or crew_code
            self.config.crew_code = crew_code
            self._save_config()

            print(f"[CLIENT] Created crew: {self.config.crew_code}")
            self._call(self.on_crew_joined, self.config.crew_code)
            return self.config.crew_code

        print(f"[CLIENT] Error creating crew: {data.get('message')}")
        return None

    async def join_crew(self, crew_code: str, display_name: str = "LEELOO",
                        timeout: Optional[float] = None) -> bool:
        """Join an existing crew by code"""
        if not self.websocket:
            return False

        self.config.display_name = display_name

        if not await self._send({
            'type': 'join_crew',
            'crew_code': crew_code,
            'device_id': self.config.device_id or None,
            'display_name': display_name
        }):
            return False

        data = await self._wait_for_reply(
            ('crew_joined', 'error'),
            self.REPLY_TIMEOUT if timeout is None else timeout)
        if data is None:
            return False

        if data.get('type') == 'crew_joined':
            self.config.device_id = data.get('device_id') or self.config.device_id
            self.config.crew_code = data.get('crew_code') or crew_code
            self.config.crew_id = data.get('crew_id') or self.config.crew_code
            self._save_config()

            print(f"[CLIENT] Joined crew: {self.config.crew_code} ({data.get('crew_members', 1)} members)")

            member_names = data.get('member_names')
            if not isinstance(member_names, list):
                member_names = []
            # Newer servers send the full roster (offline devices + Telegram too)
            roster = data.get('all_member_names')
            if not isinstance(roster, list):
                roster = member_names
            self._call(self.on_roster, list(roster))

            # Populate contacts with members already online
            for name in member_names:
                self._call(self.on_member_joined, name)

            self._call(self.on_crew_joined, self.config.crew_code)
            return True

        print(f"[CLIENT] Error joining crew: {data.get('message')}")
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

    # ------------------------------------------------------------------
    # Sending (all return True only if the frame went to an open socket)
    # ------------------------------------------------------------------

    async def _send_crew(self, msg_type: str, payload: dict) -> bool:
        if not self.config.crew_id:
            return False
        return await self._send({
            'type': 'message',
            'msg_type': msg_type,
            'payload': payload
        })

    async def send_message(self, text: str) -> bool:
        """Send a text message to crew"""
        return await self._send_crew('text', {'text': text})

    async def send_reaction(self, reaction_type: str, to_device: str = None) -> bool:
        """Send a reaction (love, fire) to crew"""
        return await self._send_crew('reaction', {
            'reaction': reaction_type,  # 'love' or 'fire'
            'to_device': to_device
        })

    async def push_song(self, spotify_uri: str, artist: str = "", track: str = "",
                        album: str = "", album_art_url: str = "", note: str = "") -> bool:
        """Push a song to crew"""
        return await self._send_crew('song_push', {
            'artist': artist,
            'track': track,
            'album': album,
            'spotify_uri': spotify_uri,
            'album_art_url': album_art_url,
            'note': note
        })

    async def send_nudge(self) -> bool:
        """Send a nudge/wink to crew"""
        return await self._send_crew('nudge', {})

    async def send_hang_propose(self, datetime_str: str, description: str = "") -> bool:
        """Propose a hangout time to crew"""
        return await self._send_crew('hang_propose', {
            'datetime': datetime_str,
            'description': description
        })

    async def send_hang_confirm(self) -> bool:
        """Confirm a proposed hangout"""
        return await self._send_crew('hang_confirm', {})

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw) -> Optional[dict]:
        """Decode one frame; None if it is not a JSON object"""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as e:
            print(f"[CLIENT] Skipping malformed message: {e}")
            return None
        if not isinstance(data, dict):
            print("[CLIENT] Skipping non-object message")
            return None
        return data

    async def _dispatch(self, data: dict):
        """_handle_message, but a failure in it never propagates"""
        try:
            await self._handle_message(data)
        except Exception:
            print(f"[CLIENT] Error handling {data.get('type')!r} message:")
            traceback.print_exc()

    async def listen(self):
        """Listen for incoming messages until the socket closes"""
        ws = self.websocket
        if not ws:
            return

        self.running = True

        try:
            async for message in ws:
                data = self._parse(message)
                if data is not None:
                    await self._dispatch(data)
            print("[CLIENT] Connection closed")

        except ConnectionClosed:
            print("[CLIENT] Connection closed")

        except Exception as e:
            print(f"[CLIENT] Error: {e!r}")

        finally:
            # Any way out of the loop (close, error, cancel) = disconnected,
            # unless connect() already replaced this socket with a new one.
            if self.websocket is ws:
                self._mark_disconnected()

    async def _handle_message(self, data: dict):
        """Handle incoming message from relay"""
        msg_type = data.get('type')

        if msg_type == 'message':
            payload = data.get('payload')
            if not isinstance(payload, dict):
                payload = {}
            inner_type = data.get('msg_type') or 'text'
            from_name = data.get('from_name') or 'Someone'

            # Stored while we were offline -> history, not a live event
            if data.get('queued') and self.on_queued_message:
                try:
                    ts = float(data.get('timestamp')) / 1000.0
                except (TypeError, ValueError):
                    ts = time.time()
                print(f"[MSG] (queued) {from_name}: {inner_type}")
                self._call(self.on_queued_message, from_name, inner_type, payload, ts)
                return

            if inner_type == 'text':
                print(f"[MSG] {from_name}: {payload.get('text', '')}")
                self._call(self.on_message, from_name, payload.get('text', ''))

            elif inner_type == 'reaction':
                reaction = payload.get('reaction', 'love')
                print(f"[REACTION] {from_name} sent {reaction}!")
                self._call(self.on_reaction, from_name, reaction)

            elif inner_type == 'song_push':
                print(f"[SONG] {from_name} pushed: {payload.get('artist')} - {payload.get('track')}")
                self._call(self.on_song_push, from_name, payload)

            elif inner_type == 'nudge':
                print(f"[NUDGE] {from_name} sent a nudge!")
                self._call(self.on_nudge, from_name)

            elif inner_type == 'hang_propose':
                print(f"[HANG] {from_name} proposed: {payload.get('datetime')}")
                self._call(self.on_hang_propose, from_name,
                           payload.get('datetime', ''), payload.get('description', ''))

            elif inner_type == 'hang_confirm':
                print(f"[HANG] {from_name} confirmed!")
                self._call(self.on_hang_confirm, from_name)

        elif msg_type == 'member_joined':
            name = data.get('display_name', 'Someone')
            print(f"[CREW] {name} joined!")
            self._call(self.on_member_joined, name)

        elif msg_type == 'member_offline':
            name = data.get('display_name', 'Someone')
            print(f"[CREW] {name} went offline")
            self._call(self.on_member_offline, name)

        elif msg_type == 'spotify_auth_complete':
            # Spotify OAuth tokens received from relay server
            tokens = data.get('tokens')
            if not isinstance(tokens, dict):
                tokens = {}
            print(f"[CLIENT] Spotify auth complete! access_token={'yes' if tokens.get('access_token') else 'no'}")
            if tokens.get('access_token'):
                # Save tokens to disk
                tokens_path = os.path.join(
                    os.environ.get("LEELOO_HOME", "/home/pi/leeloo-ui"),
                    "spotify_tokens.json"
                )
                try:
                    _atomic_write_json(tokens_path, tokens, indent=2)
                    print(f"[CLIENT] Spotify tokens saved to {tokens_path}")
                except Exception as e:
                    print(f"[CLIENT] Error saving Spotify tokens: {e}")

                self._call(self.on_spotify_auth, tokens)

        elif msg_type == 'error':
            print(f"[CLIENT] Server error: {data.get('message')}")

        elif msg_type == 'pong':
            pass  # Keepalive response

    async def keepalive(self, interval: float = 30):
        """Send periodic pings to keep connection alive; ends on disconnect"""
        while self.connected and self.websocket:
            if not await self._send({'type': 'ping'}):
                break
            await asyncio.sleep(interval)

    async def disconnect(self):
        """Disconnect from relay"""
        ws = self.websocket
        self.websocket = None
        self._mark_disconnected()
        await self._close_socket(ws)
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
    client.on_song_push = lambda name, song: print(f">>> {name} pushed: {song.get('artist')} - {song.get('track')}")

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
