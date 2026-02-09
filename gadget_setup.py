#!/usr/bin/env python3
"""
LEELOO Setup Display - LCD status screens for captive portal setup
Renders terminal-style status screens to Waveshare 3.5" LCD (480x320)
"""

import os
import struct
import time
from PIL import Image, ImageDraw, ImageFont

# Colors - cyberpunk terminal palette
COLORS = {
    'bg': '#1A1D2E',
    'green': '#719253',
    'purple': '#9C93DD',
    'rose': '#D6697F',
    'tan': '#C2995E',
    'lavender': '#A7AFD4',
    'white': '#FFFFFF',
    'dim': '#4A4A6A',
}


class SetupLCD:
    """Renders setup status screens to LCD"""

    def __init__(self):
        self.width = 480
        self.height = 320
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)

        # Load fonts
        try:
            self.font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            self.font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
            self.font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            self.font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 10)
        except:
            self.font_title = ImageFont.load_default()
            self.font_large = ImageFont.load_default()
            self.font_med = ImageFont.load_default()
            self.font_small = ImageFont.load_default()
            self.font_tiny = ImageFont.load_default()

    def clear(self):
        """Clear the screen"""
        self.image = Image.new('RGB', (self.width, self.height), color=COLORS['bg'])
        self.draw = ImageDraw.Draw(self.image)

    def _center_x(self, text, font):
        """Get x position to center text"""
        try:
            bbox = font.getbbox(text)
            text_width = bbox[2] - bbox[0]
        except:
            text_width = len(text) * 8
        return (self.width - text_width) // 2

    def draw_header(self, subtitle="SETUP"):
        """Draw the terminal-style header"""
        # Top border
        self.draw.rectangle([10, 10, self.width - 10, 45], outline=COLORS['purple'], width=2)

        # Header text
        header = f"LEELOO v1.0 ─── {subtitle}"
        x = self._center_x(header, self.font_med)
        self.draw.text((x, 18), header, font=self.font_med, fill=COLORS['purple'])

    def draw_box(self, y, height, color=COLORS['green']):
        """Draw a content box"""
        self.draw.rectangle([30, y, self.width - 30, y + height], outline=color, width=2)

    def draw_slider(self, y, filled, total=10, color=COLORS['green']):
        """Draw a progress slider"""
        slider = ""
        for i in range(total):
            slider += "■" if i < filled else "□"

        x = self._center_x(slider, self.font_large)
        # Draw filled part
        filled_str = "■" * filled
        empty_str = "□" * (total - filled)

        self.draw.text((x, y), filled_str, font=self.font_large, fill=color)
        # Calculate filled width
        try:
            filled_width = self.font_large.getbbox(filled_str)[2] if filled > 0 else 0
        except:
            filled_width = filled * 12
        self.draw.text((x + filled_width, y), empty_str, font=self.font_large, fill=COLORS['dim'])

    def write_to_lcd(self, fb_path="/dev/fb1"):
        """Write image to LCD framebuffer"""
        try:
            with open(fb_path, 'wb') as fb:
                for y in range(self.height):
                    for x in range(self.width):
                        r, g, b = self.image.getpixel((x, y))
                        # RGB888 to RGB565
                        pixel = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                        fb.write(struct.pack('H', pixel))
        except Exception as e:
            print(f"LCD write error: {e}")

    # =========================================
    # SETUP SCREENS
    # =========================================

    def render_ap_mode(self, ssid="LEE-Setup"):
        """Screen: AP mode active - waiting for phone connection"""
        self.clear()
        self.draw_header("FIRST RUN")

        # Main content box
        self.draw_box(60, 200, COLORS['green'])

        # Instructions
        y = 80
        self.draw.text((50, y), "1. On your phone, connect to", font=self.font_med, fill=COLORS['lavender'])
        y += 22
        self.draw.text((50, y), "   WiFi network:", font=self.font_med, fill=COLORS['lavender'])

        # SSID highlight
        y += 35
        ssid_display = f">>> {ssid} <<<"
        x = self._center_x(ssid_display, self.font_large)
        self.draw.text((x, y), ssid_display, font=self.font_large, fill=COLORS['green'])

        # More instructions
        y += 40
        self.draw.text((50, y), "2. Setup page will open", font=self.font_med, fill=COLORS['lavender'])
        y += 22
        self.draw.text((50, y), "   automatically", font=self.font_med, fill=COLORS['lavender'])

        y += 35
        hint = "(or visit 192.168.4.1)"
        x = self._center_x(hint, self.font_small)
        self.draw.text((x, y), hint, font=self.font_small, fill=COLORS['dim'])

        # Status bar at bottom
        self.draw.text((50, 280), "waiting for connection", font=self.font_small, fill=COLORS['dim'])
        self.draw_slider(278, 1, 10, COLORS['purple'])

        self.write_to_lcd()

    def render_phone_connected(self):
        """Screen: Phone connected - awaiting config"""
        self.clear()
        self.draw_header("SETUP IN PROGRESS")

        # Main content box
        self.draw_box(80, 140, COLORS['lavender'])

        # Message
        y = 110
        msg = "Phone connected!"
        x = self._center_x(msg, self.font_large)
        self.draw.text((x, y), msg, font=self.font_large, fill=COLORS['green'])

        y += 40
        msg = "Complete setup on your"
        x = self._center_x(msg, self.font_med)
        self.draw.text((x, y), msg, font=self.font_med, fill=COLORS['lavender'])

        y += 22
        msg = "phone browser..."
        x = self._center_x(msg, self.font_med)
        self.draw.text((x, y), msg, font=self.font_med, fill=COLORS['lavender'])

        # Status bar
        self.draw.text((50, 280), "awaiting config", font=self.font_small, fill=COLORS['dim'])
        self.draw_slider(278, 5, 10, COLORS['lavender'])

        self.write_to_lcd()

    def render_connecting(self, ssid="WiFi"):
        """Screen: Connecting to user's WiFi"""
        self.clear()
        self.draw_header("CONNECTING")

        # Main content box
        self.draw_box(80, 140, COLORS['tan'])

        # Message
        y = 110
        msg = "Connecting to:"
        x = self._center_x(msg, self.font_med)
        self.draw.text((x, y), msg, font=self.font_med, fill=COLORS['lavender'])

        y += 30
        x = self._center_x(ssid, self.font_large)
        self.draw.text((x, y), ssid, font=self.font_large, fill=COLORS['tan'])

        # Animated progress (caller should update this)
        y += 45
        self.draw_slider(y, 6, 12, COLORS['tan'])

        self.write_to_lcd()

    def render_success(self):
        """Screen: Setup complete"""
        self.clear()
        self.draw_header("READY")

        # Main content box
        self.draw_box(70, 180, COLORS['green'])

        # Checkmark
        y = 90
        check = "✓"
        x = self._center_x(check, self.font_title)
        self.draw.text((x, y), check, font=self.font_title, fill=COLORS['green'])

        y += 35
        msg = "Connected!"
        x = self._center_x(msg, self.font_large)
        self.draw.text((x, y), msg, font=self.font_large, fill=COLORS['green'])

        y += 35
        msg = "Check your phone for"
        x = self._center_x(msg, self.font_med)
        self.draw.text((x, y), msg, font=self.font_med, fill=COLORS['lavender'])

        y += 22
        msg = "the quick start guide."
        x = self._center_x(msg, self.font_med)
        self.draw.text((x, y), msg, font=self.font_med, fill=COLORS['lavender'])

        y += 30
        msg = "Then put it away!"
        x = self._center_x(msg, self.font_small)
        self.draw.text((x, y), msg, font=self.font_small, fill=COLORS['dim'])

        # Status bar
        self.draw.text((50, 280), "setup complete", font=self.font_small, fill=COLORS['green'])
        self.draw_slider(278, 10, 10, COLORS['green'])

        self.write_to_lcd()

    def render_error(self, message="Connection failed"):
        """Screen: Error occurred"""
        self.clear()
        self.draw_header("ERROR")

        # Main content box
        self.draw_box(80, 140, COLORS['rose'])

        # Error icon
        y = 100
        icon = "✗"
        x = self._center_x(icon, self.font_title)
        self.draw.text((x, y), icon, font=self.font_title, fill=COLORS['rose'])

        y += 35
        x = self._center_x(message, self.font_med)
        self.draw.text((x, y), message, font=self.font_med, fill=COLORS['rose'])

        y += 35
        msg = "Please try again..."
        x = self._center_x(msg, self.font_small)
        self.draw.text((x, y), msg, font=self.font_small, fill=COLORS['lavender'])

        self.write_to_lcd()

    def render_starting(self):
        """Screen: Starting up main app"""
        self.clear()
        self.draw_header("STARTING")

        # Main content box
        self.draw_box(100, 100, COLORS['purple'])

        y = 130
        msg = "Loading LEELOO..."
        x = self._center_x(msg, self.font_large)
        self.draw.text((x, y), msg, font=self.font_large, fill=COLORS['purple'])

        y += 40
        self.draw_slider(y, 7, 10, COLORS['purple'])

        self.write_to_lcd()


