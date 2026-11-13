"""Pack tests: validation, templates, conditions, PackManager (docs/PACKS.md)."""
import copy
import json
import os
import threading

import pytest

import leeloo_packs
from leeloo_packs import (
    MAX_PACK_BYTES,
    PackManager,
    condition_true,
    render_template,
    validate_pack,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "packs", "unc-hoops.json"), encoding="utf-8") as _f:
    UNC = json.load(_f)

MINIMAL = {"pack": 1, "id": "mini", "name": "Mini"}
T0 = 1_700_000_000          # floor(T0 / 20) is even -> rotation index 0


def unc(**changes):
    pack = copy.deepcopy(UNC)
    pack.update(changes)
    return pack


def changed(path_fn):
    """Copy of the UNC pack, changed in place by path_fn."""
    pack = copy.deepcopy(UNC)
    path_fn(pack)
    return pack


def source(**changes):
    s = {"id": "s1", "url": "https://api.open-meteo.com/v1/x", "every": 60, "fields": {"a": "b.c"}}
    s.update(changes)
    return s


def panel(**changes):
    p = {"slot": "weather", "rows": [{"left": "hello"}]}
    p.update(changes)
    return p


def with_row(row):
    return dict(MINIMAL, panels=[panel(), panel(rows=[row])])


def with_when(when):
    return dict(MINIMAL, panels=[panel(when=when)])


# ---------------------------------------------------------------- validate

def test_example_pack_is_valid():
    assert validate_pack(UNC) == []
    assert validate_pack(MINIMAL) == []


def test_all_optional_parts_valid():
    pack = dict(
        MINIMAL,
        theme={"colors": dict({k: "#00ff7F" for k in leeloo_packs.COLOR_KEYS}, bg="#101010"),
               "header": "x" * 12,
               "labels": {k: "y" * 10 for k in leeloo_packs.LABEL_KEYS}},
        sources=[source(id=f"s{i}", every=e) for i, e in enumerate((30, 3600, 61.5))],
        panels=[
            {"slot": "weather", "builtin": "weather", "priority": -100,
             "when": {"field": "s0.a", "exists": False}},
            panel(title="t" * 10, priority=100, when={"field": "s0.a", "not_equals": "x"},
                  rows=[{"left": "l" * 28, "right": "r" * 14},
                        {"left": "", "bar": {"value": "{s0.a}", "max": 0.5}},
                        {"left": "{s0.a} {s1.a}", "right": "{s2.a}"}]),
        ],
        rotate_seconds=5,
    )
    assert validate_pack(pack) == []
    assert validate_pack(dict(pack, rotate_seconds=600)) == []


def test_placeholders_do_not_count_for_the_text_limit():
    # "{game.home_score}" is 17 characters; the example uses it as `right` (limit 14)
    assert validate_pack(with_row({"left": "x" * 28 + "{game.home}", "right": "{game.home_score}"})) == []
    errors = validate_pack(with_row({"left": "x", "right": "y" * 15 + "{game.home_score}"}))
    assert errors == ["panels[1].rows[0].right: longer than 14 characters"]


