#!/usr/bin/env python3
"""
LEELOO Weather Module
Fetches real weather data from Open-Meteo API (free, no API key needed)
"""

import requests

def _weather_code_to_desc(code):
    """Convert WMO weather code to human-readable description"""
    codes = {
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
    return codes.get(code, f"code {code}")


def get_weather(lat=35.9101, lon=-79.0753, timezone=None):
    """
    Fetch current weather from Open-Meteo API

    Args:
        lat: Latitude (default: Carrboro, NC)
        lon: Longitude (default: Carrboro, NC)
        timezone: IANA timezone string (e.g. 'America/New_York'). Uses 'auto' if not set.

    Returns:
        dict with temp_f, uv, rain, temp_slider (all 0-10 scale for sliders)
    """
    try:
        tz = timezone or "auto"
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,uv_index,precipitation,weather_code"
            f"&daily=precipitation_sum"
            f"&temperature_unit=fahrenheit"
            f"&timezone={tz}"
            f"&forecast_days=1"
        )

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Extract values
        temp = int(data['current']['temperature_2m'])
        uv_index = data['current'].get('uv_index', 0)  # 0-11+ scale
        current_precip_mm = data['current'].get('precipitation', 0) or 0
        weather_code = data['current'].get('weather_code', 0)

        # 24-hour precipitation total (forecast)
        rain_mm = data['daily']['precipitation_sum'][0] if data['daily']['precipitation_sum'][0] else 0
        rain_inches = rain_mm / 25.4

        # Current precipitation rate (mm -> inches)
        current_precip_inches = current_precip_mm / 25.4

        # Use the HIGHER of current precip rate or daily total for the rain slider
        # so active rain always shows up prominently
        effective_rain = max(rain_inches, current_precip_inches * 4)  # weight current rain heavily

        # WMO weather codes: 0=clear, 1-3=partly cloudy, 45-48=fog,
        # 51-55=drizzle, 56-57=freezing drizzle, 61-65=rain, 66-67=freezing rain,
        # 71-77=snow, 80-82=rain showers, 85-86=snow showers, 95-99=thunderstorm
        is_raining = weather_code in range(51, 68) or weather_code in range(80, 83) or weather_code in range(95, 100)
        weather_desc = _weather_code_to_desc(weather_code)

        # Return raw values - display handles scaling
        return {
            'temp_f': temp,
            'uv_raw': uv_index,  # Raw UV index (0-11+)
            'rain_24h_inches': effective_rain,  # Effective rain for slider
            'current_precip_inches': current_precip_inches,
            'is_raining': is_raining,
            'weather_code': weather_code,
            'weather_desc': weather_desc,
        }

    except Exception as e:
        print(f"⚠️  Weather API error: {e}")
        # Return fallback values
        return {
            'temp_f': 72,
            'uv_raw': 5,
            'rain_24h_inches': 0.5,
        }


if __name__ == "__main__":
    # Test the API
    print("Fetching weather for Carrboro, NC...")
    weather = get_weather()
    print(f"Temperature: {weather['temp_f']}°F")
    print(f"UV Index: {weather['uv_raw']}")
    print(f"Rain (24hr): {weather['rain_24h_inches']:.2f} inches")
