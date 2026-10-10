"""Tests for Spotify credential handling in leeloo_music_manager.py.

Credentials must come from the environment / .env only, never from source.
No network: requests is replaced with fakes that record their kwargs.
"""
import importlib
import os
import re

import pytest

import leeloo_music_manager as mm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_KEYS = ("LEELOO_TEST_A", "LEELOO_TEST_B", "LEELOO_TEST_C", "LEELOO_TEST_D", "LEELOO_TEST_EMPTY")


@pytest.fixture
def clean_test_keys(monkeypatch):
    # delenv registers an undo that also removes keys the loader adds later
    for key in TEST_KEYS:
        monkeypatch.delenv(key, raising=False)


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text)
    return str(path)


# =============================================================================
# load_env_defaults
# =============================================================================

def test_loader_reads_env_file(tmp_path, clean_test_keys):
    path = write_env(tmp_path, (
        "# comment line\n"
        "\n"
        "LEELOO_TEST_A=plain\n"
        'LEELOO_TEST_B="double quoted"\n'
        "LEELOO_TEST_C='single quoted'\n"
        "  LEELOO_TEST_D = spaced = value  \n"
        "LEELOO_TEST_EMPTY=\n"
        "not a key value line\n"
    ))
    loaded = mm.load_env_defaults(path)
    assert os.environ["LEELOO_TEST_A"] == "plain"
    assert os.environ["LEELOO_TEST_B"] == "double quoted"
    assert os.environ["LEELOO_TEST_C"] == "single quoted"
    assert os.environ["LEELOO_TEST_D"] == "spaced = value"
    assert "LEELOO_TEST_EMPTY" not in os.environ
    assert sorted(loaded) == ["LEELOO_TEST_A", "LEELOO_TEST_B", "LEELOO_TEST_C", "LEELOO_TEST_D"]


def test_loader_does_not_override_existing_env(tmp_path, clean_test_keys, monkeypatch):
    monkeypatch.setenv("LEELOO_TEST_A", "from-environment")
    path = write_env(tmp_path, "LEELOO_TEST_A=from-file\nLEELOO_TEST_B=from-file\n")
    loaded = mm.load_env_defaults(path)
    assert os.environ["LEELOO_TEST_A"] == "from-environment"
    assert os.environ["LEELOO_TEST_B"] == "from-file"
    assert loaded == ["LEELOO_TEST_B"]


def test_loader_keeps_key_set_to_empty_string(tmp_path, clean_test_keys, monkeypatch):
    """An operator can export KEY= on purpose (e.g. to disable Spotify); .env must not refill it"""
    monkeypatch.setenv("LEELOO_TEST_A", "")
    path = write_env(tmp_path, "LEELOO_TEST_A=from-file\n")
    assert mm.load_env_defaults(path) == []
    assert os.environ["LEELOO_TEST_A"] == ""


def test_leeloo_home_env_var_is_honoured(tmp_path, monkeypatch):
    """Like leeloo_brain, LEELOO_HOME overrides /home/pi/leeloo-ui, and the
    module reads that home's .env when imported standalone."""
    (tmp_path / ".env").write_text("SPOTIFY_CLIENT_ID=home-id\nSPOTIFY_CLIENT_SECRET='home-secret'\n")
    monkeypatch.setenv("LEELOO_HOME", str(tmp_path))
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    try:
        importlib.reload(mm)
        assert mm.LEELOO_HOME == str(tmp_path)
        assert mm.ENV_FILE == str(tmp_path / ".env")
        assert mm.TOKENS_FILE == str(tmp_path / "spotify_tokens.json")
        assert mm.SPOTIFY_CLIENT_ID == "home-id"
        assert mm.SPOTIFY_CLIENT_SECRET == "home-secret"
    finally:
        monkeypatch.undo()
        importlib.reload(mm)


def test_leeloo_home_default(monkeypatch):
    monkeypatch.delenv("LEELOO_HOME", raising=False)
    try:
        importlib.reload(mm)
        assert mm.LEELOO_HOME == "/home/pi/leeloo-ui"
    finally:
        monkeypatch.undo()
        importlib.reload(mm)


