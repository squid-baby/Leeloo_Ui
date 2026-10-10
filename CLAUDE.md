# LEELOO Project

> Voice-interactive music sharing gadget for Raspberry Pi with 3.5" LCD display

## Quick Start

```bash
# SSH into Pi
sshpass -p 'gadget' ssh pi@leeloo.local

# Deploy code changes
sshpass -p 'gadget' scp leeloo_brain.py pi@leeloo.local:/home/pi/leeloo-ui/
sshpass -p 'gadget' ssh pi@leeloo.local "sudo systemctl restart leeloo.service"

# View logs
sshpass -p 'gadget' ssh pi@leeloo.local "sudo journalctl -u leeloo.service -f"

# Run manually (for debugging)
sshpass -p 'gadget' ssh pi@leeloo.local "sudo systemctl stop leeloo.service && cd /home/pi/leeloo-ui && sudo python3 leeloo_brain.py"
```

## Architecture

**Main Entry Point:** `leeloo_brain.py` (asyncio orchestrator)

```
leeloo_boot.py                  # Boot sequence, first-run portal
  → captive_portal.py           # WiFi setup (AP mode)
  → leeloo_brain.py             # Main brain

LeelooBrain (asyncio):
  ├── _display_loop()           # 1fps render (thread executor)
  ├── _voice_interaction()      # Tap→STT→Claude→action
  ├── _update_music()           # Spotify polling (tiered: 20s/60s/120s)
  └── expand_frame()            # Typewriter + smooth scroll

Subsystems:
  ├── TapManager                # ADXL345 tap detection
  ├── VoiceManager              # INMP441→Deepgram STT
  ├── IntentRouter              # Claude Haiku classification
  ├── LeelooDisplay             # PIL→framebuffer rendering
  └── LeelooClient              # WebSocket relay (wss://leeloobot.xyz/ws)
```

## Hardware (Raspberry Pi Zero 2 W)

| Component | Connection | Pin | Notes |
|-----------|-----------|-----|-------|
| **Waveshare 3.5" LCD** | SPI, fb1 | - | 480x320, ILI9486 driver |
| **ADXL345 Accel** | I2C (0x53) | GPIO 2,3,4 | Tap detection |
| **INMP441 Mic** | I2S | GPIO 18,19,20 | 16kHz mono |
| **WS2812B LEDs** | GPIO 12 (PWM) | Pin 32 | 3 LEDs, **wire on Pin 32 NOT Pin 11** |

**Critical:** `fbcon=map:0` in `/boot/firmware/cmdline.txt` keeps console on fb0

## Voice Commands

Tap device → speak → Haiku classifies:

| Intent | Trigger | Action |
|--------|---------|--------|
| `WEATHER_EXPAND` | "what's the weather" | Show 24hr rain viz (60min bars) |
| `ALBUM_INFO` | "tell me about this band" | Artist facts (Haiku, 1.5s) |
| `SONG_PUSH` | "share [song name]" | Spotify search + validate + push |
| `MESSAGE_SEND` | "tell them [message]" | Send to crew |
| `MESSAGE_READOUT` | "what did I miss" | Read messages |
| `TIME_QUERY` | "what time is it" | Tolkien-style cosmic time (65 words) |
| `NUDGE` | "send a nudge" | Cyan LED ping |

## Key Conventions

### Code Style
- **Prefix:** All device modules use `leeloo_` prefix
- **Async:** Never block event loop (use `run_in_executor()` for >50ms work)
- **Display:** 480x320 PIL Image → RGB565 → row-by-row fb1 writes
- **Album art:** 243x304 (244px art + 60px scancode/bar)
- **Colors:** Defined in `COLORS` dict (navy bg, green/purple/lavender/tan/rose accents)

### Critical Patterns
```python
# ✅ GOOD: Offload display rendering
await loop.run_in_executor(None, self._render_normal)

# ❌ BAD: Blocking on event loop
self._render_normal()  # 200-500ms on Pi Zero!

# ✅ GOOD: WebSocket safety
data.get('field', default_value)

# ❌ BAD: Bracket access
data['field']  # KeyError crashes device

# ✅ GOOD: Spotify search validation
if query_word in artist_name or query_word in track_name:
    return result  # Valid match

# ❌ BAD: Accept any result
return tracks[0]  # Spotify returns fuzzy matches!
```

### Display Constraints
- **Screen:** 480x320, no vsync (row-by-row writes prevent tearing)
- **Framebuffer:** `/dev/fb1` (fb0 = HDMI console)
- **RGB565:** `((r>>3)<<11) | ((g>>2)<<5) | (b>>3)`
- **Fonts:** DejaVuSansMono (16px normal, 20px large, 12px small)

## Development Workflow

