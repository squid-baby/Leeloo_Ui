#!/usr/bin/env python3
"""
LEELOO Intent Router — Claude 3.5 Haiku for voice command classification

Takes a transcript from VoiceManager, sends it to Claude with current device
context, and returns a structured Intent with action + parameters + display text.

Actions:
- WEATHER_EXPAND:   Show detailed weather info
- MESSAGE_SEND:     Send a message to the crew
- MESSAGE_READOUT:  Read recent messages ("what did I miss")
- ALBUM_INFO:       Tell me about the current artist/song
- SONG_PUSH:        Share a song with the crew
- HANG_PROPOSE:     Propose a hangout time
- HANG_CONFIRM:     Confirm a proposed hangout
- NUDGE:            Send a nudge/wink to the crew
- UNKNOWN:          Could not classify
"""

import json
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("[INTENT] anthropic not installed — intent routing disabled")


SYSTEM_PROMPT = """You are LEELOO's brain — a retro music-sharing gadget shared between close friends. Classify the user's voice command and extract parameters.

Current device state:
{context}

Respond with JSON only (no markdown, no code fences):
{{
  "action": "WEATHER_EXPAND|MESSAGE_SEND|MESSAGE_READOUT|ALBUM_INFO|SONG_PUSH|HANG_PROPOSE|HANG_CONFIRM|NUDGE|UNKNOWN",
  "params": {{ }},
  "display_text": "text to show on the retro LCD screen (keep under 200 chars)"
}}

Action details:
- WEATHER_EXPAND: User asks about weather. display_text = friendly weather summary using the data provided (temp, conditions, UV, rain chance). Include outfit/umbrella advice if relevant.
- MESSAGE_SEND: User wants to send a message to friends. Extract the actual message in params.message (just the content, not "tell them" etc).
- MESSAGE_READOUT: User asks "what did I miss" or "any messages" or "read messages". Set params.readout=true.
- ALBUM_INFO: User asks about current song/artist. display_text = 2-3 fun sentences about the artist/track using the music data provided. Be conversational and interesting.
- SONG_PUSH: User wants to share/send a specific song. Extract params.query (search terms for Spotify). If they say "send this song" with no specifics, set params.current=true.
- HANG_PROPOSE: User wants to schedule hanging out. Extract params.datetime (ISO format) and params.description. If vague like "this weekend", make reasonable assumptions.
- HANG_CONFIRM: User confirms a proposed hang. Set params.confirm=true.
- NUDGE: User wants to nudge/wink/poke friends. Set params.nudge=true.
- UNKNOWN: Can't determine intent. display_text = a brief confused but friendly response.

Keep display_text concise — this shows on a small 480x320 retro LCD. Use lowercase, casual tone. No emojis.
If the user just says something conversational like "hey" or "thanks", treat as UNKNOWN with a friendly response."""


@dataclass
class Intent:
    """Classified voice command intent"""
    action: str = "UNKNOWN"
    params: Dict[str, Any] = field(default_factory=dict)
    response_text: str = ""


class IntentRouter:
    """Routes voice transcripts to intents via Claude 3.5 Haiku"""

    def __init__(self, anthropic_api_key: str, get_context_fn: Optional[Callable] = None):
        """
        Args:
            anthropic_api_key: Anthropic API key
            get_context_fn: Callable that returns context string with current device state
        """
        self.api_key = anthropic_api_key
        self.get_context_fn = get_context_fn
        self.client = None

        if ANTHROPIC_AVAILABLE and anthropic_api_key:
            self.client = anthropic.Anthropic(api_key=anthropic_api_key)
            print("[INTENT] Claude Haiku intent router initialized")
        else:
            print("[INTENT] Intent router disabled (no API key or anthropic not installed)")

    async def route(self, transcript: str) -> Intent:
        """
        Classify a voice transcript into an Intent.

        Args:
            transcript: The user's spoken text

        Returns:
            Intent with action, params, and response_text
        """
        if not transcript or not transcript.strip():
            return Intent(action="UNKNOWN", response_text="didn't catch that")

        if not self.client:
            return Intent(action="UNKNOWN", response_text="brain offline")

        try:
            # Build context
            context = ""
            if self.get_context_fn:
                try:
                    context = self.get_context_fn()
                except Exception as e:
                    context = f"(context unavailable: {e})"

            # Format system prompt with context
            system = SYSTEM_PROMPT.format(context=context)

            # Call Claude Haiku (run sync client in executor to avoid blocking)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=300,
                    system=system,
                    messages=[
                        {"role": "user", "content": transcript}
                    ]
                )
            )

            # Parse response
            response_text = response.content[0].text.strip()
            print(f"[INTENT] Raw response: {response_text}")

            # Clean up potential markdown code fences
            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[-1]
            if response_text.endswith("```"):
                response_text = response_text.rsplit("```", 1)[0]
            response_text = response_text.strip()

            # Parse JSON
            data = json.loads(response_text)

            intent = Intent(
                action=data.get("action", "UNKNOWN"),
                params=data.get("params", {}),
                response_text=data.get("display_text", "")
            )

            print(f"[INTENT] Action: {intent.action}")
            print(f"[INTENT] Params: {intent.params}")
            print(f"[INTENT] Display: {intent.response_text[:80]}...")

            return intent

        except json.JSONDecodeError as e:
            print(f"[INTENT] JSON parse error: {e}")
            print(f"[INTENT] Raw: {response_text}")
            return Intent(action="UNKNOWN", response_text="brain glitch, try again")

        except Exception as e:
            print(f"[INTENT] Error: {e}")
            import traceback
            traceback.print_exc()
            return Intent(action="UNKNOWN", response_text="something went wrong")


