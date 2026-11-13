"""gadget_data tests: hang storage, time zones, caching, atomic writes."""
import builtins
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import gadget_data

EMPTY = {'date_str': '_/__', 'time_str': '__:__', 'countdown_str': '__:__', 'slider_boxes': 0}
NY = ZoneInfo('America/New_York')
LA = ZoneInfo('America/Los_Angeles')


@pytest.fixture(autouse=True)
def data_file(tmp_path, monkeypatch):
    path = tmp_path / 'leeloo_data.json'
    monkeypatch.setattr(gadget_data, 'DATA_FILE_PATH', str(path))
    gadget_data.set_local_tz(None)
    yield path
    gadget_data.set_local_tz(None)


@pytest.fixture
def count_reads(monkeypatch, data_file):
    reads = []
    real_open = builtins.open

    def counting_open(path, mode='r', *a, **kw):
        if str(path) == str(data_file) and 'r' in mode:
            reads.append(path)
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(builtins, 'open', counting_open)
    return reads


def write_raw(path, data, bump=True):
    path.write_text(json.dumps(data))
    if bump:  # make sure mtime differs even on coarse clocks
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_env_path_is_used():
    import importlib
    assert os.environ['LEELOO_DATA_PATH']
    # module default comes from the env var set in conftest
    src_default = importlib.import_module('gadget_data')
    assert hasattr(src_default, 'DATA_FILE_PATH')


def test_empty_state_without_file(data_file):
    assert gadget_data.load_data() == {}
    assert gadget_data.get_next_hang() is None
    assert gadget_data.format_countdown_display() == EMPTY
    assert not data_file.exists()


def test_corrupt_file_is_empty(data_file):
    data_file.write_text('{not json')
    assert gadget_data.load_data() == {}
    assert gadget_data.format_countdown_display() == EMPTY
    data_file.write_text('[1, 2]')
    os.utime(data_file, ns=(0, 10**18))
    assert gadget_data.load_data() == {}


def test_bad_datetime_is_empty(data_file):
    write_raw(data_file, {'next_hang': {'datetime': 'tomorrow-ish'}})
    assert gadget_data.format_countdown_display() == EMPTY
    write_raw(data_file, {'next_hang': {'set_by': 'x'}})
    assert gadget_data.format_countdown_display() == EMPTY


def test_set_naive_stores_isoformat_unchanged(data_file):
    dt = datetime(2031, 3, 5, 19, 30)
    assert gadget_data.set_next_hang(dt, 'voice') is True
    stored = json.loads(data_file.read_text())['next_hang']
    assert stored['datetime'] == '2031-03-05T19:30:00'
    assert stored['set_by'] == 'voice'
    assert 'created_at' in stored
    assert gadget_data.get_next_hang()['datetime'] == dt.isoformat()


def test_set_aware_stays_aware(data_file):
    dt = datetime(2031, 3, 5, 19, 30, tzinfo=NY)
    gadget_data.set_next_hang(dt, 'telegram')
    stored = json.loads(data_file.read_text())['next_hang']['datetime']
    assert stored == dt.isoformat() == '2031-03-05T19:30:00-05:00'
    assert datetime.fromisoformat(stored).utcoffset() == timedelta(hours=-5)


def test_naive_countdown_and_display():
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'test')
    now = datetime(2031, 3, 3, 14, 45)
    out = gadget_data.format_countdown_display(now=now)
    assert out == {'date_str': '3/05', 'time_str': '7:05pm', 'countdown_str': '2d:4h', 'slider_boxes': 3}


def test_countdown_under_a_day_and_formats():
    gadget_data.set_next_hang(datetime(2031, 3, 5, 0, 30), 'test')
    out = gadget_data.format_countdown_display(now=datetime(2031, 3, 4, 21, 25))
    assert out['countdown_str'] == '3h:05m'
    assert out['time_str'] == '12:30am'
    assert out['date_str'] == '3/05'
    assert out['slider_boxes'] == 0

    gadget_data.set_next_hang(datetime(2031, 12, 25, 12, 0), 'test')
    out = gadget_data.format_countdown_display(now=datetime(2031, 12, 1, 12, 0))
    assert out['countdown_str'] == '24d:0h'
    assert out['time_str'] == '12:00pm'
    assert out['date_str'] == '12/25'
    assert out['slider_boxes'] == 12       # capped


