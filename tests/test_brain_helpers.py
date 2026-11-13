"""Pure helper functions of leeloo_brain."""
from datetime import datetime

import leeloo_brain as b


def test_wrap_text_respects_width_and_splits_long_words():
    lines = b.wrap_text("the quick brown fox jumps over the lazy dog " + "x" * 50)
    assert all(len(l) <= b.WRAP_CHARS for l in lines)
    assert "".join(lines).replace(" ", "") == ("thequickbrownfoxjumpsoverthelazydog" + "x" * 50)
    assert b.wrap_text("") == [] and b.wrap_text(None) == []


def test_format_display_text_uses_the_given_colour():
    lines = b.format_display_text("hello world", "#123456")
    assert lines == [("hello world", "normal", "#123456")]
    assert b.format_display_text("", "#111")[0][0] == "no response"


def test_music_prefilter():
    assert b.looks_like_music("yall have to listen to that new Fred again song")
    assert b.looks_like_music("that Kendrick album is fire")
    assert not b.looks_like_music("hey whats up tonight")
    assert not b.looks_like_music("running 10 min late")


def test_spotify_links():
    assert b.spotify_uri_in_text("yo https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=1") == "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
    assert b.spotify_uri_in_text("https://open.spotify.com/intl-de/track/abc123") == "spotify:track:abc123"
    assert b.spotify_uri_in_text("spotify:track:abc123 !!") == "spotify:track:abc123"
    assert b.spotify_uri_in_text("no link") is None


def test_rain_viz_uses_real_data_only():
    assert b.build_rain_viz(None) == []
    assert b.build_rain_viz({'rain_24h_inches': 2.0, 'is_raining': True}) == []   # no forecast -> no chart
    dry = b.build_rain_viz({'precip_15min_mm': [0] * 24})
    assert len(dry) == 1 and "no rain" in dry[0][0]
    data = [0, 0.1, 0.5, 1.0, 5.0] + [0] * 19
    bars, timeline = b.build_rain_viz({'precip_15min_mm': data})
    assert len(bars[0]) == b.RAIN_BARS <= b.WRAP_CHARS
    assert bars[0][0] == ' ' and bars[0][4] == '█'
    assert bars[0][1] < bars[0][2] < bars[0][3] < bars[0][4]      # more rain -> taller bar
    assert b.build_rain_viz({'precip_15min_mm': data}) == [bars, timeline]   # deterministic
    assert len(timeline[0]) <= b.RAIN_BARS and bars[1] == timeline[1]       # same font -> aligned


def test_roll_forward():
    now = datetime(2026, 11, 12, 12, 0)
    assert b.roll_forward(datetime(2024, 3, 27, 19, 0), now) == datetime(2027, 3, 27, 19, 0)
    assert b.roll_forward(datetime(2026, 12, 1, 19, 0), now) == datetime(2026, 12, 1, 19, 0)
    assert b.roll_forward(datetime(2024, 2, 29, 9, 0), now) > now


def test_contact_names():
    assert b.contact_names(["Amy", {"name": "Ben"}, "Amy", "", None, {"x": 1}]) == ["Amy", "Ben"]


def test_load_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# c\nFOO_TEST_KEY="bar"\nEMPTY=\n bad line \nBAZ_TEST = \'q=1\'\n')
    monkeypatch.delenv("FOO_TEST_KEY", raising=False)
    b.load_env_file(str(env))
    import os
    assert os.environ["FOO_TEST_KEY"] == "bar" and os.environ["BAZ_TEST"] == "q=1"
    assert "EMPTY" not in os.environ
    b.load_env_file(str(tmp_path / "missing"))     # no error
