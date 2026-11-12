"""Tests for leeloo_server/telegram_bot.py (no network, no Telegram).

The relay HTTP API and the Telegram Update/Message objects are replaced by fakes.
"""
import asyncio
import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
pytest.importorskip("aiohttp")

from telegram.error import BadRequest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(ROOT, "leeloo_server", "telegram_bot.py")

MD_SPECIALS = set("_*`[")


def _load_bot():
    spec = importlib.util.spec_from_file_location("leeloo_telegram_bot", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bot(tmp_path, monkeypatch):
    module = _load_bot()
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "bot_state.json"))
    module.user_state.clear()
    return module


# --- Fakes ---

def _markdown_is_valid(text):
    """Rough model of Telegram's legacy Markdown parser: every unescaped
    entity character must be paired, and '[' must not appear unescaped."""
    counts = {"_": 0, "*": 0, "`": 0}
    i = 0
    in_code = False
    while i < len(text):
        ch = text[i]
        if ch == "\\" and not in_code and i + 1 < len(text) and text[i + 1] in MD_SPECIALS:
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            counts["`"] += 1
        elif not in_code:
            if ch == "[":
                return False
            if ch in counts:
                counts[ch] += 1
        i += 1
    return all(n % 2 == 0 for n in counts.values())


class FakeMessage:
    """Records replies; rejects invalid Markdown the way Telegram does."""

    def __init__(self, text="", strict_markdown=True, always_fail=False):
        self.text = text
        self.replies = []   # (text, kwargs) that were accepted
        self.attempts = []  # every attempt, accepted or not
        self.strict_markdown = strict_markdown
        self.always_fail = always_fail

    async def reply_text(self, text, **kwargs):
        self.attempts.append((text, kwargs))
        if self.always_fail:
            raise BadRequest("Can't parse entities")
        if self.strict_markdown and kwargs.get("parse_mode") and not _markdown_is_valid(text):
            raise BadRequest("Can't parse entities: can't find end of the entity")
        self.replies.append((text, kwargs))

    @property
    def last(self):
        return self.replies[-1][0]


def make_update(text="", user_id=42, first_name="Ana", **msg_kwargs):
    message = FakeMessage(text, **msg_kwargs)
    user = SimpleNamespace(id=user_id, first_name=first_name)
    return SimpleNamespace(effective_user=user, message=message, callback_query=None)


def make_context(args=None):
    return SimpleNamespace(args=args or [], user_data={})


class FakeRelay:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, path, body=None):
        self.calls.append((path, body))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def use_relay(bot, monkeypatch, response):
    relay = FakeRelay(response)
    monkeypatch.setattr(bot, "relay_post", relay)
    monkeypatch.setattr(bot, "relay_get", relay)
    return relay


# --- Persistence ---

def test_state_persists_across_restart(bot, monkeypatch):
    use_relay(bot, monkeypatch, {"success": True, "crew_code": "LEELOO-AB23", "devices_online": 1})
    update = make_update()
    asyncio.run(bot.join_crew(update, 42, "LEELOO-AB23"))
    assert bot.user_state[42]["crew_code"] == "LEELOO-AB23"
    assert os.path.exists(bot.STATE_FILE)

    # Simulated restart: a fresh module instance pointing at the same file
    restarted = _load_bot()
    monkeypatch.setattr(restarted, "STATE_FILE", bot.STATE_FILE)
    assert restarted.user_state == {}
    restarted.load_state()
    assert restarted.user_state == {42: {"crew_code": "LEELOO-AB23", "display_name": "Ana"}}

    # ...so a plain message goes to the crew instead of "Join a crew first"
    relay = use_relay(restarted, monkeypatch, {"success": True, "devices_reached": 1, "devices_queued": 0})
    update = make_update("hello")
    asyncio.run(restarted.handle_message(update, make_context()))
    assert relay.calls and relay.calls[0][0] == "/api/telegram/message"
    assert relay.calls[0][1]["crew_code"] == "LEELOO-AB23"
    assert "Join a crew first" not in update.message.last


def test_state_file_from_env(tmp_path, monkeypatch):
    path = str(tmp_path / "custom.json")
    monkeypatch.setenv("LEELOO_BOT_STATE_FILE", path)
    module = _load_bot()
    assert module.STATE_FILE == path


def test_save_is_atomic_and_leaves_no_temp_files(bot):
    bot.set_user_crew(1, "LEELOO-AAAA", "A")
    bot.set_user_crew(2, "LEELOO-BBBB", "B")
    directory = os.path.dirname(bot.STATE_FILE)
    assert os.listdir(directory) == ["bot_state.json"]
    with open(bot.STATE_FILE) as f:
        assert set(json.load(f)["users"]) == {"1", "2"}


