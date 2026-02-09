# LEELOO Architecture Plan
## Voice-Enabled Music Sharing Device

**Document Version**: 2.0  
**Date**: February 2026  
**Status**: Implementation Ready

---

## Executive Summary

LEELOO is a set of connected alien-shaped devices that enable physical music sharing between friends. Users touch the screen to talk, knock on the body to react, and see album art + Spotify scan codes appear on all devices simultaneously.

**Key Architecture Decisions**:
- **Cloud-first processing** — All heavy lifting (STT, AI, search) happens on server
- **Python-only device code** — No browser, direct LCD rendering with PIL
- **Dual input model** — Screen touch for voice, body knocks for reactions
- **ASCII art display** — All reactions/animations use ASCII art, not standard emojis
- **WebSocket sync** — Real-time updates to all devices in a group

**Philosophy**: Tech that adds value to your life. More fun, less phone.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Hardware Stack](#hardware-stack)
3. [Interaction Model](#interaction-model)
4. [ASCII Art Display System](#ascii-art-display-system)
5. [Device Software](#device-software)
6. [Backend Services](#backend-services)
7. [User Flows](#user-flows)
8. [Setup & Pairing Flow](#setup--pairing-flow)
9. [Phone Quick Guide](#phone-quick-guide)
10. [Weekly Mixtape](#weekly-mixtape)
11. [Database Schema](#database-schema)
12. [API Specification](#api-specification)
13. [Spotify Integration](#spotify-integration)
14. [Implementation Phases](#implementation-phases)
15. [Cost Estimates](#cost-estimates)

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              GADGET ECOSYSTEM                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐       │
│   │   DEVICE A   │◄───────►│    CLOUD     │◄───────►│   DEVICE B   │       │
│   │  (Nathan's)  │         │   BACKEND    │         │  (Friend's)  │       │
│   └──────────────┘         └──────────────┘         └──────────────┘       │
│          │                        ▲                        │               │
│          └────────────────────────┼────────────────────────┘               │
│                                   │                                        │
│                          ┌────────┴────────┐                               │
│                          │  TELEGRAM BOT   │                               │
│                          │ (alt interface) │                               │
│                          └─────────────────┘                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Latency: Voice to Display

| Step | Time | Running Total |
|------|------|---------------|
| Silence detection | 1.5s | 1.5s |
| Audio upload (~320KB) | 0.5-1s | 2-2.5s |
| Whisper transcription | 1-2s | 3-4.5s |
| Claude intent parsing | 0.5-1s | 3.5-5.5s |
| Spotify search | 0.3-0.5s | 4-6s |
| WebSocket broadcast | <0.1s | 4-6s |
| Device render + LCD | 0.3-0.5s | **4-7s total** |

---

## Hardware Stack

### Per Device (~$72)

| Component | Model | Purpose | Cost |
|-----------|-------|---------|------|
| Controller | Raspberry Pi Zero 2 W | Main compute | $15 |
| Display | Waveshare 3.5" LCD (480×320) | UI + touch | $25 |
| Microphone | USB mini mic | Voice input | $15 |
| Tap Sensor | ADXL345 accelerometer | Knock detection | $3 |
| LED | RGB LED | Status indicator | $1 |
| Power | USB-C breakout | Power input | $3 |
| Enclosure | 3D printed alien | Housing | $10 |

### GPIO Pin Assignments

```
DISPLAY (SPI)           ACCELEROMETER (I2C)     STATUS LED
├── GPIO 10 → Data      ├── GPIO 2 → SDA        ├── GPIO 17 → Red
├── GPIO 11 → Clock     ├── GPIO 3 → SCL        ├── GPIO 27 → Green
├── GPIO 8  → CS        └── GPIO 4 → INT1       └── GPIO 22 → Blue
├── GPIO 25 → DC
├── GPIO 24 → RST
└── GPIO 18 → Backlight
```

---

## Interaction Model

### Dual Input System

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INTERACTION MODEL                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  TOUCH THE SCREEN                                                   │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  • Tap screen         →  Start voice recording                      │   │
│  │                          LED green, speak, auto-stops on silence    │   │
│  │                                                                     │   │
│  │  • Tap while message  →  Dismiss / next message                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  KNOCK ON BODY (the alien's head)                                   │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  • Single knock       →  <3  Love (ASCII heart pulses)              │   │
│  │  • Double knock       →  **  Fire (ASCII flames dance)              │   │
│  │  • Triple knock       →  :D  Haha (ASCII face bounces)              │   │
│  │  • Knock-knock        →  Wave "thinking of you" ping                │   │
│  │    (pause between)                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## ASCII Art Display System

**IMPORTANT**: The device display uses ASCII art animations, NOT standard emojis. This gives LEELOO a unique retro-tech aesthetic that matches the terminal-style UI.

### Reaction Animations

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ASCII REACTION ANIMATIONS                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LOVE REACTION (single knock)                                               │
│  ─────────────────────────────                                              │
│  Frame 1:        Frame 2:        Frame 3:                                   │
│                                                                             │
│    .:::.          .::.           .:::.                                      │
│   ::::::         ::::::         :::::::                                     │
│   ::::::         :::::::        :::::::                                     │
│    ::::           :::::          :::::                                      │
│     ::             :::            :::                                       │
│      :              :              :                                        │
│                                                                             │
│  (pulses 3x then fades)                                                     │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  FIRE REACTION (double knock)                                               │
│  ────────────────────────────                                               │
│  Frame 1:        Frame 2:        Frame 3:                                   │
│                                                                             │
│      )            (             )                                           │
│     ) \          ( /           ) \                                          │
│    (   )        )   (         (   )                                         │
│     ) (          ( )           ) (                                          │
│    (   )        )   (         (   )                                         │
│   __)  (__    __)   (__      __)  (__                                       │
│                                                                             │
│  (flames dance left/right)                                                  │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  HAHA REACTION (triple knock)                                               │
│  ────────────────────────────                                               │
│  Frame 1:        Frame 2:        Frame 3:                                   │
│                                                                             │
│    _____          _____          _____                                      │
│   /     \        /     \        /     \                                     │
│  | ^   ^ |      | >   < |      | ^   ^ |                                    │
│  |   >   |      |   >   |      |   >   |                                    │
│  |  ___  |      | \___/ |      |  ___  |                                    │
│   \_____/        \_____/        \_____/                                     │
│                                                                             │
│  (face bounces up/down, eyes squint)                                        │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  KNOCK-KNOCK PING                                                           │
│  ────────────────                                                           │
│  Frame 1:        Frame 2:        Frame 3:                                   │
│                                                                             │
│                     \               \  |  /                                 │
│      _              _|              _ \|/ _                                 │
│     | |            |  \            |       |                                │
│     | |             |  |            |  |  |                                 │
│     |_|             |__|            |__|__|                                 │
│                                                                             │
│  (hand waves)                                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Reaction Display Layout

When a reaction is received, it overlays the current screen:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│                                                                             │
│                            .:::.                                            │
│                           ::::::                                            │
│                           ::::::                                            │
│                            ::::                                             │
│                             ::                                              │
│                              :                                              │
│                                                                             │
│                        Amy loved this                                       │
│                                                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

(displays for 3 seconds, then fades back to previous screen)
```

### Knock-Knock Display

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │        \  |  /                                                        │  │
│  │       _ \|/ _          Ben knocked                                    │  │
│  │      |       |                                                        │  │
│  │       |  |  |                                                         │  │
│  │       |__|__|                                                         │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│                    [Normal display content below]                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

(LED pulses twice, banner fades after 5 seconds)
```

### ASCII Art Renderer

```python
# display/ascii_reactions.py

class ASCIIReactions:
    """ASCII art frames for reaction animations"""
    
    LOVE_FRAMES = [
        """
  .:::.
 ::::::
 ::::::
  ::::
   ::
    :
        """,
        """
   .::.
  ::::::
 :::::::
  :::::
   :::
    :
        """,
        """
  .:::.
 :::::::
 :::::::
  :::::
   :::
    :
        """
    ]
    
    FIRE_FRAMES = [
        """
    )
   ) \\
  (   )
   ) (
  (   )
 __)  (__
        """,
        """
    (
   ( /
  )   (
   ( )
  )   (
 __)   (__
        """,
        """
    )
   ) \\
  (   )
   ) (
  (   )
 __)  (__
        """
    ]
    
    HAHA_FRAMES = [
        """
  _____
 /     \\
| ^   ^ |
|   >   |
|  ___  |
 \\_____/
        """,
        """
  _____
 /     \\
| >   < |
|   >   |
| \\___/ |
 \\_____/
        """,
        """
  _____
 /     \\
| ^   ^ |
|   >   |
|  ___  |
 \\_____/
        """
    ]
    
    WAVE_FRAMES = [
        """
    
   _
  | |
  | |
  |_|
        """,
        """
     \\
    _|
   |  \\
    |  |
    |__|
        """,
        """
  \\  |  /
 _ \\|/ _
|       |
 |  |  |
 |__|__|
        """
    ]


class ReactionAnimator:
    """Animate ASCII reactions on the display"""
    
    def __init__(self, renderer):
        self.renderer = renderer
        self.frame_rate = 0.15  # seconds between frames
    
    async def play_reaction(self, reaction_type: str, sender: str):
        frames = {
            'love': ASCIIReactions.LOVE_FRAMES,
            'fire': ASCIIReactions.FIRE_FRAMES,
            'haha': ASCIIReactions.HAHA_FRAMES,
            'wave': ASCIIReactions.WAVE_FRAMES
        }[reaction_type]
        
        messages = {
            'love': f"{sender} loved this",
            'fire': f"{sender} thinks this is fire",
            'haha': f"{sender} is dying",
            'wave': f"{sender} knocked"
        }[reaction_type]
        
        # Play animation 2x
        for _ in range(2):
            for frame in frames:
                self.renderer.draw_reaction_overlay(frame, messages[reaction_type])
                await asyncio.sleep(self.frame_rate)
        
        # Hold final frame
        await asyncio.sleep(1.0)
        
        # Fade out
        self.renderer.clear_overlay()
```

---

## Device Software

### File Structure

```
leeloo-device/
├── main.py                    # Entry point
├── config.py                  # GPIO pins, API URLs
├── requirements.txt
│
├── display/
│   ├── renderer.py            # PIL rendering engine
│   ├── screens.py             # Screen layouts
│   ├── ascii_reactions.py     # ASCII art animations
│   ├── fonts/                 # Monospace TTF fonts
│   └── assets/                # Icons, backgrounds
│
├── hardware/
│   ├── lcd_driver.py          # Waveshare SPI driver
│   ├── touch_driver.py        # Touchscreen input
│   ├── accelerometer.py       # ADXL345 knock detection
│   ├── led.py                 # RGB LED control
│   └── audio.py               # Microphone recording
│
├── network/
│   ├── websocket_client.py    # Real-time backend connection
│   ├── api_client.py          # REST API calls
│   └── wifi_setup.py          # AP mode, captive portal
│
└── services/
    ├── state_manager.py       # Display state, unread counts
    ├── voice_handler.py       # Record → upload → handle
    ├── knock_handler.py       # Detect patterns, send reactions
    └── message_handler.py     # Process incoming updates
```

### Main Application

```python
# main.py

import asyncio
from hardware import LCD, Touch, Accelerometer, LED, Microphone
from network import WebSocketClient, APIClient
from display import Renderer, ReactionAnimator
from services import StateManager, VoiceHandler, KnockHandler

class LEELOOApp:
    def __init__(self):
        self.lcd = LCD()
        self.touch = Touch(on_tap=self.handle_screen_tap)
        self.accel = Accelerometer(on_knock=self.handle_knock)
        self.led = LED()
        self.mic = Microphone()
        
        self.ws = WebSocketClient(on_message=self.handle_server_message)
        self.api = APIClient()
        
        self.state = StateManager()
        self.renderer = Renderer(self.lcd)
        self.animator = ReactionAnimator(self.renderer)
        self.voice = VoiceHandler(self.mic, self.api, self.led)
        self.knock = KnockHandler(self.api)
    
    async def run(self):
        await self.ws.connect()
        self.touch.start()
        self.accel.start()
        
        while True:
            self.renderer.render(self.state.current)
            await asyncio.sleep(0.1)
    
    def handle_screen_tap(self):
        if self.state.has_visible_message:
            self.state.dismiss_current_message()
        else:
            asyncio.create_task(self.voice.start_recording())
    
    def handle_knock(self, pattern: str):
        # Pattern names map to reaction types
        reactions = {
            'single': 'love',
            'double': 'fire', 
            'triple': 'haha',
            'knock-knock': 'wave'
        }
        reaction = reactions.get(pattern)
        if reaction:
            asyncio.create_task(self.knock.send_reaction(reaction))
    
    def handle_server_message(self, msg):
        if msg['type'] == 'music_share':
            self.state.add_music(msg['data'])
        elif msg['type'] == 'reaction':
            # Play ASCII animation
            asyncio.create_task(
                self.animator.play_reaction(
                    msg['data']['reaction_type'],
                    msg['data']['sender']
                )
            )
        elif msg['type'] == 'knock':
            asyncio.create_task(
                self.animator.play_reaction('wave', msg['data']['sender'])
            )
```

---

## Setup & Pairing Flow

### Step 1: First Device Setup

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FIRST DEVICE SETUP                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Plug in LEELOO → Screen shows "Connect to LEELOO-A7X2 WiFi"             │
│                                                                             │
│  2. Connect phone to "LEELOO-A7X2" WiFi → Captive portal opens              │
│                                                                             │
│  3. Enter WiFi + name:                                                      │
│     ┌────────────────────────────────────────────┐                          │
│     │  🛸 Let's set up your LEELOO!              │                          │
│     │                                            │                          │
│     │  Your WiFi: [Home-WiFi-5G ▼]               │                          │
│     │  Password:  [••••••••••]                   │                          │
│     │  Your name: [Nathan]                       │                          │
│     │                                            │                          │
│     │  [Connect]                                 │                          │
│     └────────────────────────────────────────────┘                          │
│                                                                             │
│  4. Create group:                                                           │
│     ┌────────────────────────────────────────────┐                          │
│     │  ✅ Nathan's LEELOO is ready!              │                          │
│     │                                            │                          │
│     │  Name your crew:                           │                          │
│     │  [The Music Nerds]                         │                          │
│     │                                            │                          │
│     │  [Create Group]                            │                          │
│     └────────────────────────────────────────────┘                          │
│                                                                             │
│  5. Get invite link:                                                        │
│     ┌────────────────────────────────────────────┐                          │
│     │  🎉 "The Music Nerds" created!             │                          │
│     │                                            │                          │
│     │  Share this link:                          │                          │
│     │  leeloo.app/join/WXYZ123                   │                          │
│     │  [Copy] [Text] [Email]                     │                          │
│     │                                            │                          │
│     │  [Set Up Another LEELOO]  [Done]           │                          │
│     └────────────────────────────────────────────┘                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Step 2: Additional Devices

**Same house**: Nathan sets up Amy's LEELOO, portal auto-fills WiFi

**Remote friend**: Nathan texts link, Ben/Sarah set up with their own WiFi

---

## Phone Quick Guide

After setup completes, the phone shows an interactive quick guide. This is the LAST time the user needs their phone for LEELOO.

### Quick Guide Screens (Phone)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PHONE QUICK GUIDE                                   │
│                    (shown after setup completes)                            │
├─────────────────────────────────────────────────────────────────────────────┤

┌──────────────────────────────┐
│                              │
│  🛸 You're all set!          │
│                              │
│  Quick guide to your         │
│  new alien friend:           │
│                              │
│  Swipe to learn →            │
│                              │
│  ● ○ ○ ○ ○                   │
│                              │
└──────────────────────────────┘

                ↓ swipe

┌──────────────────────────────┐
│                              │
│  🎤 Share Music              │
│                              │
│  Touch the screen and say:   │
│                              │
│  "Share [song] by [artist]"  │
│                              │
│  Your alien sends it to      │
│  all your friends instantly. │
│                              │
│  ○ ● ○ ○ ○                   │
│                              │
└──────────────────────────────┘

                ↓ swipe

┌──────────────────────────────┐
│                              │
│  👊 Quick Reactions          │
│                              │
│  Knock on your alien's       │
│  head to react:              │
│                              │
│  1 knock = ❤️ Love           │
│  2 knocks = 🔥 Fire          │
│  3 knocks = 😂 Haha          │
│                              │
│  ○ ○ ● ○ ○                   │
│                              │
└──────────────────────────────┘

                ↓ swipe

┌──────────────────────────────┐
│                              │
│  👋 Thinking of You          │
│                              │
│  Miss your friend?           │
│                              │
│  Do a "knock-knock":         │
│  knock... pause... knock     │
│                              │
│  They'll see:                │
│  "[Your name] knocked"       │
│                              │
│  Like knocking on their      │
│  door from across the world. │
│                              │
│  ○ ○ ○ ● ○                   │
│                              │
└──────────────────────────────┘

                ↓ swipe

┌──────────────────────────────┐
│                              │
│  📼 Weekly Mixtape           │
│                              │
│  Every Sunday, your alien    │
│  creates a Spotify playlist  │
│  of everything you all       │
│  shared that week.           │
│                              │
│  One scan = full playlist.   │
│                              │
│  ○ ○ ○ ○ ●                   │
│                              │
└──────────────────────────────┘

                ↓ swipe

┌──────────────────────────────┐
│                              │
│                              │
│        📱 → 🛸               │
│                              │
│                              │
│  Now put your phone away     │
│  and enjoy tech that adds    │
│  value to your life.         │
│                              │
│                              │
│  More fun, less phone.       │
│                              │
│                              │
│  [Start Using LEELOO]        │
│                              │
│                              │
└──────────────────────────────┘

└─────────────────────────────────────────────────────────────────────────────┘
```

### Quick Guide Implementation

```html
<!-- templates/quick-guide.html -->
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, sans-serif;
            background: #1A1D2E;
            color: white;
            min-height: 100vh;
            overflow-x: hidden;
        }
        .slides {
            display: flex;
            transition: transform 0.3s ease;
        }
        .slide {
            min-width: 100vw;
            min-height: 100vh;
            padding: 40px 30px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            text-align: center;
        }
        .emoji { font-size: 48px; margin-bottom: 20px; }
        h2 { font-size: 24px; margin-bottom: 16px; }
        p { font-size: 18px; line-height: 1.5; color: #B8A9C9; margin-bottom: 12px; }
        .highlight { 
            background: #2A2D3E; 
            padding: 16px; 
            border-radius: 12px; 
            margin: 16px 0;
            font-family: monospace;
            font-size: 16px;
        }
        .dots {
            display: flex;
            justify-content: center;
            gap: 8px;
            margin-top: 30px;
        }
        .dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #4A4A6A;
        }
        .dot.active { background: #719253; }
        .final-slide {
            background: linear-gradient(180deg, #1A1D2E 0%, #2A3D2E 100%);
        }
        .final-slide .emoji { font-size: 64px; }
        .final-slide h2 { font-size: 20px; font-weight: normal; }
        .final-slide .tagline {
            font-size: 28px;
            font-weight: bold;
            color: #719253;
            margin-top: 20px;
        }
        button {
            background: #719253;
            color: white;
            border: none;
            padding: 16px 32px;
            border-radius: 12px;
            font-size: 18px;
            margin-top: 30px;
        }
        .reaction-list {
            text-align: left;
            max-width: 200px;
            margin: 20px auto;
        }
        .reaction-list div {
            padding: 8px 0;
            font-size: 18px;
        }
    </style>
</head>
<body>
    <div class="slides" id="slides">
        <!-- Slide 1: Welcome -->
        <div class="slide">
            <div class="emoji">🛸</div>
            <h2>You're all set!</h2>
            <p>Quick guide to your new alien friend.</p>
            <p style="color: #888;">Swipe to learn →</p>
            <div class="dots">
                <div class="dot active"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
        
        <!-- Slide 2: Share Music -->
        <div class="slide">
            <div class="emoji">🎤</div>
            <h2>Share Music</h2>
            <p>Touch the screen and say:</p>
            <div class="highlight">"Share [song] by [artist]"</div>
            <p>Your alien sends it to all your friends instantly.</p>
            <div class="dots">
                <div class="dot"></div>
                <div class="dot active"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
        
        <!-- Slide 3: Reactions -->
        <div class="slide">
            <div class="emoji">👊</div>
            <h2>Quick Reactions</h2>
            <p>Knock on your alien's head:</p>
            <div class="reaction-list">
                <div>1 knock = ❤️ Love</div>
                <div>2 knocks = 🔥 Fire</div>
                <div>3 knocks = 😂 Haha</div>
            </div>
            <div class="dots">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot active"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
        
        <!-- Slide 4: Knock-Knock -->
        <div class="slide">
            <div class="emoji">👋</div>
            <h2>Thinking of You</h2>
            <p>Miss your friend?</p>
            <div class="highlight">knock... pause... knock</div>
            <p>Like knocking on their door from across the world.</p>
            <div class="dots">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot active"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
        
        <!-- Slide 5: Mixtape -->
        <div class="slide">
            <div class="emoji">📼</div>
            <h2>Weekly Mixtape</h2>
            <p>Every Sunday, your alien creates a Spotify playlist of everything shared that week.</p>
            <p>One scan = full playlist.</p>
            <div class="dots">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot active"></div>
                <div class="dot"></div>
            </div>
        </div>
        
        <!-- Slide 6: Sign Off -->
        <div class="slide final-slide">
            <div class="emoji">📱 → 🛸</div>
            <h2>Now put your phone away and enjoy tech that adds value to your life.</h2>
            <div class="tagline">More fun, less phone.</div>
            <button onclick="window.location.href='/done'">Start Using LEELOO</button>
            <div class="dots">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot active"></div>
            </div>
        </div>
    </div>
    
    <script>
        let currentSlide = 0;
        const slides = document.getElementById('slides');
        const totalSlides = 6;
        
        // Swipe detection
        let startX = 0;
        slides.addEventListener('touchstart', e => startX = e.touches[0].clientX);
        slides.addEventListener('touchend', e => {
            const diff = startX - e.changedTouches[0].clientX;
            if (Math.abs(diff) > 50) {
                if (diff > 0 && currentSlide < totalSlides - 1) currentSlide++;
                else if (diff < 0 && currentSlide > 0) currentSlide--;
                slides.style.transform = `translateX(-${currentSlide * 100}vw)`;
                updateDots();
            }
        });
        
        function updateDots() {
            document.querySelectorAll('.slide').forEach((slide, i) => {
                const dots = slide.querySelectorAll('.dot');
                dots.forEach((dot, j) => {
                    dot.classList.toggle('active', j === currentSlide);
                });
            });
        }
    </script>
</body>
</html>
```

---

## User Flows

### Flow 1: Share Music with Message

```
Nathan: "Send sabotage by the beasties because my inlaws just showed up"

→ All devices show album art + Spotify code + "my inlaws just showed up lol"
→ Message fades after 30 seconds, music stays
```

### Flow 2: Reaction

```
Amy sees Nathan's share, knocks once on her alien

→ Amy's device: "Sent!" confirmation
→ Nathan's device: ASCII heart animation + "Amy loved this"
→ Animation fades after 3 seconds
```

### Flow 3: Knock-Knock

```
Ben misses his friends, does: knock... pause... knock

→ Ben's device: LED pulses, "Sent!"
→ All other devices: ASCII wave animation + "Ben knocked"
→ Friends can knock-knock back
```

### Flow 4: Missed Messages

```
Nathan wakes up, sees: "Amy (2)" in messages box

Nathan: "What did I miss?"

→ Shows message 1 of 3 with album art
→ Tap screen to cycle through
→ Unread counts clear
```

---

## Weekly Mixtape

Every Sunday at midnight, the backend creates a real Spotify playlist for each group.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│     __  __ _      _                                                         │
│    |  \/  (_)_  _| |_ __ _ _ __   ___                                       │
│    | |\/| | \ \/ / __/ _` | '_ \ / _ \                                      │
│    | |  | | |>  <| || (_| | |_) |  __/                                      │
│    |_|  |_|_/_/\_\\__\__,_| .__/ \___|                                      │
│                          |_|                                                │
│                                                                             │
│    The Music Nerds - Week of Feb 3                                          │
│                                                                             │
│    23 songs shared                                                          │
│                                                                             │
│    Nathan: 8  Amy: 6  Ben: 5  Sarah: 4                                      │
│                                                                             │
│    ┌─────────────────────────────┐                                          │
│    │                             │                                          │
│    │     [Spotify Scan Code]     │                                          │
│    │                             │                                          │
│    │     Scan for full playlist  │                                          │
│    │                             │                                          │
│    └─────────────────────────────┘                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema

```sql
-- Core tables
CREATE TABLE devices (
    id UUID PRIMARY KEY,
    hardware_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    last_seen TIMESTAMP
);

CREATE TABLE friend_groups (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    invite_code TEXT UNIQUE NOT NULL
);

CREATE TABLE device_group_members (
    device_id UUID REFERENCES devices,
    group_id UUID REFERENCES friend_groups,
    last_read_message_id UUID,
    PRIMARY KEY (device_id, group_id)
);

CREATE TABLE music_shares (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES friend_groups,
    sender_device_id UUID REFERENCES devices,
    spotify_track_id TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    album_art_url TEXT,
    spotify_uri TEXT NOT NULL,
    message TEXT,
    shared_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE reactions (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES friend_groups,
    sender_device_id UUID REFERENCES devices,
    target_share_id UUID REFERENCES music_shares,
    reaction_type TEXT NOT NULL,  -- 'love', 'fire', 'haha'
    sent_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE knocks (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES friend_groups,
    sender_device_id UUID REFERENCES devices,
    sent_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE mixtapes (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES friend_groups,
    spotify_playlist_uri TEXT NOT NULL,
    track_count INTEGER NOT NULL,
    week_start DATE NOT NULL
);
```

---

## API Specification

### REST Endpoints

```yaml
POST /api/device/register     → { device_id, api_key }
POST /api/group/create        → { group_id, invite_code }
POST /api/group/join          → { group_id, members }
POST /api/voice               → { intent, action }
POST /api/share/music         → { share_id }
POST /api/reaction            → { reaction_id }
POST /api/knock               → { knock_id }
GET  /api/messages/unread     → { counts, messages }
```

### WebSocket Messages

```yaml
# Server → Device
music_share:    { track, artist, album_art, code_url, message, sender }
reaction:       { reaction_type, sender }
knock:          { sender }
mixtape:        { scan_code_url, track_count, week_label }
```

---

## Spotify Integration

### Scan Code (Free, No Auth)

```python
def get_scan_code_url(spotify_uri: str) -> str:
    return f"https://scannables.scdn.co/uri/plain/png/1A1D2E/white/280/{spotify_uri}"
```

Works for tracks AND playlists.

### Track Search (Free API)

```python
# Requires Spotify Developer App (free)
results = spotify.search(q=query, type='track', limit=1)
```

### Playlist Creation (For Mixtape)

LEELOO has its own Spotify account that creates public playlists each week.

---

## Implementation Phases

| Phase | Week | Deliverable |
|-------|------|-------------|
| 1. Backend | 1-2 | API + WebSockets + DB |
| 2. Display | 3-4 | PIL rendering on LCD |
| 3. Spotify | 5 | Search + scan codes |
| 4. Voice | 6-7 | Touch → Whisper → Claude → share |
| 5. Reactions | 8 | Knock detection + ASCII animations |
| 6. Setup | 9 | Captive portal + phone guide |
| 7. Telegram | 10 | Alternative sharing |
| 8. Mixtape | 11 | Weekly playlist job |
| 9. Polish | 12 | Reliability + docs |

---

## Cost Estimates

### Per Device: ~$72

### Monthly Backend: ~$15-30 (for 1000 devices)

### Kickstarter Pricing

| Option | Price | Margin |
|--------|-------|--------|
| Pair (2) | $199 | 23% |
| Quad (4) | $349 | 13% |
| Add-on (1) | $119 | 35% |

---

## Summary

**LEELOO** = Physical music sharing with friends

- Touch screen → voice commands
- Knock on body → quick reactions (ASCII art animations)
- Knock-knock → "thinking of you" ping
- Weekly mixtape → one scan, full playlist
- Phone quick guide → then put your phone away

**More fun, less phone.**