def test_default_now_uses_real_clock():
    gadget_data.set_next_hang(datetime.now() + timedelta(days=2, hours=4, minutes=20), 'test')
    assert gadget_data.format_countdown_display()['countdown_str'] == '2d:4h'
    gadget_data.set_next_hang(datetime.now(timezone.utc) + timedelta(hours=5, minutes=30), 'test')
    assert gadget_data.format_countdown_display()['countdown_str'] in ('5h:29m', '5h:30m')


def test_aware_hang_shown_in_local_zone():
    # 7:30pm New York == 4:30pm Los Angeles, same instant
    gadget_data.set_next_hang(datetime(2031, 7, 4, 19, 30, tzinfo=NY), 'telegram')
    now = datetime(2031, 7, 4, 12, 0, tzinfo=timezone.utc)   # 11.5h before

    gadget_data.set_local_tz(LA)
    la = gadget_data.format_countdown_display(now=now)
    gadget_data.set_local_tz(NY)
    ny = gadget_data.format_countdown_display(now=now)

    assert la['time_str'] == '4:30pm' and la['date_str'] == '7/04'
    assert ny['time_str'] == '7:30pm' and ny['date_str'] == '7/04'
    assert la['countdown_str'] == ny['countdown_str'] == '11h:30m'


def test_aware_hang_date_rolls_over_in_other_zone():
    gadget_data.set_next_hang(datetime(2031, 7, 4, 23, 30, tzinfo=LA), 'telegram')
    gadget_data.set_local_tz(ZoneInfo('Europe/Berlin'))
    out = gadget_data.format_countdown_display(now=datetime(2031, 7, 4, 0, 0, tzinfo=timezone.utc))
    assert out['date_str'] == '7/05' and out['time_str'] == '8:30am'
    assert out['countdown_str'] == '1d:6h'


def test_naive_hang_is_local_wall_time_in_configured_zone():
    gadget_data.set_local_tz(LA)
    gadget_data.set_next_hang(datetime(2031, 7, 4, 19, 30), 'voice')     # legacy naive
    # aware "now" given in UTC: 19:30 PDT == 02:30 UTC next day
    out = gadget_data.format_countdown_display(now=datetime(2031, 7, 4, 23, 30, tzinfo=timezone.utc))
    assert out['time_str'] == '7:30pm' and out['date_str'] == '7/04'
    assert out['countdown_str'] == '3h:00m'
    # naive "now" is local wall time as well
    out = gadget_data.format_countdown_display(now=datetime(2031, 7, 4, 18, 0))
    assert out['countdown_str'] == '1h:30m'


def test_mixed_naive_now_with_aware_hang():
    gadget_data.set_local_tz(NY)
    gadget_data.set_next_hang(datetime(2031, 7, 4, 16, 30, tzinfo=LA), 'telegram')
    out = gadget_data.format_countdown_display(now=datetime(2031, 7, 4, 18, 30))   # NY wall time
    assert out['countdown_str'] == '1h:00m'
    assert out['time_str'] == '7:30pm'


def test_set_local_tz_none_is_system_local():
    gadget_data.set_local_tz(NY)
    gadget_data.set_local_tz(None)
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'test')
    out = gadget_data.format_countdown_display(now=datetime(2031, 3, 5, 18, 5))
    assert out['time_str'] == '7:05pm' and out['countdown_str'] == '1h:00m'


def test_past_hang_auto_clears(data_file):
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'test')
    assert gadget_data.format_countdown_display(now=datetime(2031, 3, 5, 19, 6)) == EMPTY
    assert gadget_data.get_next_hang() is None
    assert 'next_hang' not in json.loads(data_file.read_text())

    gadget_data.set_next_hang(datetime(2020, 1, 1, tzinfo=timezone.utc), 'test')
    assert gadget_data.format_countdown_display() == EMPTY
    assert gadget_data.get_next_hang() is None


