# Gadget Wiring Reference Guide
## Pi Zero 2 W — Complete Pin Map

**Date:** February 2026
**Components:** Waveshare 3.5" LCD (A/B/C), INMP441 Mic, ADXL345 Accelerometer, WS2812B LED

---

## Important Notes Before You Start

- The Waveshare 3.5" LCD plugs on top as a hat — it covers all GPIO pins
- Solder wires from the back of the Pi for the mic, accelerometer, and LED
- Route wires out to the side before mounting the LCD hat
- LCD backlight is always on — GPIO 18 is freed up for the mic
- The mic runs on 3.3V, the LED strip runs on 5V — don't mix them up
- Add `dtparam=audio=off` to `/boot/config.txt` — you're using I2S, not analog audio
- The LCD hat physically connects touch pins (GPIO 7, 9, 17) even if touch isn't used

---

## Raspberry Pi Zero 2 W Pin Reference

```
                    Pi Zero 2 W (pin side facing you)
                    ┌─────────────────────────┐
    ADXL VCC  3.3V  1 │ ●  ●  │ 2   5V    LED 5V
    ADXL SDA (GPIO 2)  3 │ ●  ●  │ 4   --
    ADXL SCL (GPIO 3)  5 │ ●  ●  │ 6   LED GND
   ADXL INT1 (GPIO 4)  7 │ ●  ●  │ 8   --
            ADXL GND  9 │ ●  ●  │ 10  (LCD SPI)
      (LCD Touch IRQ) 11 │ ●  ●  │ 12  MIC SCK (GPIO 18)
                   -- 13 │ ●  ●  │ 14  --
                   -- 15 │ ●  ●  │ 16  --
             MIC VDD 17 │ ●  ●  │ 18  (LCD DC — GPIO 24)
          (LCD MOSI) 19 │ ●  ●  │ 20  MIC GND + MIC L/R
      (LCD Touch SO) 21 │ ●  ●  │ 22  (LCD RST — GPIO 25)
          (LCD SCLK) 23 │ ●  ●  │ 24  (LCD CS)
                   -- 25 │ ●  ●  │ 26  (LCD Touch CS)
                   -- 27 │ ●  ●  │ 28  --
                   -- 29 │ ●  ●  │ 30  --
                   -- 31 │ ●  ●  │ 32  LED DIN (GPIO 12)
                   -- 33 │ ●  ●  │ 34  --
   MIC WS (GPIO 19) 35 │ ●  ●  │ 36  --
                   -- 37 │ ●  ●  │ 38  MIC SD (GPIO 20)
                   -- 39 │ ●  ●  │ 40  --
                    └─────────────────────────┘
```

---

## Component 1: Waveshare 3.5" LCD (Hat)

**Connection:** Plugs directly onto GPIO header — no soldering needed

| Function     | GPIO    | Pi Pin # | Notes                          |
|-------------|---------|----------|--------------------------------|
| SPI MOSI    | GPIO 10 | Pin 19   | Data to LCD                    |
| SPI SCLK    | GPIO 11 | Pin 23   | Clock                          |
| SPI CS      | GPIO 8  | Pin 24   | Chip select (CE0)              |
| DC          | GPIO 24 | Pin 18   | Data/Command                   |
| RST         | GPIO 25 | Pin 22   | Reset                          |
| Touch CS    | GPIO 7  | Pin 26   | Touch chip select (CE1)        |
| Touch MISO  | GPIO 9  | Pin 21   | Touch data return              |
| Touch IRQ   | GPIO 17 | Pin 11   | Touch interrupt (not used)     |
| Backlight   | GPIO 18 | Pin 12   | NOT CONNECTED by default — always on |

Nothing to solder here — just push the hat onto the Pi after everything else is wired.

---

## Component 2: INMP441 Microphone (I2S)

**Connection:** 6 wires soldered from back of Pi

| INMP441 Pin | Connect To | Pi Pin # | Wire Color Suggestion |
|------------|-----------|----------|----------------------|
| VDD        | 3.3V      | Pin 17   | Red                  |
| GND        | Ground    | Pin 20   | Black                |
| WS         | GPIO 19   | Pin 35   | Yellow               |
| SCK        | GPIO 18   | Pin 12   | Orange               |
| SD         | GPIO 20   | Pin 38   | Green                |
| L/R        | Ground    | Pin 20   | Black (share with GND) |

**Notes:**
- L/R tied to GND selects left channel — never leave it floating
- VDD is 3.3V (1.8V–3.3V range) — do NOT connect to 5V
- SCK uses GPIO 18 (LCD backlight is not connected by default, so no conflict)
- Requires `dtparam=i2s=on` in `/boot/config.txt`

---

## Component 3: ADXL345 Accelerometer (I2C)