def test_missing_state_file_tolerated(bot):
    bot.load_state()
    assert bot.user_state == {}


@pytest.mark.parametrize("content", [
    "{not json",
    "",
    "[1, 2, 3]",
    '{"users": "nope"}',
    '{"users": {"abc": {"crew_code": "LEELOO-AAAA"}, "7": "x", "8": {"crew_code": 5}}}',
])
def test_corrupt_state_file_tolerated(bot, content):
    with open(bot.STATE_FILE, "w") as f:
        f.write(content)
    bot.load_state()
    assert bot.user_state == {}
    # and the bot can still save afterwards
    bot.set_user_crew(5, "LEELOO-CCCC", "C")
    bot.load_state()
    assert bot.user_state[5]["crew_code"] == "LEELOO-CCCC"


def test_save_failure_does_not_raise(bot, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(bot, "STATE_FILE", str(blocker / "sub" / "state.json"))
    bot.set_user_crew(1, "LEELOO-AAAA", "A")  # must not raise
    assert bot.user_state[1]["crew_code"] == "LEELOO-AAAA"


# --- Markdown safety ---

@pytest.mark.parametrize("text", [
    "snake_case rocks",
    "2 * 3 = 6",
    "see [this",
    "a `tick",
    "_*[`",
    "back\\slash_",
])
def test_markdown_specials_do_not_break_confirmation(bot, monkeypatch, text):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "A_na*"}
    relay = use_relay(bot, monkeypatch, {"success": True, "devices_reached": 2, "devices_queued": 0})
    update = make_update(text)
    asyncio.run(bot.handle_message(update, make_context()))

    assert len(relay.calls) == 1
    assert relay.calls[0][1]["payload"]["text"] == text  # relay gets the raw text
    assert len(update.message.replies) == 1
    reply, kwargs = update.message.replies[0]
    assert "Message Sent!" in reply
    assert kwargs.get("parse_mode") == "Markdown"  # accepted first time, properly escaped
    assert len(update.message.attempts) == 1


def test_md_escape(bot):
    assert bot.md_escape("a_b*c`d[e") == "a\\_b\\*c\\`d\\[e"
    assert bot.md_escape("plain") == "plain"
    assert bot.md_escape(None) == ""


def test_rejected_markdown_falls_back_to_plain_text(bot):
    message = FakeMessage()

    async def run():
        return await bot._send(message.reply_text, "broken _markdown", markdown=True)

    assert asyncio.run(run()) is True
    assert message.replies == [("broken _markdown", {})]


def test_failed_confirmation_is_not_reported_as_failed_send(bot, monkeypatch):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    relay = use_relay(bot, monkeypatch, {"success": True, "devices_reached": 1})
    update = make_update("hi", always_fail=True)
    asyncio.run(bot.handle_message(update, make_context()))  # must not raise
    assert len(relay.calls) == 1
    assert all("not sent" not in text.lower() for text, _ in update.message.attempts)


def test_welcome_with_special_name(bot):
    update = make_update(first_name="a_b")
    asyncio.run(bot.start(update, make_context()))
    assert len(update.message.replies) == 1
    assert update.message.replies[0][1].get("parse_mode") == "Markdown"


def test_crew_status_escapes_names(bot, monkeypatch):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    use_relay(bot, monkeypatch, {
        "crew_code": "LEELOO-AB23", "devices_online": 1,
        "device_names": ["kitchen_pi"], "telegram_user_names": ["*star", "[x"],
    })
    edits = FakeMessage()
    query = SimpleNamespace(edit_message_text=edits.reply_text)
    asyncio.run(bot.show_crew_status(query, 42))
    assert len(edits.attempts) == 1 and len(edits.replies) == 1
    assert "kitchen\\_pi" in edits.last


# --- Relay errors ---

def test_join_rate_limited(bot, monkeypatch):
    use_relay(bot, monkeypatch, {
        "error": "rate_limited", "message": "Too many attempts, try again later", "status": 429,
    })
    update = make_update()
    asyncio.run(bot.join_crew(update, 42, "LEELOO-AB23"))
    reply = update.message.last.lower()
    assert "too many attempts" in reply
    assert "wait a few minutes" in reply
    assert 42 not in bot.user_state
    assert not os.path.exists(bot.STATE_FILE)


