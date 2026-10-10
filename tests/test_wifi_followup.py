"""WiFi follow-up fixes (behaviour on a real Pi), tested with fakes.

No Pi, nmcli, hostapd or network needed: subprocess / nmcli calls go to
fakes that record every argv.

1. AP teardown only when hostapd runs; wlan0 raised + managed before connecting
2. Network scan before NetworkManager is stopped; typed (hidden) SSIDs
3. A password nmcli won't store is a failed attempt; portal password rules
4. nmcli without sudo, with its own --wait; failed connects leave no profile
5. Power-safe config writes
6. setup script only passes --break-system-packages to a pip that knows it
"""
import json
import os
import re
import shutil
import subprocess
import types

import pytest

import connect_saved_wifi as csw
import wifi_manager as wm
from test_wifi_setup import FakeClock, FakeNM, no_sleep

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LINK_UP = ['ip', 'link', 'set', 'wlan0', 'up']
MANAGED = ['nmcli', 'device', 'set', 'wlan0', 'managed', 'yes']


class FakeSystem:
    """Answers systemctl/pgrep and records every argv (run(argv) -> (ok, out, err))"""

    def __init__(self, hostapd_active=False, hostapd_process=False, nm_active=True):
        self.hostapd_active = hostapd_active
        self.hostapd_process = hostapd_process
        self.nm_active = nm_active
        self.calls = []

    def __call__(self, argv, timeout=30):
        self.calls.append(list(argv))
        if argv == ['systemctl', 'is-active', '--quiet', 'hostapd']:
            return self.hostapd_active, "", ""
        if argv == ['pgrep', '-x', 'hostapd']:
            return self.hostapd_process, "", ""
        if argv == ['systemctl', 'is-active', '--quiet', 'NetworkManager']:
            return self.nm_active, "", ""
        if argv == ['systemctl', 'start', 'NetworkManager']:
            self.nm_active = True
        return True, "", ""


# ============================================
# 1. AP teardown only when hostapd is really up
# ============================================

def test_stop_ap_mode_leaves_wlan0_alone_when_hostapd_not_running(monkeypatch):
    """Boot connect / retry_saved with NM already up: no flush + link down"""
    teardowns = []
    monkeypatch.setattr(csw, '_wm_stop_ap_mode', lambda: teardowns.append(1))
    system = FakeSystem(hostapd_active=False, hostapd_process=False, nm_active=True)
    csw.stop_ap_mode(run=system, sleep=no_sleep)
    assert teardowns == []
    assert ['killall', 'hostapd'] not in system.calls
    assert ['systemctl', 'start', 'NetworkManager'] not in system.calls
    # The link is still raised and handed to NM (no-ops when already so)
    assert LINK_UP in system.calls and MANAGED in system.calls
    assert not any('down' in c for c in system.calls)


def test_switch_to_wifi_does_not_tear_down_when_ap_is_not_up(monkeypatch):
    teardowns, connects = [], []
    monkeypatch.setattr(csw, '_wm_stop_ap_mode', lambda: teardowns.append(1))
    monkeypatch.setattr(csw.time, 'sleep', no_sleep)
    monkeypatch.setattr(csw, 'connect_with_networkmanager',
                        lambda ssid, pw, timeout_s=None: connects.append(ssid) or True)
    calls = []

    def fake_subprocess_run(argv, **kwargs):
        calls.append(list(argv))
        # hostapd inactive / no process; NetworkManager active
        rc = 0 if 'NetworkManager' in argv and 'is-active' in argv else 3
        if argv[:1] != ['systemctl'] and argv[:1] != ['pgrep']:
            rc = 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(csw.subprocess, 'run', fake_subprocess_run)
    assert csw.switch_to_wifi('Home', 'password1', timeout_s=60)
    assert teardowns == [] and connects == ['Home']
    assert ['ip', 'link', 'set', 'wlan0', 'down'] not in calls
    assert LINK_UP in calls


@pytest.mark.parametrize('service, process', [(True, False), (False, True)])
def test_stop_ap_mode_tears_down_running_ap_then_raises_link(monkeypatch, service, process):
    """hostapd as a service, or started directly by the start_ap_mode fallback"""
    teardowns = []
    monkeypatch.setattr(csw, '_wm_stop_ap_mode', lambda: teardowns.append(1))
    system = FakeSystem(hostapd_active=service, hostapd_process=process, nm_active=False)
    csw.stop_ap_mode(run=system, sleep=no_sleep)
    assert teardowns == [1]
    assert ['killall', 'hostapd'] in system.calls
    start = system.calls.index(['systemctl', 'start', 'NetworkManager'])
    # NM (re)started, THEN the link raised and managed
    assert system.calls.index(LINK_UP) > start
    assert system.calls.index(MANAGED) > start