INVALID = [
    # (name, pack, path that the error must name)
    ("unknown top-level key", dict(MINIMAL, extra=1), "extra"),
    ("unknown key in theme", dict(MINIMAL, theme={"font": "x"}), "theme.font"),
    ("unknown key in source", dict(MINIMAL, sources=[source(headers={})]), "sources[0].headers"),
    ("unknown key in panel", dict(MINIMAL, panels=[panel(color="red")]), "panels[0].color"),
    ("unknown key in row", with_row({"left": "a", "center": "b"}), "panels[1].rows[0].center"),
    ("unknown key in bar", with_row({"left": "a", "bar": {"value": "1", "max": 2, "min": 0}}),
     "panels[1].rows[0].bar.min"),
    ("unknown key in when", with_when({"field": "a.b", "equals": "x", "regex": "y"}), "panels[0].when.regex"),
    ("version", dict(MINIMAL, pack=2), "pack"),
    ("version as text", dict(MINIMAL, pack="1"), "pack"),
    ("version true", dict(MINIMAL, pack=True), "pack"),
    ("no version", {"id": "mini", "name": "Mini"}, "pack"),
    ("bad id (upper case)", dict(MINIMAL, id="UNC"), "id"),
    ("bad id (too long)", dict(MINIMAL, id="a" * 33), "id"),
    ("bad id (empty)", dict(MINIMAL, id=""), "id"),
    ("bad id (line end)", dict(MINIMAL, id="abc\n"), "id"),
    ("no id", {"pack": 1, "name": "Mini"}, "id"),
    ("name too long", dict(MINIMAL, name="n" * 25), "name"),
    ("name empty", dict(MINIMAL, name=""), "name"),
    ("no name", {"pack": 1, "id": "mini"}, "name"),
    ("theme not an object", dict(MINIMAL, theme=[]), "theme"),
    ("bad colour", dict(MINIMAL, theme={"colors": {"bg": "navy"}}), "theme.colors.bg"),
    ("short colour", dict(MINIMAL, theme={"colors": {"tan": "#FFF"}}), "theme.colors.tan"),
    ("colour not text", dict(MINIMAL, theme={"colors": {"tan": 255}}), "theme.colors.tan"),
    ("unknown colour key", dict(MINIMAL, theme={"colors": {"pink": "#FF00FF"}}), "theme.colors.pink"),
    ("header too long", dict(MINIMAL, theme={"header": "h" * 13}), "theme.header"),
    ("header empty", dict(MINIMAL, theme={"header": ""}), "theme.header"),
    ("label too long", dict(MINIMAL, theme={"labels": {"time": "t" * 11}}), "theme.labels.time"),
    ("unknown label", dict(MINIMAL, theme={"labels": {"music": "x"}}), "theme.labels.music"),
    ("4 sources", dict(MINIMAL, sources=[source(id=f"s{i}") for i in range(4)]), "sources"),
    ("sources not an array", dict(MINIMAL, sources={}), "sources"),
    ("http url", dict(MINIMAL, sources=[source(url="http://api.open-meteo.com/x")]), "sources[0].url"),
    ("file url", dict(MINIMAL, sources=[source(url="file:///etc/passwd")]), "sources[0].url"),
    ("no url", dict(MINIMAL, sources=[{"id": "s1", "every": 60, "fields": {"a": "b"}}]), "sources[0].url"),
    ("every too small", dict(MINIMAL, sources=[source(every=29)]), "sources[0].every"),
    ("every too big", dict(MINIMAL, sources=[source(every=3601)]), "sources[0].every"),
    ("every as text", dict(MINIMAL, sources=[source(every="60")]), "sources[0].every"),
    ("bad source id", dict(MINIMAL, sources=[source(id="Game")]), "sources[0].id"),
    ("source id twice", dict(MINIMAL, sources=[source(), source()]), "sources[1].id"),
    ("13 fields", dict(MINIMAL, sources=[source(fields={f"f{i}": "a" for i in range(13)})]), "sources[0].fields"),
    ("no fields", dict(MINIMAL, sources=[source(fields={})]), "sources[0].fields"),
    ("bad field key", dict(MINIMAL, sources=[source(fields={"Home-Score": "a"})]), "sources[0].fields.Home-Score"),
    ("field path too long", dict(MINIMAL, sources=[source(fields={"a": "p" * 201})]), "sources[0].fields.a"),
    ("field path not text", dict(MINIMAL, sources=[source(fields={"a": 3})]), "sources[0].fields.a"),
    ("7 panels", dict(MINIMAL, panels=[panel() for _ in range(7)]), "panels"),
    ("panel not an object", dict(MINIMAL, panels=["weather"]), "panels[0]"),
    ("bad slot", dict(MINIMAL, panels=[panel(slot="time")]), "panels[0].slot"),
    ("no slot", dict(MINIMAL, panels=[{"rows": [{"left": "a"}]}]), "panels[0].slot"),
    ("bad builtin", dict(MINIMAL, panels=[{"slot": "weather", "builtin": "time"}]), "panels[0].builtin"),
    ("builtin with rows", dict(MINIMAL, panels=[{"slot": "weather", "builtin": "weather",
                                                 "rows": [{"left": "a"}]}]), "panels[0]"),
    ("no rows", dict(MINIMAL, panels=[{"slot": "weather", "title": "x"}]), "panels[0].rows"),
    ("0 rows", dict(MINIMAL, panels=[panel(rows=[])]), "panels[0].rows"),
    ("4 rows", dict(MINIMAL, panels=[panel(rows=[{"left": "a"}] * 4)]), "panels[0].rows"),
    ("title too long", dict(MINIMAL, panels=[panel(title="t" * 11)]), "panels[0].title"),
    ("left too long", with_row({"left": "l" * 29}), "panels[1].rows[0].left"),
    ("no left", with_row({"right": "r"}), "panels[1].rows[0].left"),
    ("right too long", with_row({"left": "l", "right": "r" * 15}), "panels[1].rows[0].right"),
    ("right and bar", with_row({"left": "l", "right": "r", "bar": {"value": "1", "max": 2}}),
     "panels[1].rows[0]"),
    ("bar max 0", with_row({"left": "l", "bar": {"value": "1", "max": 0}}), "panels[1].rows[0].bar.max"),
    ("bar max negative", with_row({"left": "l", "bar": {"value": "1", "max": -5}}), "panels[1].rows[0].bar.max"),
    ("bar max as text", with_row({"left": "l", "bar": {"value": "1", "max": "10"}}), "panels[1].rows[0].bar.max"),
    ("bar without value", with_row({"left": "l", "bar": {"max": 10}}), "panels[1].rows[0].bar.value"),
    ("when with two operators", with_when({"field": "a.b", "equals": "x", "exists": True}), "panels[0].when"),
    ("when with no operator", with_when({"field": "a.b"}), "panels[0].when"),
    ("when without field", with_when({"equals": "x"}), "panels[0].when.field"),
    ("when exists not a bool", with_when({"field": "a.b", "exists": "yes"}), "panels[0].when.exists"),
    ("when equals not text", with_when({"field": "a.b", "equals": 3}), "panels[0].when.equals"),
    ("priority too big", dict(MINIMAL, panels=[panel(priority=101)]), "panels[0].priority"),
    ("priority too small", dict(MINIMAL, panels=[panel(priority=-101)]), "panels[0].priority"),
    ("priority not an integer", dict(MINIMAL, panels=[panel(priority=1.5)]), "panels[0].priority"),
    ("rotate too small", dict(MINIMAL, rotate_seconds=4), "rotate_seconds"),
    ("rotate too big", dict(MINIMAL, rotate_seconds=601), "rotate_seconds"),
    ("rotate as text", dict(MINIMAL, rotate_seconds="20"), "rotate_seconds"),
    ("rotate true", dict(MINIMAL, rotate_seconds=True), "rotate_seconds"),
]


