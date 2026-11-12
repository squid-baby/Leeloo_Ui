#!/usr/bin/env python3
"""
LEELOO Weather Module
Fetches real weather data from Open-Meteo API (free, no API key needed)
"""

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NOWCAST_STEPS = 24          # 24 x 15 min = next 6 hours
REQUEST_TIMEOUT = 10        # seconds

_WMO_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


def _weather_code_to_desc(code):
    """Convert WMO weather code to human-readable description"""
    return _WMO_CODES.get(code, f"code {code}")


def parse_weather(data):
    """Convert an Open-Meteo forecast response into the LEELOO weather dict.

    Raises KeyError/TypeError/ValueError if the response has no usable
    current temperature. All other fields are optional.
    """
    current = data['current']
    temp = int(round(float(current['temperature_2m'])))
    uv_index = current.get('uv_index') or 0
    current_precip_mm = current.get('precipitation') or 0
    weather_code = current.get('weather_code') or 0

    # Calendar-day precipitation total (forecast)
    daily_sums = (data.get('daily') or {}).get('precipitation_sum') or [0]
    rain_inches = (daily_sums[0] or 0) / 25.4
    current_precip_inches = current_precip_mm / 25.4

    # Use the HIGHER of current precip rate or daily total for the rain slider
    # so active rain always shows up prominently
    effective_rain = max(rain_inches, current_precip_inches * 4)

    # WMO codes: 51-67 drizzle/rain, 80-82 showers, 95-99 thunderstorm
    is_raining = (51 <= weather_code <= 67) or (80 <= weather_code <= 82) or (95 <= weather_code <= 99)

    # Real 15-minute precipitation forecast (mm per 15 min) for the rain chart
    minutely = (data.get('minutely_15') or {}).get('precipitation') or []
    nowcast = [float(v or 0) for v in minutely[:NOWCAST_STEPS]]

    return {
        'temp_f': temp,
        'uv_raw': uv_index,                       # Raw UV index (0-11+)
        'rain_24h_inches': effective_rain,        # Effective rain for slider
        'current_precip_inches': current_precip_inches,
        'is_raining': is_raining,
        'weather_code': weather_code,
        'weather_desc': _weather_code_to_desc(weather_code),
        'precip_15min_mm': nowcast,               # next 6 h, 15-min steps
    }


def get_weather(lat=35.9101, lon=-79.0753, timezone=None):
    """
    Fetch current weather from Open-Meteo API.

    Args:
        lat, lon: Location (default: Carrboro, NC)
        timezone: IANA timezone string. Uses 'auto' if not set.

    Returns:
        Weather dict (see parse_weather), or None if the fetch failed.
        The caller decides what to show on failure. This function never
        returns invented values.
    """
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,uv_index,precipitation,weather_code',
                'daily': 'precipitation_sum',
                'minutely_15': 'precipitation',
                'forecast_minutely_15': NOWCAST_STEPS,
                'temperature_unit': 'fahrenheit',
                'timezone': timezone or 'auto',
                'forecast_days': 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return parse_weather(resp.json())
    except Exception as e:
        print(f"⚠️  Weather API error: {e}")
        return None


if __name__ == "__main__":
    print("Fetching weather for Carrboro, NC...")
    weather = get_weather()
    if weather:
        print(f"Temperature: {weather['temp_f']}°F ({weather['weather_desc']})")
        print(f"UV Index: {weather['uv_raw']}")
        print(f"Rain (24hr): {weather['rain_24h_inches']:.2f} inches")
        print(f"Next 6 h (mm/15min): {weather['precip_15min_mm']}")
    else:
        print("Weather not available")