def build_context(weather_data=None, music_data=None, contacts=None, messages=None):
    """
    Build context string for the intent router.

    This is called by LeelooBrain to provide current device state.
    """
    parts = []

    # Weather
    if weather_data:
        temp = weather_data.get('temp_f', '?')
        condition = weather_data.get('condition', 'unknown')
        uv = weather_data.get('uv_index', '?')
        rain = weather_data.get('precipitation_chance', '?')
        humidity = weather_data.get('humidity', '?')
        wind = weather_data.get('wind_speed', '?')
        high = weather_data.get('high_f', '?')
        low = weather_data.get('low_f', '?')
        parts.append(
            f"Weather: {temp}F, {condition}, UV {uv}, "
            f"Rain {rain}%, Humidity {humidity}%, Wind {wind}mph, "
            f"High {high}F / Low {low}F"
        )
    else:
        parts.append("Weather: not available")

    # Music
    if music_data:
        artist = music_data.get('artist', 'unknown')
        track = music_data.get('track', 'unknown')
        album = music_data.get('album', '')
        listeners = music_data.get('listeners', '')
        pushed_by = music_data.get('pushed_by', '')
        music_str = f"Music: {artist} - {track}"
        if album:
            music_str += f" ({album})"
        if listeners:
            music_str += f" [{listeners} monthly listeners]"
        if pushed_by:
            music_str += f" [shared by {pushed_by}]"
        parts.append(music_str)
    else:
        parts.append("Music: nothing playing")

    # Contacts
    if contacts:
        names = [c.get('name', '?') for c in contacts]
        parts.append(f"Crew members: {', '.join(names)}")

    # Recent messages
    if messages:
        recent = messages[-5:]  # Last 5
        msg_lines = []
        for m in recent:
            msg_lines.append(f"  {m.get('sender', '?')}: {m.get('text', '')}")
        parts.append("Recent messages:\n" + "\n".join(msg_lines))
    else:
        parts.append("Messages: none recent")

    return "\n".join(parts)


# =============================================================================
# Test
# =============================================================================

async def demo():
    """Demo — test intent routing with sample transcripts"""
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try loading from .env
        env_path = "/home/pi/leeloo-ui/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not api_key:
        print("Set ANTHROPIC_API_KEY env var or add to /home/pi/leeloo-ui/.env")
        return

    # Sample context
    def get_context():
        return build_context(
            weather_data={
                'temp_f': 72, 'condition': 'Partly cloudy',
                'uv_index': 6, 'precipitation_chance': 10,
                'humidity': 55, 'wind_speed': 8,
                'high_f': 78, 'low_f': 62
            },
            music_data={
                'artist': 'Cinnamon Chasers',
                'track': 'Luv Deluxe',
                'album': 'A Million Miles From Home',
                'listeners': '1.2M'
            },
            contacts=[
                {'name': 'Jen'},
                {'name': 'Marcus'},
                {'name': 'Dev'}
            ]
        )

    router = IntentRouter(api_key, get_context_fn=get_context)

    test_phrases = [
        "what's the weather like",
        "tell my friends I miss them",
        "what did I miss",
        "tell me about this artist",
        "send this song to the crew",
        "let's hang Saturday at 3pm",
        "yeah that works for me",
    ]

    print("\n--- Intent Router Demo ---\n")

    for phrase in test_phrases:
        print(f"\n{'='*50}")
        print(f"User: '{phrase}'")
        intent = await router.route(phrase)
        print(f"Action:  {intent.action}")
        print(f"Params:  {intent.params}")
        print(f"Display: {intent.response_text}")
        await asyncio.sleep(0.5)  # Rate limit


if __name__ == "__main__":
    asyncio.run(demo())