### Local Testing
```bash
# Preview mode (no Pi needed)
python3 gadget_display.py  # Creates /tmp/leeloo_preview.png

# Run demos
python3 demos/demo_weather_typewriter.py
python3 demos/demo_message_expand.py
```

### Deploy to Pi
1. Edit code locally
2. `scp` to Pi
3. `systemctl restart leeloo.service`
4. Check logs with `journalctl -f`

### Debug on Pi
```bash
# Stop service, run manually
sudo systemctl stop leeloo.service
cd /home/pi/leeloo-ui
sudo python3 leeloo_brain.py  # See print() output

# Check tap detection
sudo python3 leeloo_tap.py  # prints detected taps

# Test display
sudo python3 show_on_display.py
```

## Configuration Files

| File | Purpose | Location |
|------|---------|----------|
| `.env` | API keys (Deepgram, Anthropic, Spotify) | `/home/pi/leeloo-ui/.env` |
| `device_config.json` | WiFi, user, location | Created by portal |
| `crew_config.json` | Crew code, members | Created by portal |
| `spotify_tokens.json` | OAuth tokens (auto-refresh) | Created by OAuth |
| `current_music.json` | Cached music display data | Updated every 20-120s |

## Critical Gotchas

### 1. **Asyncio Guard Flags**
Set flags **BEFORE** `create_task()`, not inside:
```python
# ✅ GOOD
self._welcome_qr_active = True
asyncio.create_task(self._show_welcome())

# ❌ BAD
asyncio.create_task(self._show_welcome())  # Task doesn't run immediately!
# Inside task:
self._welcome_qr_active = True  # Too late, display loop already fired
```

### 2. **Spotify Search Validation**
Spotify returns fuzzy matches for ANY query. Must validate:
```python
# Extract query words
query_words = [w for w in query.split() if w not in {'the', 'and', 'a'}]

# Check if ANY word appears in result
if not any(w in artist.lower() or w in track.lower() for w in query_words):
    return None  # Reject poor match
```

### 3. **NetworkManager After AP Mode**
```bash
# MUST restart NetworkManager after captive portal
sudo systemctl start NetworkManager
nmcli device wifi connect "SSID" password "..."
```

### 4. **Album Art Cache Invalidation**
When changing image dimensions, **delete stale cache first**:
```python
if os.path.exists(cached_path):
    os.remove(cached_path)  # Clear before re-downloading
```

### 5. **Music Polling (Tiered)**
- 20s when actively playing
- 60s when recently played (<5min)
- 120s when idle
- Saves 54% API calls (~2,000/day vs 4,320/day)

### 6. **Display Render Latency**
PIL rendering takes 200-500ms on Pi Zero. Always use `run_in_executor()`:
```python
await loop.run_in_executor(None, write_to_framebuffer, img)
```

### 7. **Scroll Timing**
- Typewriter: 25ms/char, 80ms line pause
- Overflow: Immediate smooth scroll (no 2s pause)
- Hold: 20s for info frames

### 8. **Pin Numbering**
- **Physical Pin 11** = GPIO 17 (LCD touch IRQ)
- **Physical Pin 32** = GPIO 12 (LED DIN) ← **Check this first if LEDs don't work!**

### 9. **Anthropic Model Names**
All Claude calls use the `HAIKU_MODEL` constant from `leeloo_intent.py` (currently `claude-haiku-5-5`; `claude-3-haiku-20240307` was retired April 2026). Change the model in that one place only.
```python
# ✅ Import the constant, disable thinking for short calls, read the first text block
from leeloo_intent import HAIKU_MODEL, THINKING_DISABLED, extract_text
resp = client.messages.create(model=HAIKU_MODEL, max_tokens=300,
                              thinking=THINKING_DISABLED, messages=[...])
text = extract_text(resp)  # None on refusal / no text block -> use fallback

# ❌ Don't use -latest aliases, hardcoded model strings, or response.content[0].text
model="claude-3-5-haiku-latest"  # 404
# ❌ Haiku 5.5 returns HTTP 400 for: temperature/top_p/top_k, budget_tokens, assistant prefill
```
A 404 (`anthropic.NotFoundError`) logs a loud "model NOT FOUND" banner: the model ID is retired or wrong.

### 10. **OAuth State Parameter**
```python
# state MUST match device_id
state = self.ws_client.config.device_id
# NOT a new UUID or random string!
```

### 11. **WS2812B ↔ INMP441 DMA Conflict**
WS2812B (GPIO 12 PWM DMA) and INMP441 (I2S DMA) share the Pi Zero DMA controller. Any LED activity during arecord causes "Interrupted system call" / 0 audio chunks.
```python
# ✅ Always cancel ambient before voice recording
await self.led._cancel_ambient()
# ❌ Never run a continuous LED loop while arecord is active
```

