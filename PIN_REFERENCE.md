# LED Pin Fix - Quick Reference

## The Problem
- **Code expects:** GPIO 12 (Physical Pin 32)
- **Wire is on:** Physical Pin 11 (which is GPIO 17, not GPIO 12)

## The Solution
**Move the LED DIN wire from Pin 11 to Pin 32**

## Pin Layout (left side of GPIO header)
```
Pin 1  [3.3V]       ← ADXL345 VCC
Pin 3  [GPIO 2]     ← ADXL345 SDA
Pin 5  [GPIO 3]     ← ADXL345 SCL
Pin 7  [GPIO 4]     ← ADXL345 INT1
Pin 9  [GND]        ← ADXL345 GND
Pin 11 [GPIO 17]    ← LCD Touch IRQ (YOUR WIRE IS HERE - WRONG!)
Pin 13 [GPIO 27]
Pin 15 [GPIO 22]
Pin 17 [3.3V]       ← MIC VDD
Pin 19 [GPIO 10]    ← LCD MOSI
Pin 21 [GPIO 9]     ← LCD Touch SO
Pin 23 [GPIO 11]    ← LCD SCLK
Pin 25 [GND]
Pin 27 [GPIO 0]
Pin 29 [GPIO 5]
Pin 31 [GPIO 6]
Pin 33 [GPIO 13]
Pin 35 [GPIO 19]    ← MIC WS
Pin 37 [GPIO 26]
Pin 39 [GND]
```

## Pin Layout (right side of GPIO header)
```
Pin 2  [5V]         ← LED 5V ✓
Pin 4  [5V]
Pin 6  [GND]        ← LED GND ✓
Pin 8  [GPIO 14]
Pin 10 [GPIO 15]
Pin 12 [GPIO 18]    ← MIC SCK
Pin 14 [GND]
Pin 16 [GPIO 23]
Pin 18 [GPIO 24]    ← LCD DC
Pin 20 [GND]        ← MIC GND + L/R
Pin 22 [GPIO 25]    ← LCD RST
Pin 24 [GPIO 8]     ← LCD CS
Pin 26 [GPIO 7]     ← LCD Touch CS
Pin 28 [GPIO 1]
Pin 30 [GND]
Pin 32 [GPIO 12]    ← LED DIN (MOVE WIRE HERE!) ★★★
Pin 34 [GND]
Pin 36 [GPIO 16]
Pin 38 [GPIO 20]    ← MIC SD
Pin 40 [GPIO 21]
```

## Physical Location
Pin 32 is on the **right side** of the GPIO header, **16 pins down from the top**.

Count from the top right:
- Top right = Pin 2 (5V) ← LED 5V is here
- Pin 4, 6 (GND), 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30
- **Pin 32** ← Move LED DIN here (16th pin on right side)

## After Moving the Wire
Test with:
```bash
sudo python3 test_led_live.py
```

You should see the LEDs light up!
