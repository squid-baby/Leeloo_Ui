"""Intent router: prompt layout (cache-safe), parsing, context."""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import leeloo_intent as li


class FakeMessages:
    def __init__(self, text="", stop_reason="end_turn", reject_thinking=False, error=None):
        self.text, self.stop_reason = text, stop_reason
        self.reject_thinking, self.error = reject_thinking, error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_thinking and 'thinking' in kwargs:
            raise TypeError("unexpected keyword argument 'thinking'")
        if self.error:
            raise self.error
        blocks = [SimpleNamespace(type="thinking", thinking=""),
                  SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=blocks, stop_reason=self.stop_reason)


def router_with(messages, context="Weather: 70F"):
    r = li.IntentRouter("", get_context_fn=lambda: context)
    r.client = SimpleNamespace(messages=messages)
    return r


def test_model_is_current_and_has_no_date_suffix():
    assert li.CLAUDE_MODEL == "claude-haiku-5-5"
    assert "claude-3" not in open(li.__file__).read()


def test_system_prompt_is_static_and_context_goes_in_user_message():
    fake = FakeMessages(json.dumps({"action": "NUDGE", "params": {}, "display_text": ""}))
    r = router_with(fake, context="Weather: 70F SECRET-STATE")
    asyncio.run(r.route("poke them"))
    asyncio.run(r.route("poke them again"))
    a, b = fake.calls
    assert a['system'] == b['system']                       # byte-identical -> cacheable
    assert a['system'][0]['cache_control'] == {"type": "ephemeral"}
    assert "SECRET-STATE" not in a['system'][0]['text']
    assert "{context}" not in li.SYSTEM_PROMPT
    user = a['messages'][0]['content']
    assert "SECRET-STATE" in user and "poke them" in user
    assert a['model'] == li.CLAUDE_MODEL
    assert 'temperature' not in a and 'top_p' not in a


def test_route_parses_all_fields():
    fake = FakeMessages('```json\n{"action":"MESSAGE_SEND","params":{"message":"hi"},"display_text":"sent"}\n```')
    intent = asyncio.run(router_with(fake).route("tell them hi"))
    assert (intent.action, intent.params, intent.response_text) == ("MESSAGE_SEND", {"message": "hi"}, "sent")


@pytest.mark.parametrize("reply", ["", "sorry, no json", '["a"]', '{"action": 5}',
                                   '{"action":"DROP_TABLES","params":"x","display_text":7}'])
def test_bad_replies_become_unknown(reply):
    intent = asyncio.run(router_with(FakeMessages(reply)).route("x"))
    assert intent.action == "UNKNOWN" and isinstance(intent.params, dict)
    assert isinstance(intent.response_text, str)


def test_api_error_and_refusal_do_not_raise():
    intent = asyncio.run(router_with(FakeMessages(error=RuntimeError("down"))).route("x"))
    assert intent.action == "UNKNOWN" and intent.response_text
    intent = asyncio.run(router_with(FakeMessages("x", stop_reason="refusal")).route("x"))
    assert intent.action == "UNKNOWN"


def test_old_sdk_without_thinking_argument_still_works():
    fake = FakeMessages('{"action":"NUDGE"}', reject_thinking=True)
    assert asyncio.run(router_with(fake).route("x")).action == "NUDGE"
    assert len(fake.calls) == 2


def test_empty_transcript_and_missing_client():
    assert asyncio.run(router_with(FakeMessages()).route("  ")).action == "UNKNOWN"
    assert asyncio.run(li.IntentRouter("").route("hi")).response_text == "brain offline"


def test_context_error_does_not_stop_routing():
    fake = FakeMessages('{"action":"NUDGE"}')
    r = li.IntentRouter("", get_context_fn=lambda: 1 / 0)
    r.client = SimpleNamespace(messages=fake)
    assert asyncio.run(r.route("x")).action == "NUDGE"


def test_build_context_accepts_string_and_dict_contacts():
    text = li.build_context(contacts=["Amy", {"name": "Ben"}])
    assert "Crew members: Amy, Ben" in text


def test_build_context_content():
    msgs = [{"sender": "S", "text": f"m{i}"} for i in range(8)]     # newest first
    text = li.build_context(
        weather_data={'temp_f': 61, 'weather_desc': 'rain', 'uv_raw': 1, 'is_raining': True,
                      'current_precip_inches': 0.1, 'rain_24h_inches': 0.4,
                      'precip_15min_mm': [0.5] * 24},
        music_data={'artist': 'A', 'track': 'T', 'album': 'L', 'listeners': '1M', 'pushed_by': 'Amy'},
        messages=msgs, now=datetime(2026, 3, 27, 19, 5), pending_hang="2026-03-28T20:00:00")
    assert "Now: 2026-03-27 Friday 19:05" in text
    assert "61F" in text and "CURRENTLY RAINING" in text and "next 2 hours: 4.0 mm" in text
    assert "A - T (L)" in text and "shared by Amy" in text
    assert "Pending hang proposal: 2026-03-28T20:00:00" in text
    assert "m0" in text and "m4" in text and "m5" not in text       # 5 newest only
    assert text.index("m4") < text.index("m0")                      # oldest -> newest
    empty = li.build_context()
    assert "Weather: not available" in empty and "Music: nothing playing" in empty
    assert "Weather: not available" in li.build_context(weather_data={'error': True})


def test_extract_json():
    assert li.extract_json('noise {"a": {"b": 1}} trailing') == {"a": {"b": 1}}
    with pytest.raises(json.JSONDecodeError):
        li.extract_json("nothing here")