def test_loader_missing_file_is_quiet(tmp_path):
    assert mm.load_env_defaults(str(tmp_path / "nope.env")) == []


def test_loader_default_path_is_leeloo_home():
    assert mm.ENV_FILE == os.path.join(mm.LEELOO_HOME, ".env")


# =============================================================================
# No secrets in source
# =============================================================================

def test_no_spotify_secret_literal_in_source():
    with open(os.path.join(REPO_ROOT, "leeloo_music_manager.py")) as f:
        src = f.read()
    # A Spotify client ID / secret is 32 hex chars; none may appear in the file
    assert not re.search(r"\b[0-9a-fA-F]{32}\b", src)
    assert re.search(r'SPOTIFY_CLIENT_ID = os\.environ\.get\("SPOTIFY_CLIENT_ID"', src)
    assert re.search(r'SPOTIFY_CLIENT_SECRET = os\.environ\.get\("SPOTIFY_CLIENT_SECRET"', src)


def test_module_names_come_from_environment(monkeypatch):
    """Reloading with credentials in os.environ exposes them as module names
    (leeloo_brain imports SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET from here)."""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-client-secret")
    try:
        importlib.reload(mm)
        assert mm.SPOTIFY_CLIENT_ID == "test-client-id"
        assert mm.SPOTIFY_CLIENT_SECRET == "test-client-secret"
        assert mm._have_spotify_credentials()
    finally:
        monkeypatch.undo()
        importlib.reload(mm)


def test_missing_secret_warns_once_and_imports(monkeypatch, capsys):
    if os.path.exists(mm.ENV_FILE):
        pytest.skip("a real LEELOO .env exists on this machine")
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    try:
        importlib.reload(mm)
        out = capsys.readouterr().out
        assert out.count("WARNING: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set") == 1
        assert mm.SPOTIFY_CLIENT_SECRET == ""
        assert not mm._have_spotify_credentials()
    finally:
        monkeypatch.undo()
        importlib.reload(mm)


# =============================================================================
# Functions that need the secret
# =============================================================================

class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def forbid_network(*args, **kwargs):
    raise AssertionError("no HTTP call expected without credentials")


def test_refresh_without_secret_returns_none(monkeypatch):
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_SECRET", "")
    monkeypatch.setattr(mm, "load_tokens", lambda: {"refresh_token": "r", "access_token": "a"})
    monkeypatch.setattr(mm.requests, "post", forbid_network)
    assert mm.refresh_access_token() is None


def test_listeners_lookup_without_secret_returns_none(monkeypatch):
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_SECRET", "")
    monkeypatch.setattr(mm.requests, "post", forbid_network)
    monkeypatch.setattr(mm.requests, "get", forbid_network)
    assert mm.get_listeners_for_artist_name("Tame Impala") is None


def test_refresh_uses_timeout_and_env_credentials(monkeypatch):
    calls = []
    saved = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, {"access_token": "new-token"})

    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setattr(mm, "SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(mm, "load_tokens", lambda: {"refresh_token": "r", "access_token": "a"})
    monkeypatch.setattr(mm, "save_tokens", saved.append)
    monkeypatch.setattr(mm.requests, "post", fake_post)

    assert mm.refresh_access_token() == "new-token"
    url, kwargs = calls[0]
    assert url == mm.SPOTIFY_TOKEN_URL
    assert kwargs["timeout"] == 10
    import base64
    assert kwargs["headers"]["Authorization"] == "Basic " + base64.b64encode(b"id:secret").decode()
    # Spotify may omit refresh_token on refresh: the old one is kept
    assert saved == [{"access_token": "new-token", "refresh_token": "r"}]


def test_every_requests_call_has_timeout():
    with open(os.path.join(REPO_ROOT, "leeloo_music_manager.py")) as f:
        src = f.read()
    # Find each requests.get/post( ... ) call and check its argument list has timeout=
    for match in re.finditer(r"requests\.(get|post)\(", src):
        depth, i = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        assert "timeout=" in src[match.end():i], src[match.start():i]
