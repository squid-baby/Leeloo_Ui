#!/usr/bin/env python3
"""
LEELOO Weather Module
Fetches real weather data from Open-Meteo API (free, no API key needed)
"""

import requests

def get_weather(lat=35.9101, lon=-79.0753):
    """
    Fetch current weather from Open-Meteo API

    Args:
        lat: Latitude (default: Carrboro, NC)
        lon: Longitude (default: Carrboro, NC)

    Returns:
        dict with temp_f, uv, rain, temp_slider (all 0-10 scale for sliders)
    """
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,uv_index"
            f"&daily=precipitation_sum"
            f"&temperature_unit=fahrenheit"
            f"&timezone=America/New_York"
            f"&forecast_days=1"
        )

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Extract values
        temp = int(data['current']['temperature_2m'])
        uv_index = data['current'].get('uv_index', 0)  # 0-11+ scale

        # 24-hour precipitation total
        rain_mm = data['daily']['precipitation_sum'][0] if data['daily']['precipitation_sum'][0] else 0
        rain_inches = rain_mm / 25.4

        # Return raw values - display handles scaling
        return {
            'temp_f': temp,
            'uv_raw': uv_index,  # Raw UV index (0-11+)
            'rain_24h_inches': rain_inches,  # 24hr rain in inches
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