@pytest.mark.parametrize("name,pack,path", INVALID, ids=[c[0] for c in INVALID])
def test_invalid_pack_names_the_path(name, pack, path):
    errors = validate_pack(pack)
    assert errors, name
    assert all(isinstance(e, str) for e in errors)
    assert any(e.startswith(path + ":") for e in errors), errors
    # one mistake -> one error (no flood of follow-up errors)
    assert len(errors) == 1, errors


def test_error_text_example_from_the_interface():
    assert validate_pack(with_row({"left": "l" * 29})) == [
        "panels[1].rows[0].left: longer than 28 characters"]


@pytest.mark.parametrize("bad", [None, [], "pack", 1, True, [MINIMAL]])
def test_non_dict_input(bad):
    errors = validate_pack(bad)
    assert len(errors) == 1 and errors[0].startswith("pack:")


def test_oversize_pack():
    # Valid in every part, only too large as a whole
    fields = {f"f{i}": "p" * 200 for i in range(12)}
    big = dict(MINIMAL, sources=[source(id=f"s{i}", fields=fields) for i in range(3)])
    while len(json.dumps(big, separators=(",", ":")).encode()) <= MAX_PACK_BYTES:
        big["sources"][0]["url"] += "x" * 500
    errors = validate_pack(big)
    assert len(errors) == 1 and errors[0].startswith("pack:") and "8000" in errors[0]
    assert MAX_PACK_BYTES == 8000