# Callback handler for captive portal
def lcd_update_handler(screen, **kwargs):
    """Handle LCD updates from captive portal"""
    lcd = SetupLCD()

    if screen == 'ap_mode':
        lcd.render_ap_mode(ssid=kwargs.get('ssid', 'LEE-Setup'))
    elif screen == 'phone_connected':
        lcd.render_phone_connected()
    elif screen == 'connecting':
        lcd.render_connecting(ssid=kwargs.get('ssid', 'WiFi'))
    elif screen == 'connected' or screen == 'success':
        lcd.render_success()
    elif screen == 'error':
        lcd.render_error(message=kwargs.get('message', 'An error occurred'))
    elif screen == 'starting':
        lcd.render_starting()


if __name__ == "__main__":
    # Demo mode - cycle through screens
    import sys

    lcd = SetupLCD()

    if len(sys.argv) > 1:
        screen = sys.argv[1]
        if screen == "ap":
            lcd.render_ap_mode("LEE-A7X2")
        elif screen == "phone":
            lcd.render_phone_connected()
        elif screen == "connecting":
            lcd.render_connecting("Home-WiFi-5G")
        elif screen == "success":
            lcd.render_success()
        elif screen == "error":
            lcd.render_error("Connection failed")
        elif screen == "starting":
            lcd.render_starting()
        else:
            print(f"Unknown screen: {screen}")
            print("Options: ap, phone, connecting, success, error, starting")
    else:
        # Demo all screens
        print("Cycling through setup screens...")

        lcd.render_ap_mode("LEE-A7X2")
        print("1. AP Mode")
        time.sleep(3)

        lcd.render_phone_connected()
        print("2. Phone Connected")
        time.sleep(3)

        lcd.render_connecting("Home-WiFi-5G")
        print("3. Connecting")
        time.sleep(3)

        lcd.render_success()
        print("4. Success")
        time.sleep(3)

        lcd.render_error("Connection failed")
        print("5. Error")
        time.sleep(3)

        lcd.render_starting()
        print("6. Starting")
        time.sleep(3)

        print("Demo complete!")