def test_restore_then_retry_is_idempotent(monkeypatch):
    """restore_client_mode() then switch_to_wifi(): second pass touches nothing"""
    teardowns = []
    monkeypatch.setattr(csw, '_wm_stop_ap_mode', lambda: teardowns.append(1))
    system = FakeSystem(hostapd_active=True, nm_active=False)
    csw.stop_ap_mode(run=system, sleep=no_sleep)
    system.hostapd_active = False          # The teardown stopped it
    system.calls.clear()
    csw.stop_ap_mode(run=system, sleep=no_sleep)
    assert teardowns == [1]
    assert ['systemctl', 'start', 'NetworkManager'] not in system.calls
    assert system.calls[-2:] == [LINK_UP, MANAGED]


def test_connect_raises_link_before_connecting():
    nm = FakeNM(correct={'Home': 'password1'})
    assert csw.connect_with_networkmanager('Home', 'password1', run=nm, sleep=no_sleep, wait_s=3)
    first_connect = next(i for i, c in enumerate(nm.calls) if c[:4] == ['nmcli', 'device', 'wifi', 'connect'])
    assert LINK_UP in nm.calls[:first_connect]
    assert nm.calls.index(LINK_UP) < nm.calls.index(MANAGED) < first_connect


def test_wifi_manager_stop_ap_mode_leaves_link_up(monkeypatch):
    calls = []
    monkeypatch.setattr(wm, 'run_command', lambda argv, check=False: calls.append(argv))
    monkeypatch.setattr(wm.time, 'sleep', no_sleep)
    wm.stop_ap_mode()
    links = [c for c in calls if c[1:4] == ['ip', 'link', 'set']]
    assert links[-1] == ['sudo', 'ip', 'link', 'set', 'wlan0', 'up']


# ============================================
# 2. Scan before NM stops; typed SSIDs
# ============================================

