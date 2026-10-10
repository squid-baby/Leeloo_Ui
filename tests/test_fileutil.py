"""Tests for leeloo_fileutil.atomic_write_json"""
import json
import os
import stat

import pytest

from leeloo_fileutil import atomic_write_json


def test_writes_json(tmp_path):
    p = tmp_path / "a.json"
    atomic_write_json(str(p), {"x": 1})
    assert json.loads(p.read_text()) == {"x": 1}


def test_creates_missing_directory(tmp_path):
    p = tmp_path / "sub" / "a.json"
    atomic_write_json(str(p), [1, 2])
    assert json.loads(p.read_text()) == [1, 2]


def test_keeps_existing_permissions(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{}")
    os.chmod(p, 0o600)
    atomic_write_json(str(p), {"y": 2})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_explicit_mode_for_new_file(tmp_path):
    p = tmp_path / "secret.json"
    atomic_write_json(str(p), {}, mode=0o600)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_failed_write_keeps_old_file_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"old": true}')
    with pytest.raises(TypeError):
        atomic_write_json(str(p), {"bad": object()})  # not JSON-serialisable
    assert json.loads(p.read_text()) == {"old": True}
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]
