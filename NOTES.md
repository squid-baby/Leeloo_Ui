# Gadget UI - Development Notes

## Project Overview
Retro terminal-style music/info device UI for Raspberry Pi with Waveshare 3.5" LCD (480×320).

## Hardware Setup
- Raspberry Pi (gadget.local)
- Waveshare 3.5" LCD (480×320, ILI9486 driver)
- Framebuffer: /dev/fb1 (LCD), /dev/fb0 (HDMI)
- RGB565 format for LCD output

## Pi Connection
- Hostname: gadget.local
- User: pi
- Password: gadget

## Key Files
- `gadget_display.py` - Main Python renderer (PIL/Pillow)
- `gadget_weather.py` - Open-Meteo API weather integration
- `gadget_data.py` - JSON-based persistence for hang times
- `doorways-album.jpg` - Album art

## Display Layout (top to bottom)
1. **Weather Box** (tan, y=16, h=59): temp °F, UV index, rain
2. **Time Box** (purple, y=83, h=71): time/date, moon phase slider, "nxt hang" countdown
3. **Messages Box** (lavender, y=162, h=28): contact names with unread badges (○Amy ②Ben)
4. **Album Box** (green, y=198, h=108): artist, track, BPM, listeners, pushed by

## Important Fixes
- **Console cursor blinking on LCD**: Run `sudo con2fbmap 1 0` to map console to fb0 (HDMI) instead of fb1 (LCD)
- **Circled numbers not rendering**: Use DejaVuSans.ttf (not Mono) for font_symbol
- **⓪ doesn't render**: Use ○ (white circle) instead for zero count

## Deployment
```bash
# Copy files to Pi
sshpass -p 'gadget' scp gadget_display.py pi@gadget.local:/home/pi/

# Render to LCD
sshpass -p 'gadget' ssh pi@gadget.local "cd /home/pi && python3 -c \"
from gadget_display import GadgetDisplay
from gadget_weather import get_weather
from datetime import datetime
import struct

weather_data = get_weather()
display = GadgetDisplay(preview_mode=True)

now = datetime.now()
time_data = {
    'time_str': now.strftime('%-I:%M %p'),
    'date_str': now.strftime('%b %-d'),
    'seconds': now.second,
}

contacts = ['Amy', 'Ben']
album_data = {
    'artist': 'Cinnamon Chasers',
    'track': 'Doorways',
    'bpm': 120,
    'listeners': '262K',
    'pushed_by': 'Amy',
}

img = display.render(weather_data, time_data, contacts, album_data, album_art_path='/home/pi/doorways-album.jpg')

def rgb_to_rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

with open('/dev/fb1', 'wb') as fb:
    for y in range(320):
        for x in range(480):
            r, g, b = img.getpixel((x, y))
            pixel = rgb_to_rgb565(r, g, b)
            fb.write(struct.pack('H', pixel))
\""
```

## Moon Phase
- Calculated locally using 29.53-day lunar cycle from known new moon (Jan 29, 2025)
- 6-box slider: 0 = new moon, 6 = full moon

## Countdown Slider
- Uses `deplete_left=True` parameter
- Boxes empty from left to right as time passes
- 9-box slider for hang countdown

## Fonts
- font_header: DejaVuSansMono 9pt (Gadget v1.0, tap to talk)
- font_tiny: DejaVuSansMono 12pt (main text)
- font_symbol: DejaVuSans 13pt (circled numbers ①②③)
- font_slider: DejaVuSansMono 16pt (■□ boxes)

## Date: February 7, 2025
