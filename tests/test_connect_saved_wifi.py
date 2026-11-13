"""WiFi connect script: no shell, and a failure never dead-ends the setup."""
import json

import connect_saved_wifi as cw

EVIL = "pass'; touch /tmp/leeloo_pwned; '"


def setup_config(monkeypatch, tmp_path, **values):
    path = tmp_path / "device_config.json"
    path.write_text(json.dumps(values))
    monkeypatch.setattr(cw, "DEVICE_CONFIG_PATH", str(path))
    monkeypatch.setattr(cw.time, "sleep", lambda s: None)
    return path


def test_no_shell_is_used():
    src = open(cw.__file__).read()
    assert "shell=True" not in src


def test_password_is_passed_as_one_argument(monkeypatch, tmp_path):
    setup_config(monkeypatch, tmp_path, setup_complete=True, wifi_ssid="Home'; x", wifi_password=EVIL)
    calls = []

    class R:
        def __init__(self, out=""): self.returncode, self.stdout, self.stderr = 0, out, ""

    def fake_run(args, **kw):
        assert isinstance(args, list) and not kw.get("shell")
        calls.append(args)
        connected = any("connect" in c for c in calls)
        if args[:2] == ["nmcli", "-t"]:
            return R("yes:Home'; x\n" if connected else "no:Other\n")
        return R()
    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    assert cw.main() == 0
    connect = next(c for c in calls if "connect" in c)
    assert EVIL in connect and "Home'; x" in connect      # exact, unmodified, single arguments
    assert json.loads(open(cw.DEVICE_CONFIG_PATH).read())["setup_complete"] is True


def test_wrong_password_reopens_setup(monkeypatch, tmp_path):
    path = setup_config(monkeypatch, tmp_path, setup_complete=True, wifi_ssid="Home", wifi_password="bad")

    class R:
        returncode, stdout, stderr = 1, "", "Secrets were required"
    monkeypatch.setattr(cw.subprocess, "run", lambda args, **kw: R())
    clock = [0.0]
    monkeypatch.setattr(cw.time, "time", lambda: clock.__setitem__(0, clock[0] + 5) or clock[0])
    assert cw.main() == 1
    config = json.loads(path.read_text())
    assert config["setup_complete"] is False and "Home" in config["wifi_error"]


def test_active_ssid_unescapes_colons(monkeypatch):
    monkeypatch.setattr(cw, "run_cmd", lambda args, timeout=30: (True, "no:x\nyes:My\\:Net\n", ""))
    assert cw.active_ssid() == "My:Net"
