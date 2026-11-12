"""Weather: real data only, never invented fallback values."""
import gadget_weather as gw

SAMPLE = {
    "current": {"temperature_2m": 58.6, "uv_index": 3.2, "precipitation": 0.5, "weather_code": 61},
    "minutely_15": {"precipitation": [0.0, 0.2, None, 1.5] + [0.0] * 30},
    "daily": {"precipitation_sum": [12.7]},
}


class Resp:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self._data


def test_parse_weather():
    w = gw.parse_weather(SAMPLE)
    assert w['temp_f'] == 59 and w['uv_raw'] == 3.2
    assert w['is_raining'] is True and w['weather_desc'] == "light rain"
    assert abs(w['rain_24h_inches'] - 0.5) < 1e-6
    assert len(w['precip_15min_mm']) == gw.NOWCAST_STEPS
    assert w['precip_15min_mm'][:4] == [0.0, 0.2, 0.0, 1.5]


def test_parse_weather_minimal_response():
    w = gw.parse_weather({"current": {"temperature_2m": 70}})
    assert w['temp_f'] == 70 and w['precip_15min_mm'] == [] and w['is_raining'] is False


def test_get_weather_success_and_request_parameters(monkeypatch):
    seen = {}
    def fake_get(url, params=None, timeout=None):
        seen.update(params, timeout=timeout)
        return Resp(SAMPLE)
    monkeypatch.setattr(gw.requests, "get", fake_get)
    assert gw.get_weather(1, 2, timezone="America/New_York")['temp_f'] == 59
    assert seen['minutely_15'] == 'precipitation' and seen['timezone'] == "America/New_York"
    assert seen['timeout']


def test_get_weather_failure_returns_none_not_fake_values(monkeypatch):
    def boom(*a, **k): raise OSError("no network")
    monkeypatch.setattr(gw.requests, "get", boom)
    assert gw.get_weather(1, 2) is None
    monkeypatch.setattr(gw.requests, "get", lambda *a, **k: Resp(status=500))
    assert gw.get_weather(1, 2) is None
    monkeypatch.setattr(gw.requests, "get", lambda *a, **k: Resp({"unexpected": True}))
    assert gw.get_weather(1, 2) is None