def test_size_is_counted_in_utf8_bytes():
    pack = dict(MINIMAL, sources=[source(url="https://api.open-meteo.com/" + "é" * 3990)])
    assert len(json.dumps(pack, ensure_ascii=False)) < MAX_PACK_BYTES      # characters: fits
    assert validate_pack(pack)[0].startswith("pack:")                       # bytes: too large


def test_validate_never_raises_on_odd_data():
    odd = [
        {"pack": 1, "id": ["x"], "name": {}},
        dict(MINIMAL, panels=[None, 3, "x"]),
        dict(MINIMAL, sources=[None]),
        dict(MINIMAL, theme={"colors": [], "labels": 3, "header": None}),
        dict(MINIMAL, panels=[panel(rows=[None, 3]), panel(rows="abc"), panel(when="x")]),
        dict(MINIMAL, rotate_seconds=float("nan")),
        dict(MINIMAL, extra={1, 2}),
        {1: 2},
    ]
    for pack in odd:
        assert validate_pack(pack), pack


def test_validate_does_not_change_the_pack():
    before = copy.deepcopy(UNC)
    validate_pack(UNC)
    assert UNC == before


# --------------------------------------------------------------- templates

def test_template_present_missing_several():
    values = {"game.home": "UNC", "game.home_score": "78", "game.away": "DUKE"}
    assert render_template("{game.home}", values) == "UNC"
    assert render_template("{game.home} {game.home_score}", values) == "UNC 78"
    assert render_template("{game.home}-{game.away}: {game.home_score}", values) == "UNC-DUKE: 78"
    assert render_template("{game.clock}", values) == "--"
    assert render_template("period {game.period} {game.home}", values) == "period -- UNC"
    assert render_template("{game.home}{game.home}", values) == "UNCUNC"


def test_template_without_braces_is_unchanged():
    for text in ("final", "", "50% of 3", "a } b"):
        assert render_template(text, {"final": "x"}) == text


def test_template_braces_without_match():
    values = {"a.b": "1"}
    assert render_template("{", values) == "{"
    assert render_template("a { b", values) == "a { b"
    assert render_template("{}", values) == "{}"
    assert render_template("{a.b", values) == "{a.b"
    assert render_template("{{a.b}}", values) == "{1}"
    assert render_template("{not a name}", values) == "{not a name}"


def test_template_never_raises():
    assert render_template("{a.b}", None) == "--"
    assert render_template("{a.b}", "text") == "--"
    assert render_template("{a.b}", {"a.b": 12}) == "12"
    assert render_template(None, {}) == ""
    assert render_template(42, {}) == "42"
    # a value is put in as it is (a "{x}" inside a value is not a template)
    assert render_template("{a.b}", {"a.b": "{c.d}", "c.d": "no"}) == "{c.d}"


# -------------------------------------------------------------- conditions

def test_conditions():
    values = {"game.state": "in", "game.period": "2"}
    assert condition_true(None, values) is True
    assert condition_true({"field": "game.state", "equals": "in"}, values) is True
    assert condition_true({"field": "game.state", "equals": "post"}, values) is False
    assert condition_true({"field": "game.state", "not_equals": "post"}, values) is True
    assert condition_true({"field": "game.state", "not_equals": "in"}, values) is False
    assert condition_true({"field": "game.state", "exists": True}, values) is True
    assert condition_true({"field": "game.state", "exists": False}, values) is False
    assert condition_true({"field": "game.clock", "exists": True}, values) is False
    assert condition_true({"field": "game.clock", "exists": False}, values) is True


def test_conditions_compare_as_strings():
    assert condition_true({"field": "game.period", "equals": "2"}, {"game.period": 2}) is True
    assert condition_true({"field": "game.period", "not_equals": "2"}, {"game.period": 2}) is False