def test_join_crew_not_found(bot, monkeypatch):
    use_relay(bot, monkeypatch, {"error": "crew_not_found", "message": "x", "status": 404})
    update = make_update()
    asyncio.run(bot.join_crew(update, 42, "LEELOO-AB23"))
    assert "not found" in update.message.last
    assert 42 not in bot.user_state


def test_join_invalid_code_never_calls_relay(bot, monkeypatch):
    relay = use_relay(bot, monkeypatch, {"success": True})
    for code in ["LEELOO-AB`3", "LEELOO-_*[x", "HELLO", "LEELOO-ABCDE"]:
        update = make_update()
        asyncio.run(bot.join_crew(update, 42, code))
        assert "Invalid crew code" in update.message.last
    assert relay.calls == []


@pytest.mark.parametrize("error", [
    {"error": "network_error"},
    {"error": "bad_response", "status": 502},
    {"error": "http_500", "status": 500},
    {"error": "unauthorized", "status": 401},
])
def test_relay_errors_give_short_clear_message(bot, monkeypatch, error):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}

    use_relay(bot, monkeypatch, error)
    update = make_update("hi")
    asyncio.run(bot.handle_message(update, make_context()))
    assert len(update.message.replies) == 1
    assert "Message Sent" not in update.message.last
    assert len(update.message.last) < 200
    assert bot.user_state[42]["crew_code"] == "LEELOO-AB23"  # still a member

    update = make_update()
    asyncio.run(bot.join_crew(update, 43, "LEELOO-ZZ99"))
    assert "Could not join" in update.message.last
    assert 43 not in bot.user_state

    edits = FakeMessage()
    query = SimpleNamespace(edit_message_text=edits.reply_text,
                            from_user=SimpleNamespace(id=44, first_name="Bo"))
    asyncio.run(bot.create_crew(query, 44))
    assert "Could not create a crew" in edits.last
    assert 44 not in bot.user_state

    edits = FakeMessage()
    query = SimpleNamespace(edit_message_text=edits.reply_text)
    asyncio.run(bot.show_crew_status(query, 42))
    assert "Could not get crew status" in edits.last


def test_not_in_crew_forgets_membership(bot, monkeypatch):
    bot.set_user_crew(42, "LEELOO-AB23", "Ana")
    use_relay(bot, monkeypatch, {"error": "not_in_crew", "message": "x", "status": 403})
    update = make_update("hi")
    asyncio.run(bot.handle_message(update, make_context()))
    assert "/start" in update.message.last
    assert 42 not in bot.user_state
    bot.load_state()
    assert 42 not in bot.user_state


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return json.loads(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, outcome):
        self.outcome = outcome

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def request(self, method, url, **kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_relay_request_network_error(bot, monkeypatch):
    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(OSError("connection refused")))
    data = asyncio.run(bot.relay_post("/api/telegram/message", {}))
    assert data == {"error": "network_error"}


def test_relay_request_timeout(bot, monkeypatch):
    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(asyncio.TimeoutError()))
    data = asyncio.run(bot.relay_get("/api/telegram/crew/status", {"crew_code": "x"}))
    assert data == {"error": "network_error"}


def test_relay_request_non_json(bot, monkeypatch):
    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(_FakeResp(502, "<html>Bad Gateway</html>")))
    data = asyncio.run(bot.relay_post("/x", {}))
    assert data == {"error": "bad_response", "status": 502}


def test_relay_request_non_object_json(bot, monkeypatch):
    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(_FakeResp(200, "[]")))
    assert asyncio.run(bot.relay_post("/x", {}))["error"] == "bad_response"


def test_relay_request_http_errors(bot, monkeypatch):
    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(
        _FakeResp(429, '{"error": "rate_limited", "message": "Too many attempts, try again later"}')))
    data = asyncio.run(bot.relay_post("/x", {}))
    assert data["error"] == "rate_limited" and data["status"] == 429

    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(_FakeResp(500, "{}")))
    data = asyncio.run(bot.relay_post("/x", {}))
    assert data == {"error": "http_500", "status": 500}

    monkeypatch.setattr(bot.aiohttp, "ClientSession", _FakeSession(_FakeResp(200, '{"success": true}')))
    assert asyncio.run(bot.relay_post("/x", {})) == {"success": True}