**Connection:** 5 wires soldered from back of Pi

| ADXL345 Pin | Connect To | Pi Pin # | Wire Color Suggestion |
|------------|-----------|----------|----------------------|
| VCC        | 3.3V      | Pin 1    | Red                  |
| GND        | Ground    | Pin 9    | Black                |
| SDA        | GPIO 2    | Pin 3    | Blue                 |
| SCL        | GPIO 3    | Pin 5    | Purple               |
| INT1       | GPIO 4    | Pin 7    | White                |

**Notes:**
- INT1 is for hardware tap detection interrupts
- I2C address is 0x53 (default, SDO low) or 0x1D (SDO high)
- VCC is 3.3V — do NOT connect to 5V
- No external pull-ups needed — Pi has 1.8k pull-ups on SDA/SCL

---

## Component 4: WS2812B LED Strip (NeoPixel)

**Connection:** 3 wires + 330-ohm resistor

| LED Strip Pin | Connect To                        | Pi Pin # | Wire Color Suggestion |
|--------------|----------------------------------|----------|----------------------|
| 5V           | 5V                               | Pin 2    | Red                  |
| GND          | Ground                           | Pin 6    | Black                |
| DIN          | GPIO 12 through 330-ohm resistor | Pin 32   | Green                |

**Notes:**
- GPIO 12 uses PWM0 — the only NeoPixel-compatible pin available in this build
- Requires `dtparam=audio=off` in `/boot/config.txt` to free the PWM hardware
- Cut just 1–3 LEDs from the strip for status indication
- This LED runs on 5V (not 3.3V like the other components)
- The 330-ohm resistor protects the data line (300–500 ohm range is fine)

### Resistor Wiring Detail

```
Pi Pin 32 (GPIO 12) ───[ 330Ω ]─── DIN on LED strip
```

### Why GPIO 12?

The `rpi_ws281x` library only works on pins with PWM, PCM, or SPI hardware:
- GPIO 18 (PWM0) — taken by I2S mic
- GPIO 10 (SPI) — taken by LCD, and SPI can't be shared with NeoPixels
- GPIO 21 (PCM) — same hardware block as I2S
- **GPIO 12 (PWM0)** — free, works with `dtparam=audio=off`

---

## Soldering Order (Recommended)

1. **ADXL345** — Pins 1, 3, 5, 7, 9 (top of header, easy to reach)
2. **INMP441** — Pins 12, 17, 20, 35, 38 (spread across header)
3. **WS2812B LED** — Pins 2, 6, 32 (don't forget the resistor on DIN)
4. Route all wires out to one side
5. Mount LCD hat on top last

---

## /boot/config.txt Additions

```ini
dtparam=i2c_arm=on
dtparam=i2s=on
dtparam=spi=on
dtparam=audio=off
```

---

## Quick Test Commands (After Wiring)

### Test I2C (Accelerometer)
```bash
sudo i2cdetect -y 1  # Should show device at 0x53
```

### Test I2S (Microphone)
```bash
arecord -l  # Should list the INMP441 as a capture device
```

### Test LED
```python
import board
import neopixel
pixels = neopixel.NeoPixel(board.D12, 1)
pixels[0] = (0, 255, 0)  # Green = success!
```

---

## Pin Conflict Summary

| GPIO | Used By          | Protocol  |
|------|-----------------|-----------|
| 2    | ADXL345 SDA      | I2C       |
| 3    | ADXL345 SCL      | I2C       |
| 4    | ADXL345 INT1     | Interrupt |
| 7    | LCD Touch CS     | SPI (hat) |
| 8    | LCD CS           | SPI       |
| 9    | LCD Touch MISO   | SPI (hat) |
| 10   | LCD MOSI         | SPI       |
| 11   | LCD SCLK         | SPI       |
| 12   | WS2812B DIN      | PWM0      |
| 17   | LCD Touch IRQ    | GPIO (hat)|
| 18   | INMP441 SCK      | I2S       |
| 19   | INMP441 WS       | I2S       |
| 20   | INMP441 SD       | I2S       |
| 24   | LCD DC           | GPIO      |
| 25   | LCD RST          | GPIO      |

**No conflicts. All components use separate pins and protocols.**

---

## Known Gotchas

- **EMI from SPI LCD**: The LCD hat can cause white noise on the INMP441 if physically stacked directly above. Mount the mic with some distance if audio quality matters.
- **Low mic volume**: The INMP441 output is quiet — you'll likely need ALSA softvol or software gain.
- **3.3V logic to 5V LED**: GPIO 12 outputs 3.3V but WS2812B wants 5V logic. For 1–3 LEDs with short wires this usually works fine. For reliability, add a 74AHCT125 level shifter.