### 12. **`current_music.json` `is_playing` is Stale**
The JSON cache reflects playback state at write time — never trust it as live. Only treat `is_playing=True` as real if `spotify_tokens.json` exists (live OAuth connection).
```python
has_live_spotify = os.path.exists(spotify_tokens_path)
is_playing = has_live_spotify and self.music_data.get('is_playing', False)
```

### 13. **Asyncio Tight Loops Block Audio on Pi Zero**
Any loop without `await` (even CPU-only work like off-screen PIL draws) blocks the event loop. On Pi Zero single core this starves arecord's stdout read → 0 chunks / Read timeout.
```python
# ✅ Always yield at least once per loop iteration
await asyncio.sleep(0)  # costs nothing, keeps event loop alive
```

### 14. **Boot Splash Image**
File is `LeeLoo_boot.png` in `/home/pi/leeloo-ui/` — scaled to fill 480x320 at boot. Replace by scp-ing new PNG as that exact filename. No code change needed.
```bash
sshpass -p 'gadget' scp new_image.png pi@leeloo.local:/home/pi/leeloo-ui/LeeLoo_boot.png
```

### 15. **Show Expanded Frames Only Through `_start_expansion()`**
Never `create_task(self.expand_frame(...))` directly. `_start_expansion()` cancels the running and queued expansions and serialises them with a lock, so two frames never draw at once and an old task never resets `ui_state` after a new one starts. Cancel with `_cancel_expansions()`.
```python
# ✅ GOOD
self._start_expansion(FrameType.MESSAGES, lines, duration=EXPANDED_HOLD_DURATION)
# ❌ BAD — old expansion keeps drawing; it even cancelled itself before
self._expand_task = asyncio.create_task(self.expand_frame(...))
```

### 16. **LED Recording Guard (DMA, see #11)**
`_voice_interaction` calls `await self.led.recording_started()` before `arecord` and `self.led.recording_finished()` in `finally`. While the guard is on, every LED animation is skipped and `set_ambient()` only stores the state. Incoming messages / nudges during recording no longer kill the audio. Keep new LED effects inside `_run_animation()` so they respect the guard.

### 17. **Threads: Display Lock, Music Lock, Loop Handle**
Executor threads and the event loop share `self.display` and the framebuffer: hold `self._display_lock` for render + full-frame writes. Spotify refresh + `current_music.json` writes hold `self._music_lock`. Never hold either across an `await`. To start a coroutine from a thread, use `asyncio.run_coroutine_threadsafe(coro, self._loop)` — `ensure_future` raises in a thread.

### 18. **Power-Safe JSON Writes**
The device can lose power any time. Write config/state JSON with `leeloo_fileutil.atomic_write_json()` (temp file + `os.replace`), never `open(path, 'w')`. Use `mode=0o600` for secrets (`device_config.json` holds the WiFi password, `spotify_tokens.json`). Use `durable=False` only for small writes on the event loop (skips a slow SD-card fsync).

### 19. **WiFi Setup Rules**
- Never `shell=True` with SSIDs or passwords (portal input runs as root) — argv lists only.
- `setup_complete` is written **only after** WiFi is confirmed connected. A wrong password brings the setup AP back with an LCD error.
- Boot recovery: no WiFi for ~3 min → WiFi-only recovery portal; no input for ~10 min → retry saved WiFi; repeat.
- The portal server shuts down after setup and refuses setup POSTs afterwards.

### 20. **Python 3.9 Compatibility**
Pi OS Bullseye ships Python 3.9. No `str | None` annotations (use `Optional[str]`), no `Task.cancelling()`, no `asyncio.timeout()`, no `match`. `tests/test_py39_compat.py` checks annotations.

## Testing

### Automated Tests (run these before every deploy)
```bash
# One time: install test tools on your laptop (not needed on the Pi)
pip install -r requirements.txt -r requirements-dev.txt

# Run all tests (no Pi hardware, network or API keys needed)
python3 -m pytest -q

# Run one file, with names of each test
python3 -m pytest -v tests/test_tap.py
```
Every new feature or fix gets a test in `tests/` that **fails before** the change and **passes after** it. Hardware (LEDs, accelerometer) runs in mock mode in tests.

### Without Hardware
```python
display = LeelooDisplay(preview_mode=True)
display.render(weather, time, contacts, music)
display.show()  # Opens /tmp/leeloo_preview.png
```

### With Hardware
```bash
# Fire.gif animation in message frame
sudo systemctl stop leeloo.service
sudo python3 demos/demo_message_expand.py

# Weather expand with rain viz
sudo python3 demos/demo_weather_typewriter.py

# Tap detection
sudo python3 leeloo_tap.py  # prints detected taps
```

## Server Deployment

**Relay:** `leeloobot.xyz` (Node.js + PM2)
```bash
# Deploy
scp server.js root@leeloobot.xyz:/root/leeloo-relay/
ssh root@leeloobot.xyz "pm2 restart leeloo-relay"

# Logs
ssh root@leeloobot.xyz "pm2 logs leeloo-relay --lines 20"
```