def test_conditions_with_a_missing_value():
    assert condition_true({"field": "game.state", "equals": "in"}, {}) is False
    assert condition_true({"field": "game.state", "equals": ""}, {}) is False
    assert condition_true({"field": "game.state", "not_equals": "in"}, {}) is True


def test_conditions_never_raise():
    assert condition_true("in", {}) is False
    assert condition_true({}, {}) is False
    assert condition_true({"field": "a.b"}, {"a.b": "1"}) is False
    assert condition_true({"field": "a.b", "equals": "1"}, None) is False
    assert condition_true({"field": ["a"], "exists": False}, {}) is True


# ------------------------------------------------------------- PackManager

@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "pack.json")


@pytest.fixture
def mgr(path):
    m = PackManager(path)
    assert m.install(UNC) == []
    return m


def test_empty_manager(path):
    m = PackManager(path)
    assert m.pack is None and m.values == {}
    assert m.theme_colors() == {} and m.labels() == {} and m.header() is None
    assert m.current_panel() is None and m.current_panel(T0) is None
    assert m.signature() == m.signature(T0)
    assert m.set_values("unc-hoops", {"game.state": "in"}) is False
    m.reset()                                   # nothing to remove: no error
    assert not os.path.exists(path)


def test_install_persist_reload(path):
    m = PackManager(path)
    assert m.install(UNC) == []
    assert m.pack == UNC
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == UNC
    assert os.listdir(os.path.dirname(path)) == ["pack.json"]      # no temp file left
    again = PackManager(path)
    assert again.pack == UNC
    assert again.header() == "GO HEELS"
    assert again.labels() == {"messages": "crew"}
    assert again.theme_colors() == UNC["theme"]["colors"]
    assert again.values == {}                   # values are not stored


def test_install_makes_the_directory(tmp_path):
    p = tmp_path / "new" / "dir" / "pack.json"
    assert PackManager(str(p)).install(MINIMAL) == []
    assert PackManager(str(p)).pack == MINIMAL


def test_install_invalid_keeps_old_pack(mgr, path):
    mgr.set_values("unc-hoops", {"game.state": "in"})
    errors = mgr.install(dict(UNC, name="n" * 25))
    assert errors and errors[0].startswith("name:")
    assert mgr.install(None)
    assert mgr.pack == UNC
    assert mgr.values == {"game.state": "in"}
    assert PackManager(path).pack == UNC


def test_install_replaces_pack_and_clears_values(mgr, path):
    mgr.set_values("unc-hoops", {"game.state": "in"})
    assert mgr.install(MINIMAL) == []
    assert mgr.pack == MINIMAL and mgr.values == {}
    assert mgr.theme_colors() == {} and mgr.header() is None
    assert PackManager(path).pack == MINIMAL


def test_install_is_atomic(mgr, path, monkeypatch):
    """The new pack is written to a temp file and moved in with os.replace."""
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        with open(src, encoding="utf-8") as f:
            calls.append((os.path.dirname(src) == os.path.dirname(dst), dst, json.load(f)))
        return real_replace(src, dst)

    monkeypatch.setattr(leeloo_packs.os, "replace", spy)
    assert mgr.install(MINIMAL) == []
    assert calls == [(True, path, MINIMAL)]     # complete file, same directory


