#!/usr/bin/env python3
"""
LEELOO packs - device side (format: docs/PACKS.md, section 1).

A pack is DATA, not code: one JSON object that changes the colours, the
header, the box labels and what the weather box shows. This module checks a
pack, stores the active one and works out which panel shows now.

Pure Python: no network, no PIL. The relay fetches the data sources and
sends the values (`pack_data`); the device only draws them.
"""

import copy
import json
import math
import os
import re
import tempfile
import threading
import time

MAX_PACK_BYTES = 8000

DEFAULT_ROTATE_SECONDS = 20
MAX_VALUE_CHARS = 40
MAX_VALUES = 64            # 3 sources x 12 fields = 36 in a valid pack
MAX_VALUE_KEY_CHARS = 40   # "source_id.field" is 33 at most

COLOR_KEYS = ('bg', 'green', 'purple', 'lavender', 'tan', 'rose', 'white', 'yellow', 'orange')
LABEL_KEYS = ('weather', 'time', 'messages', 'album')

_TOP_KEYS = ('pack', 'id', 'name', 'theme', 'sources', 'panels', 'rotate_seconds')
_THEME_KEYS = ('colors', 'header', 'labels')
_SOURCE_KEYS = ('id', 'url', 'every', 'fields')
_BUILTIN_PANEL_KEYS = ('slot', 'builtin', 'when', 'priority')
_CUSTOM_PANEL_KEYS = ('slot', 'title', 'rows', 'when', 'priority')
_ROW_KEYS = ('left', 'right', 'bar')
_BAR_KEYS = ('value', 'max')
_WHEN_OPS = ('equals', 'not_equals', 'exists')

_PACK_ID_RE = re.compile(r'[a-z0-9-]{1,32}')
_NAME_RE = re.compile(r'[a-z][a-z0-9_]{0,15}')      # source id, field key
_COLOR_RE = re.compile(r'#[0-9A-Fa-f]{6}')
_TEMPLATE_RE = re.compile(r'\{([A-Za-z0-9_.]+)\}')


# ----------------------------------------------------------------- validate