**Telegram Bot:** `leeloo_server/telegram_bot.py` (systemd service, **long polling** — no webhook)
- Talks to the relay's `/api/telegram/*` endpoints on localhost
- Bot: `@Leeloo2259_bot`
- `relay_server.py` is old and not deployed; `server.js` is the live relay

## Environment Variables

```bash
# /home/pi/leeloo-ui/.env
DEEPGRAM_API_KEY=...           # Nova-2 STT
ANTHROPIC_API_KEY=...          # Claude Haiku
SPOTIFY_CLIENT_ID=...          # Web API
SPOTIFY_CLIENT_SECRET=...      # Client credentials flow + token refresh
```

**Spotify secrets live in `.env` only.** Never hardcode `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` in source (this repo is public). `leeloo_music_manager.py` reads them from `os.environ`, loading `.env` itself for keys not already set; if they are missing it prints one warning and token refresh / artist lookups return `None`.

## Don't Do This

- ❌ Use `wpa_supplicant` (use NetworkManager/nmcli)
- ❌ Call external APIs in AP mode (no internet)
- ❌ Use `navigator.clipboard` in portal (no HTTPS)
- ❌ Bracket-access WebSocket data (`data['field']`)
- ❌ Accept first Spotify result without validation
- ❌ Block asyncio event loop with PIL rendering
- ❌ Use `-latest` suffix for Anthropic models
- ❌ Create album art at different sizes per script
- ❌ Use `systemd.mask=` without reboot (persists!)
- ❌ Trust "smoke and intolerance" searches 😅
- ❌ Run continuous LED animations (ambient breathe) while voice recording — DMA conflict kills audio
- ❌ Trust `is_playing` from `current_music.json` without checking `spotify_tokens.json` exists
- ❌ Write off-screen content in a tight loop without `await asyncio.sleep(0)` — blocks I2S reads
- ❌ Call `expand_frame()` through `create_task` — use `_start_expansion()`
- ❌ Write JSON state with `open(path, 'w')` — use `atomic_write_json()`
- ❌ Hardcode model IDs or Spotify credentials — `HAIKU_MODEL` constant, `.env`
- ❌ Use `shell=True` with any user-provided value
- ❌ Call `asyncio.ensure_future()` from an executor thread

## Tuning Values

| Parameter | Value | Notes |
|-----------|-------|-------|
| `TAP_THRESHOLD` | 1.2 m/s² | Lower = more sensitive |
| `DOUBLE_TAP_WINDOW` | 0.55s | Max gap between taps; also single-tap delay |
| `DEBOUNCE_TIME` | 0.18s | Rejects rebound; must stay < double-tap window |
| `SETTLE_TIME` | 0.08s | Blind time after a spike (~0.16s total with drain) |
| Music polling (active) | 20s | When is_playing=true |
| Music polling (recent) | 60s | Last played <5min |
| Music polling (idle) | 120s | Idle state |
| Typewriter delay | 25ms/char | Character animation |
| Expanded hold | 20s | Info frame display |
| Shared music timeout | 30min | Priority over now-playing |
| Ambient LED peak brightness | 50% | `AMBIENT_PEAK` in `leeloo_led.py` |
| Ambient LED cycle | 8s | `AMBIENT_CYCLE_S` in `leeloo_led.py` |
| Ambient LED framerate | 10fps (0.1s sleep) | Safe on Pi Zero, visually smooth |

## File Structure

```
/home/pi/leeloo-ui/
├── leeloo_brain.py          # Main entry point
├── leeloo_boot.py           # Boot sequence
├── leeloo_*.py              # Core modules
├── gadget_display.py        # Display renderer
├── display/                 # Frame animator, reactions
├── boot/                    # Systemd service, splash
├── demos/                   # Demo scripts (manual, on hardware)
├── tests/                   # pytest suite (runs without hardware)
├── leeloo_fileutil.py       # atomic_write_json (power-safe writes)
├── album_art/               # Cached album art
├── .env                     # API keys (gitignored)
├── device_config.json       # WiFi, user, location
├── crew_config.json         # Crew setup
└── spotify_tokens.json      # OAuth tokens
```

## Service Management

```bash
# Restart after code changes
sudo systemctl restart leeloo.service

# View status
sudo systemctl status leeloo.service

# Live logs
sudo journalctl -u leeloo.service -f

# Stop for manual run
sudo systemctl stop leeloo.service
```

## Documentation

- `README.md` - Project overview
- `docs/` - Detailed documentation (see below)
- `archive/` - Historical fixes and session logs
- `.claude.local.md` - Personal dev preferences (gitignored)

---

**Quick Tip:** Press `#` during any Claude session to auto-update this file with learnings!
