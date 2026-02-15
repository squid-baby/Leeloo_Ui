#!/usr/bin/env python3
"""
Test rain visualization output
"""
import random

def build_rain_viz(weather_data):
    """
    Build ASCII rain visualization similar to iOS weather
    """
    # Extract rain data
    rain_24h = weather_data.get('rain_24h_inches', 0)
    current_precip = weather_data.get('current_precip_inches', 0)
    is_raining = weather_data.get('is_raining', False)

    # Unicode block chars from low to high
    blocks = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']

    # Generate 24 bars (representing ~2.5 min intervals over 60 min)
    bars = []
    for i in range(24):
        # If it's raining now, high intensity for first few bars, then random taper
        if is_raining and i < 8:
            intensity = random.randint(5, 7)  # High rain
        elif is_raining and i < 16:
            intensity = random.randint(2, 5)  # Tapering off
        elif rain_24h > 0.5:  # Forecast shows significant rain
            intensity = random.randint(3, 6)  # Variable rain
        elif rain_24h > 0.1:  # Light rain forecasted
            intensity = random.randint(1, 3)  # Occasional drops
        else:
            intensity = 0  # No rain

        bars.append(blocks[intensity])

    # Build the visualization
    bar_line = ''.join(bars)
    timeline = "Now 10m 20m 30m 40m"

    return bar_line, timeline


if __name__ == "__main__":
    print("Rain Visualization Test\n" + "="*50)

    # Test 1: Heavy rain currently
    print("\n1. Heavy rain currently:")
    weather1 = {
        'rain_24h_inches': 1.2,
        'current_precip_inches': 0.5,
        'is_raining': True
    }
    bars, timeline = build_rain_viz(weather1)
    print(bars)
    print(timeline)

    # Test 2: Light rain forecasted
    print("\n2. Light rain forecasted:")
    weather2 = {
        'rain_24h_inches': 0.2,
        'current_precip_inches': 0.0,
        'is_raining': False
    }
    bars, timeline = build_rain_viz(weather2)
    print(bars)
    print(timeline)

    # Test 3: No rain
    print("\n3. No rain:")
    weather3 = {
        'rain_24h_inches': 0.0,
        'current_precip_inches': 0.0,
        'is_raining': False
    }
    bars, timeline = build_rain_viz(weather3)
    print(bars)
    print(timeline)

    # Test 4: Significant rain coming
    print("\n4. Significant rain coming:")
    weather4 = {
        'rain_24h_inches': 0.8,
        'current_precip_inches': 0.0,
        'is_raining': False
    }
    bars, timeline = build_rain_viz(weather4)
    print(bars)
    print(timeline)

    print("\n" + "="*50)
    print("✓ All tests complete!")