def test_failed_write_keeps_old_pack_and_file(mgr, path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(leeloo_packs.os, "replace", boom)
    errors = mgr.install(MINIMAL)
    assert len(errors) == 1 and errors[0].startswith("storage:")
    assert mgr.pack == UNC
    monkeypatch.undo()
    assert PackManager(path).pack == UNC
    assert os.listdir(os.path.dirname(path)) == ["pack.json"]      # temp file removed


def test_reset(mgr, path):
    mgr.set_values("unc-hoops", {"game.state": "in"})
    mgr.reset()
    assert mgr.pack is None and mgr.values == {}
    assert not os.path.exists(path)
    assert mgr.current_panel(T0) is None and mgr.theme_colors() == {}
    assert PackManager(path).pack is None
    mgr.reset()                                 # twice is fine


@pytest.mark.parametrize("content", [
    b"", b"not json", b"[1, 2]", b'{"pack": 1, "id": "BAD ID", "name": "x"}',
    b'{"pack": 2, "id": "x", "name": "x"}', b"\xff\xfe\x00", b"{" * 5000,
])
def test_invalid_file_on_disk_is_ignored_and_left_alone(path, content):
    with open(path, "wb") as f:
        f.write(content)
    m = PackManager(path)
    assert m.pack is None and m.current_panel() is None
    with open(path, "rb") as f:
        assert f.read() == content


def test_unreadable_path_means_no_pack(tmp_path):
    m = PackManager(str(tmp_path))              # a directory, not a file
    assert m.pack is None
    assert m.install(MINIMAL)                   # cannot be stored -> error
    assert m.pack is None


def test_set_values(mgr):
    assert mgr.set_values("unc-hoops", {"game.state": "pre", "game.period": 2, 7: "x",
                                        "game.long": "x" * 100, "game.none": None}) is True
    assert mgr.values == {"game.state": "pre", "game.period": "2", "game.long": "x" * 40}
    # the same values again: nothing changed
    assert mgr.set_values("unc-hoops", {"game.state": "pre", "game.period": "2",
                                        "game.long": "x" * 40}) is False
    # a message holds all values: a value that is not in it is gone
    assert mgr.set_values("unc-hoops", {"game.state": "in"}) is True
    assert mgr.values == {"game.state": "in"}


def test_set_values_ignores_other_pack_ids_and_bad_data(mgr):
    mgr.set_values("unc-hoops", {"game.state": "pre"})
    assert mgr.set_values("other-pack", {"game.state": "in"}) is False
    assert mgr.set_values(None, {"game.state": "in"}) is False
    assert mgr.set_values("unc-hoops", ["game.state"]) is False
    assert mgr.set_values("unc-hoops", None) is False
    assert mgr.set_values("unc-hoops", "in") is False
    assert mgr.values == {"game.state": "pre"}


def test_no_internal_objects_are_returned(mgr):
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home": "UNC"})
    mgr.pack["theme"]["colors"]["bg"] = "#000000"
    mgr.pack["panels"].clear()
    mgr.values["game.state"] = "post"
    mgr.theme_colors()["bg"] = "#000000"
    mgr.labels()["time"] = "x"
    mgr.current_panel(T0)["rows"].clear()
    assert mgr.pack == UNC
    assert mgr.values == {"game.state": "in", "game.home": "UNC"}
    assert mgr.theme_colors()["bg"] == "#13294B"
    assert mgr.labels() == {"messages": "crew"}
    assert len(mgr.current_panel(T0)["rows"]) == 3


def test_install_copies_the_pack(path):
    pack = copy.deepcopy(UNC)
    m = PackManager(path)
    assert m.install(pack) == []
    pack["theme"]["header"] = "CHANGED"
    assert m.header() == "GO HEELS"


# --------------------------------------------------------- panel selection

def test_no_values_shows_only_panels_without_when(mgr):
    for now in range(T0, T0 + 200, 7):
        assert mgr.current_panel(now) is None           # the built-in weather


def test_pre_game_alternates_builtin_and_next_game(mgr):
    mgr.set_values("unc-hoops", {"game.state": "pre", "game.match": "UNC @ DUKE",
                                 "game.when": "Sat 6:30 PM", "game.record": "20-4"})
    rotate = UNC["rotate_seconds"]
    shown = [mgr.current_panel(T0 + i * rotate) for i in range(6)]
    # panels in pack order: built-in (index 2), then "next game" (index 3)
    assert [p is None for p in shown] == [True, False, True, False, True, False]
    assert shown[1] == {
        "title": "next game",
        "rows": [
            {"left": "UNC @ DUKE", "right": None, "bar": None},
            {"left": "Sat 6:30 PM", "right": None, "bar": None},
            {"left": "20-4", "right": "--", "bar": None},       # game.standing is missing
        ],
    }
    # the same panel for the whole rotate_seconds, then the next one
    assert mgr.current_panel(T0 + rotate) == mgr.current_panel(T0 + 2 * rotate - 0.001)
    assert mgr.current_panel(T0 + rotate - 0.001) is None
    # deterministic for a given `now`
    assert mgr.current_panel(T0 + rotate) == mgr.current_panel(T0 + rotate)


def test_live_game_always_shows_the_live_panel(mgr):
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home": "UNC", "game.home_score": "78",
                                 "game.away": "DUKE", "game.away_score": "70",
                                 "game.period": "2", "game.clock": "4:12"})
    expected = {
        "title": "live",
        "rows": [
            {"left": "UNC", "right": "78", "bar": None},
            {"left": "DUKE", "right": "70", "bar": None},
            {"left": "period 2", "right": "4:12", "bar": None},
        ],
    }
    for now in range(T0, T0 + 200, 7):
        assert mgr.current_panel(now) == expected
    assert mgr.current_panel() == expected              # the clock


