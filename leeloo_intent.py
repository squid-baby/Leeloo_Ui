#!/usr/bin/env python3
"""
LEELOO Intent Router — Claude Haiku for voice command classification

Takes a transcript from VoiceManager, sends it to Claude with current device
context, and returns a structured Intent with action + parameters + display text.

Prompt layout (keeps the prompt cache valid):
- system:  STATIC instructions only (cached). Never put changing data here.
- user:    current device state + the voice command (changes every call).
"""

import os
import json
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, Callable

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("[INTENT] anthropic not installed — intent routing disabled")


# One place for the model name. All Claude calls on the device use it.
# Do not add a date suffix. Override with LEELOO_CLAUDE_MODEL in .env.
CLAUDE_MODEL = os.environ.get("LEELOO_CLAUDE_MODEL", "claude-haiku-5-5")

API_TIMEOUT_S = 15.0

ACTIONS = (
    "WEATHER_EXPAND", "MESSAGE_SEND", "MESSAGE_READOUT", "ALBUM_INFO",
    "SONG_PUSH", "HANG_PROPOSE", "HANG_CONFIRM", "NUDGE", "TELEGRAM_SETUP",
    "TIME_QUERY", "UNKNOWN",
)

# STATIC. No .format(), no dates, no device state. Any change to this text
# invalidates the prompt cache for every device.
SYSTEM_PROMPT = """You are LEELOO's brain — a retro music-sharing gadget shared between close friends. Each request gives you the current device state and one voice command. Classify the command and extract parameters.

Respond with one JSON object only (no markdown, no code fences, no other text):
{
  "action": "WEATHER_EXPAND|MESSAGE_SEND|MESSAGE_READOUT|ALBUM_INFO|SONG_PUSH|HANG_PROPOSE|HANG_CONFIRM|NUDGE|TELEGRAM_SETUP|TIME_QUERY|UNKNOWN",
  "params": { },
  "display_text": "text to show on the retro LCD screen (keep under 200 chars)"
}

Action details:
- WEATHER_EXPAND: User asks about weather. display_text = friendly weather summary that uses ONLY the weather data in the device state (temp, conditions, UV, rain). Include outfit/umbrella advice if relevant. If the device state says weather is not available, say that you can't reach the weather right now. Never invent numbers.
- MESSAGE_SEND: User wants to send a message to friends. Extract the actual message in params.message (just the content, not "tell them" etc).
- MESSAGE_READOUT: User asks "what did I miss" or "any messages" or "read messages". Set params.readout=true.
- ALBUM_INFO: User asks about current song/artist. display_text = 2-3 fun sentences about the artist/track named in the device state. Be conversational and interesting. If the device state says nothing is playing, say so.
- SONG_PUSH: User wants to share/send a specific song. Extract params.query (search terms for Spotify: artist and title). If they say "send this song" with no specifics, set params.current=true.
- HANG_PROPOSE: User wants to schedule hanging out. Extract params.datetime as ISO 8601 local time without a timezone (example: 2026-03-27T19:00:00). Resolve relative dates ("saturday", "tomorrow") from the "Now" line of the device state. The date must be in the future. Extract params.description. If vague like "this weekend", make reasonable assumptions. display_text = short confirmation of what was proposed, from the proposer's POV, e.g. "saturday 8pm – your place?" — NOT "let me know if that works".
- HANG_CONFIRM: User confirms/agrees to a hang ("I'm in", "yes", "sounds good", "confirmed", etc.). Set params.confirm=true. display_text = short enthusiastic confirmation, e.g. "you're in!" or "locked in."
- NUDGE: User wants to nudge/wink/poke friends. Set params.nudge=true.
- TELEGRAM_SETUP: User asks about Telegram setup, how to message from phone, or how to connect Telegram. Set params.telegram=true.
- TIME_QUERY: User asks about the time, date, or "what time is it". Set params.time=true. display_text is NOT used for this action (will be generated separately).
- UNKNOWN: Can't determine intent. display_text = a brief confused but friendly response that suggests one thing the user can say.

Keep display_text concise — this shows on a small 480x320 retro LCD. Use lowercase, casual tone. No emojis.
If the user just says something conversational like "hey" or "thanks", treat as UNKNOWN with a friendly response."""