def _result(rc, out=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")


def test_scan_uses_networkmanager_first_and_unescapes(monkeypatch):
    calls = []

    def run(argv, check=False):
        calls.append(argv)
        if argv[:2] == ['nmcli', '-t']:
            return _result(0, 'Home\nCafe\\:Wifi\n\nLEE-AB12\nHome\nback\\\\slash\n')
        return _result(1)

    monkeypatch.setattr(wm, 'run_command', run)
    assert wm.scan_wifi_networks() == ['Home', 'Cafe:Wifi', 'back\\slash']
    assert calls[0] == ['nmcli', '-t', '-f', 'SSID', 'device', 'wifi', 'list',
                        '--rescan', 'yes', 'ifname', 'wlan0']
    assert not any('iwlist' in c for c in calls)


def test_scan_falls_back_to_cached_nm_list_then_iwlist_with_link_up(monkeypatch):
    calls = []

    def run(argv, check=False):
        calls.append(argv)
        if argv[:2] == ['nmcli', '-t']:
            return _result(8)          # NetworkManager is not running
        if 'iwlist' in argv:
            return _result(0, '   ESSID:"Home"\n   ESSID:""\n   ESSID:"Lab"\n')
        return _result(0)

    monkeypatch.setattr(wm, 'run_command', run)
    assert wm.scan_wifi_networks() == ['Home', 'Lab']
    rescans = [c[c.index('--rescan') + 1] for c in calls if '--rescan' in c]
    assert rescans == ['yes', 'no']
    iwlist = next(i for i, c in enumerate(calls) if 'iwlist' in c)
    assert ['sudo', 'ip', 'link', 'set', 'wlan0', 'up'] in calls[:iwlist]


class FakeServer:
    def __init__(self, *a, **k):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


@pytest.fixture
def cp_env(tmp_path, monkeypatch):
    import captive_portal as cp
    monkeypatch.setattr(cp, 'DEVICE_CONFIG_PATH', str(tmp_path / 'device_config.json'))
    monkeypatch.setattr(cp, 'CREW_CONFIG_PATH', str(tmp_path / 'crew_config.json'))
    monkeypatch.setattr(cp, 'PORTAL_POLL_S', 0.001)
    cp.reset_portal_state()
    yield cp
    cp.set_lcd_callback(None)
    cp.cached_networks = []
    cp.reset_portal_state()


def test_portal_scans_before_releasing_wlan0_for_ap(cp_env, monkeypatch):
    cp = cp_env
    order = []
    now = [0.0]

    def clock():
        now[0] += 400.0
        return now[0]

    monkeypatch.setattr(cp, '_clock', clock)
    monkeypatch.setattr(cp, 'scan_wifi_networks', lambda: order.append('scan') or ['Home'])
    monkeypatch.setattr(cp, 'release_wlan0_for_ap', lambda: order.append('release'))
    monkeypatch.setattr(cp, 'start_ap_mode', lambda: order.append('ap') or 'LEE-AB12')
    monkeypatch.setattr(cp, 'start_portal_server', lambda *a, **k: FakeServer())
    monkeypatch.setattr(cp, 'restore_client_mode', lambda: order.append('restore'))
    cp.save_device_config({'wifi_ssid': 'Home', 'wifi_password': 'password1',
                           'setup_complete': True})
    assert cp.run_captive_portal(recovery=True, idle_timeout_s=600) is False
    assert order == ['scan', 'release', 'ap', 'restore']
    assert cp.cached_networks == ['Home']


def test_boot_lets_portal_scan_before_nm_is_stopped(tmp_path, monkeypatch):
    import leeloo_boot as boot
    monkeypatch.setattr(boot, 'show_splash', lambda *a, **k: None)
    monkeypatch.setattr(boot.time, 'sleep', no_sleep)
    cfg = tmp_path / 'device_config.json'
    cfg.write_text(json.dumps({'wifi_ssid': 'Home', 'wifi_password': 'password1',
                               'setup_complete': True}))
    monkeypatch.setattr(boot, 'DEVICE_CONFIG_PATH', str(cfg))
    monkeypatch.setattr(boot, 'boot_wifi_check', lambda *a, **k: False)
    events = []
    monkeypatch.setattr(boot, 'disconnect_wifi_for_setup', lambda: events.append('nm-stop'))
    monkeypatch.setattr(boot, 'run_setup_portal',
                        lambda recovery=False, idle_timeout_s=None: events.append('portal') or True)
    assert boot.ensure_wifi_or_recover() == 'portal'
    assert events == ['portal']

    # First run: same, the portal releases wlan0 after its scan
    events.clear()
    monkeypatch.setattr(boot, 'is_first_run', lambda: True)
    monkeypatch.setattr(boot, 'run_first_run_screen', lambda: True)

    def portal_stops(recovery=False, idle_timeout_s=None):
        events.append('portal')
        raise KeyboardInterrupt   # Stop the boot sequence here

    monkeypatch.setattr(boot, 'run_setup_portal', portal_stops)
    monkeypatch.setattr(boot, 'clear_screen', lambda: None)
    assert boot.boot_entry() == 0
    assert events == ['portal']


def test_wifi_page_has_typed_ssid_field(cp_env):
    page = cp_env.app.test_client().get('/setup/wifi').data.decode()
    assert 'id="ssidOther"' in page and 'type="text" name="ssid_other"' in page
    assert re.search(r'<select name="ssid" id="ssid"\s*>', page)   # Not required any more
    assert 'function chosenSsid()' in page
    assert "document.getElementById('ssidOther').value" in page
    assert 'innerHTML' not in page
    assert "document.createElement('option')" in page
    # f-string brace escaping produced valid JS braces, not doubled ones
    assert '{{' not in page and '}}' not in page


def test_api_wifi_accepts_typed_hidden_ssid(cp_env):
    cp = cp_env
    cp.cached_networks = ['Home']
    client = cp.app.test_client()
    hidden = 'é' * 16       # 32 bytes of UTF-8, not in the scan list
    r = client.post('/api/wifi', json={'ssid': hidden, 'password': 'password1'})
    assert r.get_json()['success'] is True
    assert cp.setup_state['pending_ssid'] == hidden
    cp.reset_portal_state()
    r = cp.app.test_client().post('/api/wifi', json={'ssid': 'é' * 17, 'password': 'password1'})
    assert r.get_json()['success'] is False


@pytest.mark.parametrize('ssid, password', [('\ud800', 'password1'), ('Home', 'pass\ud800word'),
                                            ('Ho\x00me', 'password1')])
def test_api_wifi_rejects_unencodable_values_without_crashing(cp_env, ssid, password):
    r = cp_env.app.test_client().post('/api/wifi', json={'ssid': ssid, 'password': password})
    assert r.status_code == 200
    assert r.get_json()['success'] is False


def test_hidden_network_is_tried_after_scans_miss_it():
    nm = FakeNM()
    hidden_ok = []

    def run(argv, timeout=30):
        if argv[:4] == ['nmcli', 'device', 'wifi', 'connect']:
            nm.calls.append(list(argv))
            if argv[-2:] == ['hidden', 'yes']:
                hidden_ok.append(1)
                nm.profiles['Secret'] = 'Secret'
                nm.connected_ssid, nm.active = 'Secret', 'Secret'
                return True, "", ""
            return False, "", "Error: No network with SSID 'Secret' found."
        return nm(argv, timeout)

    assert csw.connect_with_networkmanager('Secret', 'password1', run=run, sleep=no_sleep, wait_s=3)
    assert hidden_ok == [1]
    assert csw.build_connect_argv('Secret', 'password1', hidden=True)[-2:] == ['hidden', 'yes']


# ============================================
# 3. Password that NM refuses to store
# ============================================

class StrictNM(FakeNM):
    """NM rejects WPA passwords shorter than 8 characters on modify"""

    def __call__(self, argv, timeout=30):
        if argv[:3] == ['nmcli', 'connection', 'modify'] and len(argv[-1]) < 8:
            self.calls.append(list(argv))
            return False, "", ("Error: failed to modify 802-11-wireless-security.psk: "
                               "property is invalid.")
        return super().__call__(argv, timeout)


def test_short_password_that_nm_refuses_is_not_reported_as_working():
    nm = StrictNM(profiles={'Home': 'Home'}, correct={'Home': 'old-password'})
    nm.psk['Home'] = 'old-password'
    fc = FakeClock()
    assert not csw.connect_with_networkmanager('Home', 'short', run=nm, sleep=fc.sleep,
                                               wait_s=30, clock=fc.clock)
    assert nm.psk['Home'] == 'old-password'
    # Never activated with the old stored password, and failed fast
    assert not any(c[:3] == ['nmcli', 'connection', 'up'] for c in nm.calls)
    assert fc.now <= 2


@pytest.mark.parametrize('password, ok', [
    ('password', True),               # 8
    ('p' * 63, True),
    ('a1B2' * 16, True),              # 64 hex digits: raw key
    ('', False),                      # open networks aren't supported
    ('short', False),
    ('p' * 7, False),
    ('p' * 64, False),                # 64 but not hex
    ('p' * 65, False),
    ('é' * 32, False),           # 32 characters but 64 bytes
])
def test_api_wifi_password_rules(cp_env, password, ok):
    cp = cp_env
    r = cp.app.test_client().post('/api/wifi', json={'ssid': 'Home', 'password': password})
    body = r.get_json()
    assert body['success'] is ok
    if not ok:
        assert body['error']
        assert not os.path.exists(cp.DEVICE_CONFIG_PATH)    # Nothing saved
    if not ok and password:
        assert body['error'] == cp.WIFI_PASSWORD_ERROR
        assert '8-63' in body['error']


def test_wifi_page_explains_password_length(cp_env):
    page = cp_env.app.test_client().get('/setup/wifi').data.decode()
    assert 'minlength="8"' in page and '8-63 characters' in page
    assert 'alert(result.error' in page


# ============================================
# 4. nmcli without sudo, own --wait, no leftover profiles
# ============================================

def test_builders_call_nmcli_directly():
    for argv in (csw.build_connect_argv('Home', 'password1'),
                 csw.build_set_psk_argv('Home', 'password1'),
                 csw.build_get_psk_argv('Home'),
                 csw.build_profile_up_argv('Home'),
                 csw.build_delete_profile_argv('Home')):
        assert argv[0] == 'nmcli' and 'sudo' not in argv


@pytest.mark.parametrize('argv', [csw.build_connect_argv('Home', 'password1'),
                                  csw.build_profile_up_argv('Home')])
def test_run_cmd_gives_activations_nmcli_wait_shorter_than_timeout(monkeypatch, argv):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen['argv'], seen['timeout'] = cmd, kwargs['timeout']
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(csw.subprocess, 'run', fake_run)
    csw.run_cmd(argv, timeout=30)
    assert seen['argv'][:2] == ['nmcli', '--wait'] and seen['argv'][3:] == argv[1:]
    wait_s = int(seen['argv'][2])
    assert wait_s >= 1 and seen['timeout'] >= wait_s + 3
    assert seen['timeout'] <= 30


def test_queries_are_not_given_wait(monkeypatch):
    seen = {}
    monkeypatch.setattr(csw.subprocess, 'run',
                        lambda cmd, **kw: seen.update(argv=cmd, timeout=kw['timeout'])
                        or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    argv = ['nmcli', '-t', '-f', 'DEVICE,STATE', 'device']
    csw.run_cmd(argv, timeout=12)
    assert seen == {'argv': argv, 'timeout': 12}


def test_deadline_runner_never_lets_subprocess_kill_nmcli_first(monkeypatch):
    fc = FakeClock()
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append((list(cmd), kwargs['timeout']))
        fc.now += 1
        return types.SimpleNamespace(returncode=1, stdout="", stderr="Error: Timeout expired")

    monkeypatch.setattr(csw.subprocess, 'run', fake_run)
    csw.connect_with_networkmanager('Home', 'password1', sleep=fc.sleep, wait_s=5,
                                    timeout_s=20, clock=fc.clock)
    assert all(argv[0] != 'sudo' for argv, _ in seen)
    activations = [(argv, t) for argv, t in seen if argv[1:2] == ['--wait']]
    assert activations
    for argv, timeout in activations:
        assert timeout - int(argv[2]) == csw.NMCLI_WAIT_MARGIN_S


class LeakyNM(FakeNM):
    """Like real NM: a failed 'device wifi connect' leaves its new profile behind"""

    def __call__(self, argv, timeout=30):
        if argv[:4] == ['nmcli', 'device', 'wifi', 'connect']:
            result = super().__call__(argv, timeout)
            if not result[0]:
                ssid = argv[4]
                name = ssid if ssid not in self.profiles else ssid + ' 1'
                self.profiles[name] = ssid
                self.psk[name] = argv[6]
            return result
        if argv[:3] == ['nmcli', 'connection', 'delete']:
            self.calls.append(list(argv))
            self.profiles.pop(argv[-1], None)
            return True, "", ""
        return super().__call__(argv, timeout)


def test_failed_connect_deletes_the_profile_it_created():
    nm = LeakyNM(profiles={'Office': 'Office'}, correct={'Home': 'right-password'})
    assert not csw.connect_with_networkmanager('Home', 'wrong-password', run=nm,
                                               sleep=no_sleep, wait_s=2)
    assert nm.profiles == {'Office': 'Office'}
    assert ['nmcli', 'connection', 'delete', 'id', 'Home'] in nm.calls


def test_profile_list_failure_means_nothing_is_deleted():
    nm = LeakyNM(correct={'Home': 'right-password'})
    listed = []

    def run(argv, timeout=30):
        if argv[:6] == ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show']:
            listed.append(1)
            if len(listed) == 2:          # The snapshot right before connecting
                return False, "", "Error: NetworkManager is not running."
        return nm(argv, timeout)

    assert not csw.connect_with_networkmanager('Home', 'wrong-password', run=run,
                                               sleep=no_sleep, wait_s=2)
    assert not any(c[:3] == ['nmcli', 'connection', 'delete'] for c in nm.calls)


def test_slow_connect_that_succeeds_keeps_its_profile():
    nm = LeakyNM(correct={'Home': 'password1'})
    fc = FakeClock()

    def run(argv, timeout=30):
        if argv[:4] == ['nmcli', 'device', 'wifi', 'connect']:
            nm.calls.append(list(argv))
            nm.profiles['Home'] = 'Home'
            nm.psk['Home'] = argv[6]
            return False, "", "Error: Timeout expired (25 seconds)"
        if fc.now >= 4:
            nm.connected_ssid, nm.active = 'Home', 'Home'
        return nm(argv, timeout)

    assert csw.connect_with_networkmanager('Home', 'password1', run=run, sleep=fc.sleep,
                                           wait_s=30, clock=fc.clock)
    assert 'Home' in nm.profiles
    assert not any(c[:3] == ['nmcli', 'connection', 'delete'] for c in nm.calls)


# ============================================
# 5. Power-safe config writes
# ============================================

def _cut(obj, fp, *args, **kwargs):
    """json.dump that loses power halfway through writing"""
    fp.write('{"half": ')
    raise OSError("power cut")


def test_device_config_survives_power_cut_and_is_private(cp_env, monkeypatch):
    cp = cp_env
    cp.save_device_config({'wifi_ssid': 'Home', 'wifi_password': 'password1',
                           'setup_complete': True})
    assert os.stat(cp.DEVICE_CONFIG_PATH).st_mode & 0o777 == 0o600
    with monkeypatch.context() as m:
        m.setattr(json, 'dump', _cut)
        with pytest.raises(OSError):
            cp.save_device_config({'user_name': 'Nat'})
    with open(cp.DEVICE_CONFIG_PATH) as f:
        assert json.load(f)['setup_complete'] is True


def test_crew_config_survives_power_cut(cp_env, monkeypatch):
    cp = cp_env
    path = cp.CREW_CONFIG_PATH
    cp.save_crew_config({'invite_code': 'LEELOO-AB12'})
    with monkeypatch.context() as m:
        m.setattr(json, 'dump', _cut)
        with pytest.raises(OSError):
            cp.save_crew_config({'invite_code': 'LEELOO-CD34'})
    with open(path) as f:
        assert json.load(f) == {'invite_code': 'LEELOO-AB12'}


def test_boot_save_json_survives_power_cut(tmp_path, monkeypatch):
    import leeloo_boot as boot
    path = str(tmp_path / 'device_config.json')
    boot.save_json(path, {'setup_complete': True}, mode=0o600)
    assert os.stat(path).st_mode & 0o777 == 0o600
    with monkeypatch.context() as m:
        m.setattr(json, 'dump', _cut)
        with pytest.raises(OSError):
            boot.save_json(path, {'setup_complete': True, 'latitude': 1.0})
    assert boot.load_json(path) == {'setup_complete': True}


# ============================================
# 6. pip --break-system-packages only when supported
# ============================================

SETUP_SH = os.path.join(REPO_ROOT, 'boot', 'leeloo_setup.sh')


def _pip_flags_block():
    with open(SETUP_SH) as f:
        text = f.read()
    match = re.search(r'# >>> pip-flags\n(.*?)# <<< pip-flags', text, re.S)
    assert match, "pip flag detection block missing from leeloo_setup.sh"
    return match.group(1)


@pytest.mark.skipif(shutil.which('bash') is None, reason='needs bash')
@pytest.mark.parametrize('pip_knows_flag', [True, False])
def test_setup_only_passes_break_system_packages_when_supported(tmp_path, pip_knows_flag):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    option = '  --break-system-packages  Allow pip to modify an EXTERNALLY-MANAGED env\n'
    help_text = 'Usage: pip install [options]\n  --user\n' + (option if pip_knows_flag else '')
    (tmp_path / 'help.txt').write_text(help_text)
    pip3 = bindir / 'pip3'
    pip3.write_text('#!/bin/sh\ncat "%s"\n' % (tmp_path / 'help.txt'))
    pip3.chmod(0o755)
    script = 'set -e\n' + _pip_flags_block() + 'echo "FLAGS=[${PIP_FLAGS[*]}]"\n'
    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    out = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env,
                         timeout=10)
    assert out.returncode == 0, out.stderr
    expected = '[--break-system-packages]' if pip_knows_flag else '[]'
    assert f'FLAGS={expected}' in out.stdout


@pytest.mark.skipif(shutil.which('bash') is None, reason='needs bash')
def test_setup_pip_detection_survives_missing_pip(tmp_path):
    """set -e must not abort the setup when pip3 --help fails"""
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    pip3 = bindir / 'pip3'
    pip3.write_text('#!/bin/sh\nexit 2\n')
    pip3.chmod(0o755)
    script = 'set -e\n' + _pip_flags_block() + 'echo "FLAGS=[${PIP_FLAGS[*]}]"\n'
    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    out = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env,
                         timeout=10)
    assert out.returncode == 0 and 'FLAGS=[]' in out.stdout


def test_setup_pip_installs_use_detected_flags():
    with open(SETUP_SH) as f:
        lines = [line for line in f if re.search(r'^\s*(if !\s*)?pip3 install ', line)]
    install_lines = [line for line in lines if '--help' not in line]
    assert install_lines
    for line in install_lines:
        assert '--break-system-packages' not in line
        assert '"${PIP_FLAGS[@]}"' in line
