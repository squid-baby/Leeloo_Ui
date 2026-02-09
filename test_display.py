#!/usr/bin/env python3
"""
Test script to preview different display states
"""

from gadget_display import LEELOODisplay
import time

def test_sunny_day():
    """Test sunny weather display"""
    print("🌞 Testing sunny day...")
    display = LEELOODisplay(preview_mode=True)
    
    weather_data = {
        'temp': 85,
        'sun': 95,  # Very sunny
        'rain': 0,
    }
    
    time_data = {
        'time_str': '12:30 PM',
        'hour': 12,
        'date_str': 'Jul 15',
    }
    
    messages = [
        {'name': 'Sarah', 'preview': 'Beach day? ☀️'},
        {'name': 'Mike', 'preview': "I'm in!"},
        {'name': 'Amy', 'preview': 'Bring sunscreen'},
    ]
    
    album_data = {
        'bpm': 128,
        'duration': '3:45 s',
        'artist_1': 'Summer',
        'artist_2': 'Vibes',
        'track': 'Sunshine',
        'pushed_by': 'Sarah',
        'current_time': '1:20',
        'current_seconds': 80,
        'total_seconds': 225,
        'plays': 89,
    }
    
    display.render(weather_data, time_data, messages, album_data)
    display.show()

def test_rainy_day():
    """Test rainy weather display"""
    print("🌧️  Testing rainy day...")
    display = LEELOODisplay(preview_mode=True)
    
    weather_data = {
        'temp': 55,
        'sun': 10,  # Very cloudy
        'rain': 85,  # Heavy rain
    }
    
    time_data = {
        'time_str': '6:15 PM',
        'hour': 6,
        'date_str': 'Nov 22',
    }
    
    messages = [
        {'name': 'Mike', 'preview': 'Stay inside today'},
        {'name': 'Amy', 'preview': 'Movie night?'},
        {'name': 'Sarah', 'preview': 'Im bringing popcorn'},
    ]
    
    album_data = {
        'bpm': 92,
        'duration': '4:12 s',
        'artist_1': 'Rainy Day',
        'artist_2': 'Jazz',
        'track': 'Cozy',
        'pushed_by': 'Mike',
        'current_time': '2:05',
        'current_seconds': 125,
        'total_seconds': 252,
        'plays': 67,
    }
    
    display.render(weather_data, time_data, messages, album_data)
    display.show()

def test_early_morning():
    """Test early morning display"""
    print("🌅 Testing early morning...")
    display = LEELOODisplay(preview_mode=True)
    
    weather_data = {
        'temp': 48,
        'sun': 20,  # Dawn
        'rain': 0,
    }
    
    time_data = {
        'time_str': '6:00 AM',
        'hour': 6,
        'date_str': 'Jan 1',
    }
    
    messages = [
        {'name': 'Amy', 'preview': 'Happy New Year!'},
        {'name': 'Sarah', 'preview': 'Coffee run in 10?'},
        {'name': 'Mike', 'preview': 'Still sleeping...'},
    ]
    
    album_data = {
        'bpm': 85,
        'duration': '5:30 s',
        'artist_1': 'Morning',
        'artist_2': 'Brew',
        'track': 'Wake Up',
        'pushed_by': 'Sarah',
        'current_time': '0:45',
        'current_seconds': 45,
        'total_seconds': 330,
        'plays': 12,
    }
    
    display.render(weather_data, time_data, messages, album_data)
    display.show()

def test_late_night():
    """Test late night display"""
    print("🌙 Testing late night...")
    display = LEELOODisplay(preview_mode=True)
    
    weather_data = {
        'temp': 62,
        'sun': 0,  # Night time
        'rain': 15,  # Light drizzle
    }
    
    time_data = {
        'time_str': '11:47 PM',
        'hour': 11,
        'date_str': 'Dec 31',
    }
    
    messages = [
        {'name': 'Mike', 'preview': 'You up?'},
        {'name': 'Amy', 'preview': 'One more song...'},
        {'name': 'Sarah', 'preview': 'See you tomorrow'},
    ]
    
    album_data = {
        'bpm': 110,
        'duration': '6:15 s',
        'artist_1': 'Night',
        'artist_2': 'Shift',
        'track': 'Midnight',
        'pushed_by': 'Amy',
        'current_time': '4:30',
        'current_seconds': 270,
        'total_seconds': 375,
        'plays': 95,
    }
    
    display.render(weather_data, time_data, messages, album_data)
    display.show()

if __name__ == "__main__":
    print("🎨 LEELOO Display Test Suite\n")
    print("Press Enter to see each test scenario...")
    
    input("\n📋 Test 1: Sunny Day")
    test_sunny_day()
    
    input("\n📋 Test 2: Rainy Day")
    test_rainy_day()
    
    input("\n📋 Test 3: Early Morning")
    test_early_morning()
    
    input("\n📋 Test 4: Late Night")
    test_late_night()
    
    print("\n✅ All tests complete! Check /tmp/leeloo_preview.png for the last render.")
