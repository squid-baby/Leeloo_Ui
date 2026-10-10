"""Boot-side WiFi recovery decisions and exit codes (leeloo_boot.py).

Clocks and sleeps are fakes, so nothing here waits in real time or touches
nmcli, the framebuffer, or the network.
"""
import json
import sys
import types

import pytest

import leeloo_boot as boot


class FakeClock:
    """clock()/sleep() pair: sleeping advances the clock instantly"""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def no_display(monkeypatch):
    monkeypatch.setattr(boot, 'show_splash', lambda *a, **k: None)
    monkeypatch.setattr(boot, 'clear_screen', lambda: None)
    monkeypatch.setattr(boot.time, 'sleep', lambda s: None)


# ============================================
# wait_for_wifi / boot_wifi_check
# ============================================

def test_wait_for_wifi_times_out_without_real_sleep():
    fc = FakeClock()
    assert boot.wait_for_wifi(lambda: False, 180, fc.sleep, fc.clock, poll_s=5) is False
    assert fc.now == pytest.approx(180)


def test_wait_for_wifi_returns_when_connected():
    fc = FakeClock()
    assert boot.wait_for_wifi(lambda: fc.now >= 20, 180, fc.sleep, fc.clock, poll_s=5)
    assert fc.now == 20


def test_boot_check_already_connected_does_nothing():
    fc = FakeClock()
    calls = []
    assert boot.boot_wifi_check(lambda: True, lambda: calls.append(1),
                                fc.sleep, fc.clock)
    assert calls == [] and fc.now == 0


def test_boot_check_autoconnect_within_grace():
    fc = FakeClock()
    calls = []
    assert boot.boot_wifi_check(lambda: fc.now >= 30, lambda: calls.append(1) or False,
                                fc.sleep, fc.clock, timeout_s=180, grace_s=45)
    assert calls == []


def test_boot_check_connects_saved_after_grace():
    fc = FakeClock()
    state = {'up': False}

    def connect_saved():
        state['up'] = True
        return True

    assert boot.boot_wifi_check(lambda: state['up'], connect_saved, fc.sleep, fc.clock,
                                timeout_s=180, grace_s=45)
    assert fc.now == pytest.approx(45)


def test_boot_check_gives_up_after_about_three_minutes():
    fc = FakeClock()
    calls = []

    def connect_saved():
        calls.append(fc.now)
        fc.now += 35  # A real attempt takes a while
        return False

    assert not boot.boot_wifi_check(lambda: False, connect_saved, fc.sleep, fc.clock,
                                    timeout_s=180, grace_s=45)
    assert calls == [pytest.approx(45)]
    assert fc.now == pytest.approx(180)


def test_boot_check_survives_connect_errors():
    fc = FakeClock()

    def connect_saved():
        raise RuntimeError("nmcli missing")

    assert not boot.boot_wifi_check(lambda: False, connect_saved, fc.sleep, fc.clock,
                                    timeout_s=60, grace_s=10)


# ============================================
# wifi_recovery_loop
# ============================================

def test_recovery_alternates_until_saved_wifi_returns():
    events = []
    portal_results = iter([False, False])
    retry_results = iter([False, True])

    def run_portal():
        events.append('portal')
        return next(portal_results)

    def retry_saved():
        events.append('retry')
        return next(retry_results)

    result = boot.wifi_recovery_loop(run_portal, retry_saved,
                                     on_retry=lambda: events.append('lcd'),
                                     sleep=lambda s: None, max_rounds=5)
    assert result == 'saved'
    assert events == ['portal', 'lcd', 'retry', 'portal', 'lcd', 'retry']


def test_recovery_stops_when_new_wifi_works():
    events = []
    result = boot.wifi_recovery_loop(lambda: events.append('portal') or True,
                                     lambda: events.append('retry') or True,
                                     sleep=lambda s: None, max_rounds=5)
    assert result == 'portal' and events == ['portal']


def test_recovery_keeps_retrying_when_portal_crashes():
    sleeps = []
    retries = iter([False, True])

    def run_portal():
        raise RuntimeError("flask missing")

    result = boot.wifi_recovery_loop(run_portal, lambda: next(retries),
                                     sleep=sleeps.append, max_rounds=5)
    assert result == 'saved'
    assert sleeps == [boot.WIFI_POLL_S, boot.WIFI_POLL_S]