def test_clear_keeps_other_keys(data_file):
    gadget_data.save_data({'other': 1})
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'test')
    assert gadget_data.clear_next_hang() is True
    assert json.loads(data_file.read_text()) == {'other': 1}
    assert gadget_data.clear_next_hang() is True       # idempotent


def test_file_not_reread_when_unchanged(data_file, count_reads):
    write_raw(data_file, {'next_hang': {'datetime': '2031-03-05T19:05:00', 'set_by': 'x'}})
    now = datetime(2031, 3, 3, 14, 45)
    first = gadget_data.format_countdown_display(now=now)
    for _ in range(50):
        assert gadget_data.format_countdown_display(now=now) == first
        gadget_data.get_next_hang()
        gadget_data.load_data()
    assert len(count_reads) == 1


def test_external_change_is_picked_up(data_file, count_reads):
    write_raw(data_file, {'next_hang': {'datetime': '2031-03-05T19:05:00'}})
    now = datetime(2031, 3, 3, 14, 45)
    assert gadget_data.format_countdown_display(now=now)['date_str'] == '3/05'
    write_raw(data_file, {'next_hang': {'datetime': '2031-03-09T08:00:00'}})
    out = gadget_data.format_countdown_display(now=now)
    assert out['date_str'] == '3/09' and out['time_str'] == '8:00am'
    assert len(count_reads) == 2
    data_file.unlink()
    assert gadget_data.format_countdown_display(now=now) == EMPTY


def test_own_writes_refresh_cache_without_reread(data_file, count_reads):
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'a')
    gadget_data.set_next_hang(datetime(2031, 3, 6, 9, 0), 'b')
    out = gadget_data.format_countdown_display(now=datetime(2031, 3, 5, 9, 0))
    assert out['date_str'] == '3/06' and out['countdown_str'] == '1d:0h'
    assert gadget_data.get_next_hang()['set_by'] == 'b'
    assert len(count_reads) == 0


def test_returned_data_is_a_copy():
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'a')
    gadget_data.load_data()['next_hang']['datetime'] = 'garbage'
    gadget_data.get_next_hang()['datetime'] = 'garbage'
    assert gadget_data.get_next_hang()['datetime'] == '2031-03-05T19:05:00'


def test_monkeypatched_path_switches_files(tmp_path, monkeypatch):
    gadget_data.set_next_hang(datetime(2031, 3, 5, 19, 5), 'a')
    monkeypatch.setattr(gadget_data, 'DATA_FILE_PATH', str(tmp_path / 'other.json'))
    assert gadget_data.get_next_hang() is None
    gadget_data.save_data({'k': 'v'})
    assert gadget_data.load_data() == {'k': 'v'}


def test_save_is_atomic(data_file, monkeypatch):
    gadget_data.save_data({'keep': 'me'})
    replaced = []
    real_replace = os.replace

    def spy(src, dst, *a, **kw):
        # the complete new content is already in the temp file, old file intact
        assert json.loads(open(src).read()) == {'new': 1}
        assert json.loads(open(dst).read()) == {'keep': 'me'}
        assert os.path.dirname(src) == os.path.dirname(dst)
        replaced.append((src, dst))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, 'replace', spy)
    assert gadget_data.save_data({'new': 1}) is True
    assert replaced and replaced[0][1] == str(data_file)
    assert json.loads(data_file.read_text()) == {'new': 1}
    assert os.listdir(data_file.parent) == [data_file.name]     # no temp files left


def test_failed_save_keeps_old_file(data_file, monkeypatch):
    gadget_data.save_data({'keep': 'me'})

    def fail(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, 'replace', fail)
    assert gadget_data.save_data({'new': 1}) is False
    monkeypatch.undo()
    monkeypatch.setattr(gadget_data, 'DATA_FILE_PATH', str(data_file))
    assert json.loads(data_file.read_text()) == {'keep': 'me'}
    assert gadget_data.load_data() == {'keep': 'me'}
    assert os.listdir(data_file.parent) == [data_file.name]


def test_unserialisable_save_fails_cleanly(data_file):
    gadget_data.save_data({'keep': 'me'})
    assert gadget_data.save_data({'bad': object()}) is False
    assert gadget_data.load_data() == {'keep': 'me'}
    assert os.listdir(data_file.parent) == [data_file.name]