def test_final_panel_after_the_game(mgr):
    mgr.set_values("unc-hoops", {"game.state": "post", "game.home": "UNC", "game.home_score": "88",
                                 "game.away": "DUKE", "game.away_score": "80", "game.record": "21-4"})
    for now in (T0, T0 + 20, T0 + 40):
        p = mgr.current_panel(now)
        assert p["title"] == "final"
        assert p["rows"][0] == {"left": "UNC", "right": "88", "bar": None}
        assert p["rows"][2] == {"left": "final", "right": "21-4", "bar": None}


def test_pack_without_panels_shows_builtin(path):
    m = PackManager(path)
    m.install(MINIMAL)
    assert m.current_panel(T0) is None
    m.install(dict(MINIMAL, panels=[]))
    assert m.current_panel(T0) is None
    # no panel is true -> built-in
    m.install(dict(MINIMAL, panels=[panel(when={"field": "a.b", "exists": True})]))
    assert m.current_panel(T0) is None


def test_default_rotate_and_priority(path):
    m = PackManager(path)
    m.install(dict(MINIMAL, panels=[
        panel(title="a", rows=[{"left": "A"}]),
        panel(title="b", rows=[{"left": "B"}]),
        panel(title="c", rows=[{"left": "C"}]),
        panel(title="low", priority=-1, rows=[{"left": "LOW"}]),
    ]))
    # default rotate_seconds = 20; index = floor(now / 20) % 3
    for now in (0, 19.9, 20, 45, 60, T0, T0 + 20, T0 + 40):
        assert m.current_panel(now)["title"] == "abc"[int(now // 20) % 3]
    m.install(dict(MINIMAL, rotate_seconds=5, panels=[
        panel(title="a"), panel(title="top", priority=1), panel(title="b")]))
    assert {m.current_panel(now)["title"] for now in range(0, 100)} == {"top"}


def test_bar_rows(path):
    m = PackManager(path)
    pack = dict(MINIMAL, id="bars", panels=[panel(rows=[
        {"left": "score", "bar": {"value": "{s.score}", "max": 80}},
        {"left": "over", "bar": {"value": "{s.over}", "max": 10}},
        {"left": "text", "bar": {"value": "{s.text}", "max": 10}},
    ])])
    assert m.install(pack) == []
    m.set_values("bars", {"s.score": "20", "s.over": "250", "s.text": "n/a"})
    rows = m.current_panel(T0)["rows"]
    assert [r["bar"] for r in rows] == [0.25, 1.0, 0.0]
    assert all(r["right"] is None for r in rows)
    m.set_values("bars", {"s.score": "-3", "s.over": " 5.0 ", "s.text": "nan"})
    assert [r["bar"] for r in m.current_panel(T0)["rows"]] == [0.0, 0.5, 0.0]
    m.set_values("bars", {})
    assert [r["bar"] for r in m.current_panel(T0)["rows"]] == [0.0, 0.0, 0.0]     # "--" is not a number


# ---------------------------------------------------------------- signature

def test_signature_stable_and_hashable(mgr):
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home_score": "78"})
    a = mgr.signature(T0)
    hash(a)
    assert a == mgr.signature(T0) == mgr.signature(T0 + 20) == mgr.signature(T0 + 12345)
    # the same values again: no change
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home_score": "78"})
    assert mgr.signature(T0) == a


def test_signature_changes_with_a_value_of_the_current_panel(mgr):
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home_score": "78"})
    a = mgr.signature(T0)
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home_score": "80"})
    b = mgr.signature(T0)
    assert a != b
    # a value that the current panel does not show: nothing to repaint
    mgr.set_values("unc-hoops", {"game.state": "in", "game.home_score": "80", "game.match": "UNC @ DUKE"})
    assert mgr.signature(T0) == b


def test_signature_changes_with_the_rotation_index(mgr):
    mgr.set_values("unc-hoops", {"game.state": "pre", "game.match": "UNC @ DUKE"})
    builtin, custom = mgr.signature(T0), mgr.signature(T0 + 20)
    assert builtin != custom
    assert mgr.signature(T0 + 19) == builtin and mgr.signature(T0 + 39) == custom
    assert mgr.signature(T0 + 40) == builtin


def test_signature_changes_with_theme_and_pack(mgr, path):
    a = mgr.signature(T0)
    assert a != PackManager(path + ".none").signature(T0)       # pack / no pack
    for change in (
        lambda p: p["theme"]["colors"].update(bg="#000000"),
        lambda p: p["theme"].update(header="HEELS"),
        lambda p: p["theme"]["labels"].update(time="clock"),
        lambda p: p.update(id="unc-hoops-2"),
    ):
        assert mgr.install(changed(change)) == []
        assert mgr.signature(T0) != a
    assert mgr.install(UNC) == []
    assert mgr.signature(T0) == a
    mgr.reset()
    assert mgr.signature(T0) != a


# ------------------------------------------------------------------ threads

def test_reads_from_a_thread_while_the_pack_changes(mgr):
    """The render thread reads while the event loop installs / sets values."""
    errors = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                p = mgr.current_panel()
                assert p is None or 1 <= len(p["rows"]) <= 3
                hash(mgr.signature())
                assert isinstance(mgr.theme_colors(), dict)
                assert isinstance(mgr.labels(), dict)
        except Exception as e:                  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(60):
            mgr.set_values("unc-hoops", {"game.state": ("in", "pre", "post")[i % 3],
                                         "game.home_score": str(i)})
            if i % 10 == 0:
                mgr.install(MINIMAL if i % 20 else UNC)
            if i == 35:
                mgr.reset()
                mgr.install(UNC)
    finally:
        stop.set()
        t.join()
    assert not errors


# ---- rules added after the security review ----
def test_unreadable_colours_are_rejected():
    import leeloo_packs as lp
    base = {"pack": 1, "id": "x", "name": "x"}
    black = dict(base, theme={"colors": {k: "#000000" for k in ("bg", "green", "purple", "lavender", "tan", "white")}})
    errors = lp.validate_pack(black)
    assert errors and all("too close to the background" in e for e in errors) and len(errors) == 5
    assert any("background" in e for e in lp.validate_pack(dict(base, theme={"colors": {"bg": "#FFFFFF"}})))
    assert lp.validate_pack(dict(base, theme={"colors": {"bg": "#000000"}})) == []
    assert round(lp.contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0
    assert round(lp.contrast_ratio("#1A1D2E", "#1A1D2E"), 1) == 1.0


def test_lone_surrogates_and_huge_numbers_do_not_break_the_validator(tmp_path):
    import leeloo_packs as lp
    assert lp.validate_pack({"pack": 1, "id": "x", "name": "a\ud800"}) == ["pack: has an invalid character"]
    assert lp.validate_pack({"pack": 1, "id": "x", "name": "x", "rotate_seconds": 10 ** 400})   # an error list, no exception
    mgr = lp.PackManager(str(tmp_path / "pack.json"))
    assert mgr.install({"pack": 1, "id": "x", "name": "a\ud800"}) and mgr.pack is None