def _is_number(v):
    """A JSON number (bool is not a number; NaN / Infinity are not JSON)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_integer(v):
    return _is_number(v) and float(v).is_integer()


def _unknown_keys(obj, allowed, path, errors):
    for key in obj:
        if key not in allowed:
            errors.append(f"{path + '.' if path else ''}{key}: unknown key")


def _check_text(value, path, errors, min_len, max_len, template=False):
    """A string of min_len..max_len characters. Returns True when valid.

    template=True: `{source.field}` placeholders do not count. The limit is
    for the fixed text (the example pack has "{game.home_score}", 17
    characters, as a `right` text with a limit of 14). The drawn text is cut
    to the width of the box in any case.
    """
    if not isinstance(value, str):
        errors.append(f"{path}: not a string")
        return False
    counted = _TEMPLATE_RE.sub('', value) if template else value
    if len(counted) > max_len:
        errors.append(f"{path}: longer than {max_len} characters")
        return False
    if len(value) < min_len:
        errors.append(f"{path}: empty")
        return False
    return True


def _check_range(value, path, errors, low, high, integer=False):
    if not (_is_integer(value) if integer else _is_number(value)):
        errors.append(f"{path}: not {'an integer' if integer else 'a number'}")
    elif not low <= value <= high:
        errors.append(f"{path}: not in {low} to {high}")


def _check_theme(theme, errors):
    if not isinstance(theme, dict):
        errors.append("theme: not an object")
        return
    _unknown_keys(theme, _THEME_KEYS, "theme", errors)

    if 'colors' in theme:
        colors = theme['colors']
        if not isinstance(colors, dict):
            errors.append("theme.colors: not an object")
        else:
            for key, value in colors.items():
                path = f"theme.colors.{key}"
                if key not in COLOR_KEYS:
                    errors.append(f"{path}: unknown key")
                elif not isinstance(value, str) or not _COLOR_RE.fullmatch(value):
                    errors.append(f"{path}: not a #RRGGBB colour")

    if 'header' in theme:
        _check_text(theme['header'], "theme.header", errors, 1, 12)

    if 'labels' in theme:
        labels = theme['labels']
        if not isinstance(labels, dict):
            errors.append("theme.labels: not an object")
        else:
            for key, value in labels.items():
                path = f"theme.labels.{key}"
                if key not in LABEL_KEYS:
                    errors.append(f"{path}: unknown key")
                else:
                    _check_text(value, path, errors, 1, 10)


def _check_source(source, path, errors, seen_ids):
    if not isinstance(source, dict):
        errors.append(f"{path}: not an object")
        return
    _unknown_keys(source, _SOURCE_KEYS, path, errors)
    for key in _SOURCE_KEYS:
        if key not in source:
            errors.append(f"{path}.{key}: missing")

    if 'id' in source:
        sid = source['id']
        if not isinstance(sid, str) or not _NAME_RE.fullmatch(sid):
            errors.append(f"{path}.id: must match ^[a-z][a-z0-9_]{{0,15}}$")
        elif sid in seen_ids:
            errors.append(f"{path}.id: used more than once")
        else:
            seen_ids.add(sid)

    if 'url' in source:
        url = source['url']
        if not isinstance(url, str):
            errors.append(f"{path}.url: not a string")
        elif not url.startswith("https://") or len(url) <= len("https://"):
            errors.append(f"{path}.url: must start with https://")

    if 'every' in source:
        _check_range(source['every'], f"{path}.every", errors, 30, 3600)

    if 'fields' in source:
        fields = source['fields']
        if not isinstance(fields, dict):
            errors.append(f"{path}.fields: not an object")
        else:
            if not 1 <= len(fields) <= 12:
                errors.append(f"{path}.fields: must have 1 to 12 entries")
            for key, value in fields.items():
                fpath = f"{path}.fields.{key}"
                if not isinstance(key, str) or not _NAME_RE.fullmatch(key):
                    errors.append(f"{fpath}: key must match ^[a-z][a-z0-9_]{{0,15}}$")
                else:
                    _check_text(value, fpath, errors, 0, 200)


def _check_when(when, path, errors):
    if not isinstance(when, dict):
        errors.append(f"{path}: not an object")
        return
    _unknown_keys(when, ('field',) + _WHEN_OPS, path, errors)
    if 'field' not in when:
        errors.append(f"{path}.field: missing")
    elif not isinstance(when['field'], str) or not when['field']:
        errors.append(f"{path}.field: not a string")

    ops = [op for op in _WHEN_OPS if op in when]
    if len(ops) != 1:
        errors.append(f"{path}: needs exactly one of equals, not_equals, exists")
    for op in ops:
        if op == 'exists':
            if not isinstance(when[op], bool):
                errors.append(f"{path}.exists: not true or false")
        elif not isinstance(when[op], str):
            errors.append(f"{path}.{op}: not a string")


def _check_row(row, path, errors):
    if not isinstance(row, dict):
        errors.append(f"{path}: not an object")
        return
    _unknown_keys(row, _ROW_KEYS, path, errors)
    if 'left' not in row:
        errors.append(f"{path}.left: missing")
    else:
        _check_text(row['left'], f"{path}.left", errors, 0, 28, template=True)
    if 'right' in row:
        _check_text(row['right'], f"{path}.right", errors, 0, 14, template=True)
    if 'right' in row and 'bar' in row:
        errors.append(f"{path}: has right and bar (use one)")
    if 'bar' in row:
        bar = row['bar']
        bpath = f"{path}.bar"
        if not isinstance(bar, dict):
            errors.append(f"{bpath}: not an object")
            return
        _unknown_keys(bar, _BAR_KEYS, bpath, errors)
        if 'value' not in bar:
            errors.append(f"{bpath}.value: missing")
        elif not isinstance(bar['value'], str):
            errors.append(f"{bpath}.value: not a string")
        if 'max' not in bar:
            errors.append(f"{bpath}.max: missing")
        elif not _is_number(bar['max']) or bar['max'] <= 0:
            errors.append(f"{bpath}.max: not a number above 0")


def _check_panel(panel, path, errors):
    if not isinstance(panel, dict):
        errors.append(f"{path}: not an object")
        return
    builtin = 'builtin' in panel
    if builtin and ('rows' in panel or 'title' in panel):
        errors.append(f"{path}: has builtin and rows/title (use one form)")
    _unknown_keys(panel, _BUILTIN_PANEL_KEYS + _CUSTOM_PANEL_KEYS, path, errors)

    if 'slot' not in panel:
        errors.append(f"{path}.slot: missing")
    elif panel['slot'] != 'weather':
        errors.append(f"{path}.slot: must be \"weather\"")

    if builtin:
        if panel['builtin'] != 'weather':
            errors.append(f"{path}.builtin: must be \"weather\"")
    else:
        if 'title' in panel:
            _check_text(panel['title'], f"{path}.title", errors, 1, 10)
        if 'rows' not in panel:
            errors.append(f"{path}.rows: missing (a panel needs builtin or rows)")
        else:
            rows = panel['rows']
            if not isinstance(rows, list):
                errors.append(f"{path}.rows: not an array")
            else:
                if not 1 <= len(rows) <= 3:
                    errors.append(f"{path}.rows: must have 1 to 3 rows")
                for i, row in enumerate(rows[:3]):
                    _check_row(row, f"{path}.rows[{i}]", errors)

    if 'when' in panel:
        _check_when(panel['when'], f"{path}.when", errors)
    if 'priority' in panel:
        _check_range(panel['priority'], f"{path}.priority", errors, -100, 100, integer=True)


def validate_pack(pack) -> list:
    """Check a pack against docs/PACKS.md section 1.

    Returns a list of error strings, [] when the pack is valid. Each error
    names the path of the problem, e.g.
    "panels[1].rows[0].left: longer than 28 characters".

    The relay's host allowlist is NOT checked here (the relay fetches the
    URLs, not the device); `https://` is still required. Never raises.
    """
    if not isinstance(pack, dict):
        return ["pack: not an object"]
    try:
        # Same text the relay measures: compact JSON, UTF-8
        text = json.dumps(pack, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        size = len(text.encode('utf-8', 'surrogatepass'))
    except (TypeError, ValueError, RecursionError):
        return ["pack: not JSON data"]
    if size > MAX_PACK_BYTES:
        return [f"pack: larger than {MAX_PACK_BYTES} bytes"]

    errors = []
    _unknown_keys(pack, _TOP_KEYS, "", errors)

    version = pack.get('pack')
    if not _is_number(version) or version != 1:
        errors.append("pack: must be 1")

    pid = pack.get('id')
    if not isinstance(pid, str) or not _PACK_ID_RE.fullmatch(pid):
        errors.append("id: must match ^[a-z0-9-]{1,32}$")

    if 'name' not in pack:
        errors.append("name: missing")
    else:
        _check_text(pack['name'], "name", errors, 1, 24)

    if 'theme' in pack:
        _check_theme(pack['theme'], errors)

    if 'sources' in pack:
        sources = pack['sources']
        if not isinstance(sources, list):
            errors.append("sources: not an array")
        else:
            if len(sources) > 3:
                errors.append("sources: more than 3")
            seen = set()
            for i, source in enumerate(sources[:3]):
                _check_source(source, f"sources[{i}]", errors, seen)

    if 'panels' in pack:
        panels = pack['panels']
        if not isinstance(panels, list):
            errors.append("panels: not an array")
        else:
            if len(panels) > 6:
                errors.append("panels: more than 6")
            for i, panel in enumerate(panels[:6]):
                _check_panel(panel, f"panels[{i}]", errors)

    if 'rotate_seconds' in pack:
        _check_range(pack['rotate_seconds'], "rotate_seconds", errors, 5, 600)

    return errors


# ------------------------------------------------------ templates, conditions

def render_template(text, values) -> str:
    """Put values into a template: "{game.home} {game.home_score}".

    A value that is not there shows as "--". Text without `{name}` parts is
    returned unchanged (a lone brace is ordinary text). Never raises.
    """
    if not isinstance(text, str):
        return "" if text is None else str(text)
    if '{' not in text:
        return text
    if not isinstance(values, dict):
        values = {}

    def put(match):
        value = values.get(match.group(1))
        return "--" if value is None else str(value)

    return _TEMPLATE_RE.sub(put, text)


def condition_true(when, values) -> bool:
    """Is the `when` condition of a panel true? None = no condition = True.

    equals / not_equals compare as strings. A value that is not there is
    never `equals` and always `not_equals`. Never raises.
    """
    if when is None:
        return True
    if not isinstance(when, dict):
        return False
    if not isinstance(values, dict):
        values = {}
    field = when.get('field')
    value = values.get(field) if isinstance(field, str) else None
    if 'equals' in when:
        return value is not None and str(value) == str(when['equals'])
    if 'not_equals' in when:
        return value is None or str(value) != str(when['not_equals'])
    if 'exists' in when:
        return (value is not None) == bool(when['exists'])
    return False


def _value_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)[:MAX_VALUE_CHARS]


def _bar_fraction(text, maximum):
    """Rendered bar value -> 0..1 (not a number -> 0)."""
    try:
        number = float(str(text).strip())
        fraction = number / float(maximum)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return 0.0
    if not math.isfinite(fraction):
        return 0.0
    return min(1.0, max(0.0, fraction))


# ------------------------------------------------------------- PackManager

class PackManager:
    """The active pack of this device and the latest source values.

    `current_panel` / `signature` / `theme_colors` / `header` / `labels` may
    be called from the render worker thread while `install` / `set_values` /
    `reset` run on the event loop: all state is guarded by a lock, and no
    internal mutable object is ever returned.
    """

    def __init__(self, path):
        """path: JSON file that stores the active pack (e.g.
        LEELOO_HOME/pack.json). An invalid or unreadable file means no pack;
        the file is left alone."""
        self._path = os.fspath(path)
        self._lock = threading.RLock()       # state
        self._io_lock = threading.Lock()     # one file writer at a time
        self._pack = None
        self._values = {}
        self._pack = self._load()

    def _load(self):
        try:
            if os.path.getsize(self._path) > MAX_PACK_BYTES * 4:
                return None
            with open(self._path, 'r', encoding='utf-8') as f:
                pack = json.load(f)
        except (OSError, ValueError, RecursionError):
            return None
        if validate_pack(pack):
            return None
        return pack

    # -- state ------------------------------------------------------------

    @property
    def pack(self):
        """The active pack (a copy) or None."""
        with self._lock:
            return copy.deepcopy(self._pack)

    @property
    def values(self):
        """Source values "source.field" -> str (a copy; not persisted)."""
        with self._lock:
            return dict(self._values)

    def install(self, pack) -> list:
        """Validate and make `pack` the active pack. Returns [] on success
        (stored atomically, values cleared). On failure returns the errors
        and keeps the old pack."""
        errors = validate_pack(pack)
        if errors:
            return errors
        pack = copy.deepcopy(pack)
        text = json.dumps(pack, separators=(',', ':'), ensure_ascii=False)
        with self._io_lock:
            try:
                self._write(text)
            except (OSError, ValueError) as e:
                return [f"storage: could not save the pack ({e})"]
            with self._lock:
                self._pack = pack
                self._values = {}
        return []

    def _write(self, text):
        """Temp file in the same directory + os.replace: the file on disk is
        always a complete pack (the old one or the new one)."""
        directory = os.path.dirname(self._path) or '.'
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.pack-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def reset(self):
        """Remove the pack and its file (back to the default look)."""
        with self._io_lock:
            with self._lock:
                self._pack = None
                self._values = {}
            try:
                os.remove(self._path)
            except OSError:
                pass

    def set_values(self, pack_id, values) -> bool:
        """Take source values from the relay (`pack_data`).

        Ignored when pack_id is not the active pack or values is not a dict.
        The message holds ALL values of the pack (a value that is not in it
        is gone). Only string keys are kept; values become strings of 40
        characters at most. Returns True if something changed.
        """
        if not isinstance(values, dict):
            return False
        with self._lock:
            if self._pack is None or pack_id != self._pack.get('id'):
                return False
            new = {}
            for key, value in values.items():
                if not isinstance(key, str) or not key or len(key) > MAX_VALUE_KEY_CHARS:
                    continue
                if value is None or isinstance(value, (dict, list, tuple, set)):
                    continue            # no value
                if len(new) >= MAX_VALUES:
                    break
                new[key] = _value_text(value)
            if new == self._values:
                return False
            self._values = new
            return True

    # -- look -------------------------------------------------------------

    def _theme(self):
        theme = self._pack.get('theme') if self._pack else None
        return theme if isinstance(theme, dict) else {}

    def theme_colors(self) -> dict:
        """{} or the validated colours of the pack (a copy)."""
        with self._lock:
            return dict(self._theme().get('colors') or {})

    def header(self):
        """Header text of the pack, or None."""
        with self._lock:
            return self._theme().get('header')

    def labels(self) -> dict:
        """{} or the box labels of the pack (a copy)."""
        with self._lock:
            return dict(self._theme().get('labels') or {})

    # -- panels -----------------------------------------------------------

    def _panel_state(self, now):
        """(panel index or None, rendered panel or None). Lock is held."""
        pack = self._pack
        panels = pack.get('panels') if pack else None
        if not panels:
            return None, None
        values = self._values
        live = [(i, p) for i, p in enumerate(panels) if condition_true(p.get('when'), values)]
        if not live:
            return None, None
        top = max(p.get('priority', 0) for _, p in live)
        live = [(i, p) for i, p in live if p.get('priority', 0) == top]

        if len(live) > 1:
            if now is None:
                now = time.time()
            rotate = pack.get('rotate_seconds', DEFAULT_ROTATE_SECONDS)
            try:
                turn = int(math.floor(float(now) / float(rotate)))
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                turn = 0
            index, panel = live[turn % len(live)]
        else:
            index, panel = live[0]

        if 'builtin' in panel:
            return index, None
        rows = []
        for row in panel.get('rows', ())[:3]:
            bar = row.get('bar')
            rows.append({
                'left': render_template(row.get('left', ''), values),
                'right': render_template(row['right'], values) if 'right' in row else None,
                'bar': (_bar_fraction(render_template(bar.get('value'), values), bar.get('max'))
                        if isinstance(bar, dict) else None),
            })
        return index, {'title': panel.get('title'), 'rows': rows}

    def current_panel(self, now=None):
        """What the weather box shows now ("Which panel shows" rule).

        None = the built-in weather. Else
        {"title": str|None, "rows": [{"left": str, "right": str|None,
        "bar": float|None}]} with the templates rendered and `bar` as a
        fraction 0..1. `now` is unix seconds (None = the clock); the result
        is the same for the same `now`.
        """
        with self._lock:
            return self._panel_state(now)[1]

    def signature(self, now=None):
        """Small hashable value that changes exactly when the drawn result
        would change: pack id, theme (colours, header, labels) and the
        content of the current panel."""
        with self._lock:
            if self._pack is None:
                return None
            theme = self._theme()
            _, panel = self._panel_state(now)
            if panel is None:
                shown = None
            else:
                shown = (panel['title'],
                         tuple((r['left'], r['right'], r['bar']) for r in panel['rows']))
            return (
                self._pack.get('id'),
                tuple(sorted((theme.get('colors') or {}).items())),
                theme.get('header'),
                tuple(sorted((theme.get('labels') or {}).items())),
                shown,
            )