def test_relay_network_error_end_to_end(bot, monkeypatch):
    """Real relay_post against a port nobody listens on."""
    monkeypatch.setattr(bot, "RELAY_API_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(bot, "RELAY_TIMEOUT_S", 3)
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    update = make_update("hi")
    asyncio.run(bot.handle_message(update, make_context()))
    assert "Can't reach the LEELOO server" in update.message.last


# --- Delivery wording ---

def _send_and_get_reply(bot, monkeypatch, response):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    use_relay(bot, monkeypatch, response)
    update = make_update("hi")
    asyncio.run(bot.handle_message(update, make_context()))
    return update.message.last


def test_wording_delivered_only(bot, monkeypatch):
    reply = _send_and_get_reply(bot, monkeypatch, {"success": True, "devices_reached": 2, "devices_queued": 0})
    assert "Delivered now to 2 devices." in reply
    assert "back online" not in reply


def test_wording_single_device_and_old_relay(bot, monkeypatch):
    # an older relay does not send devices_queued at all
    reply = _send_and_get_reply(bot, monkeypatch, {"success": True, "devices_reached": 1})
    assert "Delivered now to 1 device." in reply


def test_wording_delivered_and_queued(bot, monkeypatch):
    reply = _send_and_get_reply(bot, monkeypatch, {"success": True, "devices_reached": 1, "devices_queued": 2})
    assert "Delivered now to 1 device." in reply
    assert "2 devices offline" in reply
    assert "will be delivered when their LEELOO is back online" in reply


def test_wording_queued_only(bot, monkeypatch):
    reply = _send_and_get_reply(bot, monkeypatch, {"success": True, "devices_reached": 0, "devices_queued": 1})
    assert "Message Sent" not in reply
    assert "Delivered now" not in reply
    assert "will be delivered when their LEELOO is back online" in reply


def test_wording_nobody(bot, monkeypatch):
    reply = _send_and_get_reply(bot, monkeypatch, {"success": True, "devices_reached": 0, "devices_queued": 0})
    assert "Message Sent" not in reply
    assert "No device received it" in reply


# --- None-safety / flow ---

def test_edited_message_and_missing_user_are_ignored(bot, monkeypatch):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    relay = use_relay(bot, monkeypatch, {"success": True, "devices_reached": 1})
    user = SimpleNamespace(id=42, first_name="Ana")
    for update in [
        SimpleNamespace(effective_user=user, message=None, callback_query=None),
        SimpleNamespace(effective_user=None, message=FakeMessage("hi"), callback_query=None),
        SimpleNamespace(effective_user=user, message=FakeMessage("   "), callback_query=None),
        SimpleNamespace(effective_user=user, message=SimpleNamespace(text=None), callback_query=None),
    ]:
        asyncio.run(bot.handle_message(update, make_context()))
        if update.message is None or update.effective_user is None:
            asyncio.run(bot.start(update, make_context()))
        asyncio.run(bot.handle_callback(update, make_context()))
    asyncio.run(bot.help_command(SimpleNamespace(effective_user=user, message=None), make_context()))
    assert relay.calls == []


def test_awaiting_crew_code_flow_and_deep_link(bot, monkeypatch):
    relay = use_relay(bot, monkeypatch, {"success": True, "crew_code": "LEELOO-AB23", "devices_online": 0})
    context = make_context()
    context.user_data["awaiting_crew_code"] = True
    update = make_update(" leeloo-ab23 ")
    asyncio.run(bot.handle_message(update, context))
    assert relay.calls[0][1]["crew_code"] == "LEELOO-AB23"
    assert bot.user_state[42]["crew_code"] == "LEELOO-AB23"
    assert context.user_data["awaiting_crew_code"] is False

    bot.user_state.clear()
    context = make_context(args=["leelooxy77"])
    context.user_data["awaiting_crew_code"] = True
    asyncio.run(bot.start(make_update(), context))
    assert bot.user_state[42]["crew_code"] == "LEELOO-XY77"
    assert context.user_data["awaiting_crew_code"] is False


def test_callback_survives_stale_query(bot, monkeypatch):
    bot.user_state[42] = {"crew_code": "LEELOO-AB23", "display_name": "Ana"}
    edits = FakeMessage()

    async def answer():
        raise BadRequest("Query is too old and response timeout expired")

    query = SimpleNamespace(answer=answer, data="my_crew", edit_message_text=edits.reply_text,
                            from_user=SimpleNamespace(id=42, first_name="Ana"))
    asyncio.run(bot.handle_callback(SimpleNamespace(callback_query=query), make_context()))
    assert "LEELOO-AB23" in edits.last


def test_refresh_with_unchanged_status_does_not_raise(bot):
    async def edit(text, **kwargs):
        raise BadRequest("Message is not modified: specified new message content is the same")

    assert asyncio.run(bot._send(edit, "same", markdown=True)) is True