def test_ensure_wifi_or_recover_runs_wifi_only_portal(tmp_path, monkeypatch):
    cfg = tmp_path / 'device_config.json'
    cfg.write_text(json.dumps({'wifi_ssid': 'Home', 'wifi_password': 'pw',
                               'setup_complete': True, 'user_name': 'Nat'}))
    monkeypatch.setattr(boot, 'DEVICE_CONFIG_PATH', str(cfg))
    checks = iter([False])
    monkeypatch.setattr(boot, 'boot_wifi_check', lambda *a, **k: next(checks))
    monkeypatch.setattr(boot, 'disconnect_wifi_for_setup', lambda: None)
    portal_calls = []

    def fake_portal(recovery=False, idle_timeout_s=None):
        portal_calls.append((recovery, idle_timeout_s))
        return True

    monkeypatch.setattr(boot, 'run_setup_portal', fake_portal)
    assert boot.ensure_wifi_or_recover() == 'portal'
    assert portal_calls == [(True, boot.RECOVERY_PORTAL_WINDOW_S)]
    assert boot.BOOT_WIFI_WAIT_S == 180 and boot.RECOVERY_PORTAL_WINDOW_S == 600


# ============================================
# First-run detection
# ============================================

def test_is_first_run(tmp_path, monkeypatch):
    cfg = tmp_path / 'device_config.json'
    flag = tmp_path / '.first_run_complete'
    monkeypatch.setattr(boot, 'DEVICE_CONFIG_PATH', str(cfg))
    monkeypatch.setattr(boot, 'FIRST_RUN_COMPLETE', str(flag))

    assert boot.is_first_run() is True                      # No config at all
    cfg.write_text(json.dumps({'setup_complete': False, 'user_name': 'Nat'}))
    flag.write_text('1')
    assert boot.is_first_run() is True                      # WiFi never confirmed
    cfg.write_text(json.dumps({'setup_complete': True}))
    flag.unlink()
    assert boot.is_first_run() is False                     # Setup done, flag missing
    assert flag.exists()


# ============================================
# LCD states
# ============================================

def test_lcd_texts_tell_user_what_to_do():
    title, lines = boot.lcd_status_text('wifi_failed', ssid='Home', ap_ssid='LEE-AB12')
    text = ' '.join(lines)
    assert 'wifi failed' in title
    assert 'LEE-AB12' in text and 're-enter the password' in text
    _, lines = boot.lcd_status_text('recovery', ssid='Home', ap_ssid='LEE-AB12', minutes=10)
    assert 'LEE-AB12' in ' '.join(lines) and '10 min' in ' '.join(lines)
    for screen in ('connecting', 'success', 'retrying'):
        assert boot.lcd_status_text(screen, ssid='Home', ap_ssid='LEE-AB12')
    assert boot.lcd_status_text('nonsense') is None


def test_portal_lcd_update_renders_and_never_raises(monkeypatch):
    frames = []
    monkeypatch.setattr(boot, 'write_to_fb', frames.append)
    boot.portal_lcd_update('wifi_failed', ssid='A' * 32, ap_ssid='LEE-AB12')
    assert frames and frames[0].size == (480, 320)

    def broken(_img):
        raise OSError("no /dev/fb1")

    monkeypatch.setattr(boot, 'write_to_fb', broken)
    boot.portal_lcd_update('connecting', ssid='Home')  # Logged, not raised


# ============================================
# Exit codes
# ============================================

def test_boot_error_exits_non_zero():
    def main():
        raise RuntimeError("portal failed")
    assert boot.boot_entry(main) == 1


def test_keyboard_interrupt_exits_zero():
    def main():
        raise KeyboardInterrupt
    assert boot.boot_entry(main) == 0


def test_brain_crash_exits_non_zero(monkeypatch):
    # leeloo_brain.main() swallows crashes and returns: boot must still fail
    fake_brain = types.ModuleType('leeloo_brain')
    fake_brain.main = lambda: None
    monkeypatch.setitem(sys.modules, 'leeloo_brain', fake_brain)
    with pytest.raises(boot.BrainStoppedError):
        boot.run_main_ui()
    assert boot.boot_entry(boot.run_main_ui) == 1


def test_brain_exception_propagates(monkeypatch):
    fake_brain = types.ModuleType('leeloo_brain')

    def crash():
        raise ValueError("boom")

    fake_brain.main = crash
    monkeypatch.setitem(sys.modules, 'leeloo_brain', fake_brain)
    assert boot.boot_entry(boot.run_main_ui) == 1


def test_first_run_portal_failure_exits_non_zero(monkeypatch):
    monkeypatch.setattr(boot, 'is_first_run', lambda: True)
    monkeypatch.setattr(boot, 'disconnect_wifi_for_setup', lambda: None)
    monkeypatch.setattr(boot, 'run_first_run_screen', lambda: True)

    def portal_crash(recovery=False, idle_timeout_s=None):
        raise ImportError("No module named 'flask'")

    monkeypatch.setattr(boot, 'run_setup_portal', portal_crash)
    assert boot.boot_entry() == 1


def test_service_restarts_on_failure():
    import os
    path = os.path.join(os.path.dirname(boot.__file__), 'boot', 'leeloo.service')
    with open(path) as f:
        service = f.read()
    assert 'Restart=on-failure' in service
    assert 'StartLimitIntervalSec=0' in service