@dataclass
class Intent:
    """Classified voice command intent"""
    action: str = "UNKNOWN"
    params: Dict[str, Any] = field(default_factory=dict)
    response_text: str = ""


def make_client(api_key: str):
    """Create the shared Anthropic client (short timeout: a person is waiting)."""
    return anthropic.Anthropic(api_key=api_key, timeout=API_TIMEOUT_S, max_retries=1)


def ask_claude(client, system: str, user: str, max_tokens: int = 400) -> str:
    """One blocking Claude call. Returns the reply text ('' on refusal).

    Run it with loop.run_in_executor() — never call it on the event loop.
    The system prompt is sent as a cached block; keep it static.
    """
    kwargs = dict(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user}],
    )
    try:
        # These are short classification/copy tasks: skip thinking for speed.
        response = client.messages.create(thinking={"type": "disabled"}, **kwargs)
    except TypeError:
        # Very old SDK without the `thinking` argument
        response = client.messages.create(**kwargs)

    if getattr(response, "stop_reason", None) == "refusal":
        print("[INTENT] Claude declined the request")
        return ""
    parts = [
        b.text for b in (response.content or [])
        if getattr(b, "type", None) == "text"
    ]
    return "".join(parts).strip()


def extract_json(text: str) -> Dict[str, Any]:
    """Parse the first JSON object in a model reply.

    Tolerates code fences and text around the object.
    Raises json.JSONDecodeError if there is no valid object.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise json.JSONDecodeError("JSON is not an object", text, 0)
    return data


def build_user_message(context: str, transcript: str) -> str:
    """The per-request (non-cached) part of the prompt."""
    return (
        f"<device_state>\n{context}\n</device_state>\n\n"
        f"<voice_command>\n{transcript}\n</voice_command>"
    )


def parse_intent(reply: str) -> Intent:
    """Turn a model reply into a validated Intent."""
    data = extract_json(reply)
    action = data.get("action", "UNKNOWN")
    if action not in ACTIONS:
        action = "UNKNOWN"
    params = data.get("params")
    if not isinstance(params, dict):
        params = {}
    display = data.get("display_text")
    if not isinstance(display, str):
        display = ""
    return Intent(action=action, params=params, response_text=display)


class IntentRouter:
    """Routes voice transcripts to intents via Claude Haiku"""

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
            self.client = make_client(anthropic_api_key)
            print(f"[INTENT] Intent router initialized ({CLAUDE_MODEL})")
        else:
            print("[INTENT] Intent router disabled (no API key or anthropic not installed)")

    async def route(self, transcript: str) -> Intent:
        """Classify a voice transcript into an Intent."""
        if not transcript or not transcript.strip():
            return Intent(action="UNKNOWN", response_text="didn't catch that")

        if not self.client:
            return Intent(action="UNKNOWN", response_text="brain offline")

        reply = ""
        try:
            context = ""
            if self.get_context_fn:
                try:
                    context = self.get_context_fn()
                except Exception as e:
                    print(f"[INTENT] Context error: {e}")
                    context = "(device state not available)"

            user = build_user_message(context, transcript)
            loop = asyncio.get_running_loop()
            reply = await loop.run_in_executor(
                None, ask_claude, self.client, SYSTEM_PROMPT, user, 400
            )
            print(f"[INTENT] Raw response: {reply}")

            intent = parse_intent(reply)
            print(f"[INTENT] Action: {intent.action}  Params: {intent.params}")
            return intent

        except json.JSONDecodeError as e:
            print(f"[INTENT] JSON parse error: {e} — raw: {reply!r}")
            return Intent(action="UNKNOWN", response_text="brain glitch, try again")

        except Exception as e:
            print(f"[INTENT] Error: {e}")
            import traceback
            traceback.print_exc()
            return Intent(action="UNKNOWN", response_text="can't reach my brain right now. check wifi and try again")


def _contact_name(contact) -> str:
    """Contacts are plain name strings; older configs used {'name': ...}."""
    if isinstance(contact, dict):
        return str(contact.get('name', '?'))
    return str(contact)


def build_context(weather_data=None, music_data=None, contacts=None, messages=None,
                  now: Optional[datetime] = None, pending_hang: Optional[str] = None):
    """
    Build the device-state text for the intent router.

    Args:
        weather_data: dict from gadget_weather.get_weather(), or None
        music_data: dict with artist/track/album..., or None
        contacts: list of crew member names (strings; dicts with 'name' tolerated)
        messages: recent messages, NEWEST FIRST (MessageManager.get_history_24h())
        now: current local time of the device (defaults to system time)
        pending_hang: ISO datetime of a hang proposal that waits for confirmation
    """
    parts = []

    # Current local date and time, so Claude can resolve "saturday", "tonight"...
    now = now or datetime.now()
    parts.append(f"Now: {now.strftime('%Y-%m-%d %A %H:%M')} (device local time)")

    # Weather
    if weather_data and 'temp_f' in weather_data:
        temp = weather_data.get('temp_f', '?')
        uv = weather_data.get('uv_raw', '?')
        rain_inches = weather_data.get('rain_24h_inches', 0) or 0
        weather_desc = weather_data.get('weather_desc', 'unknown')
        is_raining = weather_data.get('is_raining', False)
        current_precip = weather_data.get('current_precip_inches', 0) or 0

        weather_str = f"Weather: {temp}F, {weather_desc}, UV {uv}"
        if is_raining:
            weather_str += f", CURRENTLY RAINING ({current_precip:.2f} inches/hr)"
        if rain_inches > 0.01:
            weather_str += f", rain total today: {rain_inches:.2f} inches"
        nowcast = weather_data.get('precip_15min_mm') or []
        if nowcast:
            next_2h = sum(nowcast[:8])
            weather_str += f", rain forecast next 2 hours: {next_2h:.1f} mm"
        parts.append(weather_str)
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
        names = [_contact_name(c) for c in contacts]
        parts.append(f"Crew members: {', '.join(names)}")

    # Hang proposal waiting for an answer
    if pending_hang:
        parts.append(f"Pending hang proposal: {pending_hang}")

    # Recent messages (input is newest first; show oldest -> newest)
    if messages:
        recent = list(reversed(messages[:5]))
        msg_lines = [f"  {m.get('sender', '?')}: {m.get('text', '')}" for m in recent]
        parts.append("Recent messages:\n" + "\n".join(msg_lines))
    else:
        parts.append("Messages: none recent")

    return "\n".join(parts)


# =============================================================================
# Manual test (needs ANTHROPIC_API_KEY; makes real API calls)
# =============================================================================

async def demo():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Set ANTHROPIC_API_KEY to run the demo")
        return

    def get_context():
        return build_context(
            weather_data={'temp_f': 72, 'weather_desc': 'partly cloudy', 'uv_raw': 6},
            music_data={'artist': 'Cinnamon Chasers', 'track': 'Luv Deluxe',
                        'album': 'A Million Miles From Home', 'listeners': '1.2M'},
            contacts=['Jen', 'Marcus', 'Dev'],
        )

    router = IntentRouter(api_key, get_context_fn=get_context)
    for phrase in [
        "what's the weather like", "tell my friends I miss them", "what did I miss",
        "tell me about this artist", "send this song to the crew",
        "let's hang Saturday at 3pm", "yeah that works for me",
    ]:
        intent = await router.route(phrase)
        print(f"{phrase!r} -> {intent.action} {intent.params} | {intent.response_text}")


if __name__ == "__main__":
    asyncio.run(demo())
