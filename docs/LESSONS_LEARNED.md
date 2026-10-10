# LEELOO Lessons Learned

> Critical lessons from building a voice-interactive music sharing gadget

## Top 15 Production Lessons

### 1. **Asyncio Guard Flags Must Be Set Before create_task()**
**Problem:** Display loop overwrites QR codes before welcome screen task runs

**Why:** `create_task()` doesn't execute immediately—returns control to event loop first

**Solution:**
```python
# ✅ Set flag BEFORE creating task
self._welcome_qr_active = True
asyncio.create_task(self._show_welcome())

# ❌ NOT inside the task
async def _show_welcome():
    self._welcome_qr_active = True  # Too late!
```

### 2. **Spotify Always Returns Results (Even for Garbage)**
**Problem:** "smoke and intolerance" → Jon Pardi song (completely unrelated)

**Why:** Spotify uses fuzzy matching, never returns empty results

**Solution:** Validate query terms appear in artist/track name:
```python
query_words = [w for w in query.split() if w not in {'the', 'and', 'a'}]
if not any(w in artist.lower() or w in track.lower() for w in query_words):
    return None  # Reject hallucinated match
```

### 3. **NetworkManager MUST Restart After AP Mode**
**Problem:** WiFi permanently broken after captive portal exits

**Why:** `systemd.mask=` persists until reboot, can't unmask at runtime

**Solution:**
```bash
# connect_saved_wifi.py
sudo systemctl start NetworkManager  # Critical first step!
nmcli device wifi connect "$SSID" password "$PASS"
```

### 4. **Pi Zero: Offload >50ms Work to Executor**
**Problem:** Tap detection misses taps, voice interaction sluggish

**Why:** PIL rendering blocks event loop for 200-500ms on Pi Zero

**Solution:**
```python
# ✅ Non-blocking
await loop.run_in_executor(None, self._render_normal)

# ❌ Blocks event loop
self._render_normal()  # Starves tap polling!
```

### 5. **Cache Invalidation When Changing Dimensions**
**Problem:** Album art shows at wrong size after code changes

**Why:** Cached files persist with old dimensions

**Solution:**
```python
if os.path.exists(cached_path):
    os.remove(cached_path)  # Delete before re-downloading
album_art = download_and_create_album_art(...)
```

### 6. **WebSocket Data: Always Use .get() with Defaults**
**Problem:** Device crashes on unexpected server responses

**Why:** Bracket access (`data['field']`) throws KeyError

**Solution:**
```python
# ✅ Safe
artist = data.get('artist', 'Unknown')
tracks = data.get('tracks', {}).get('items', [])

# ❌ Crashes device
artist = data['artist']  # KeyError if missing!
```

### 7. **OAuth State Parameter MUST Match device_id**
**Problem:** Tokens delivered to wrong device or lost

**Why:** Server uses `state` to identify which WebSocket to send tokens to

**Solution:**
```python
# ✅ Use actual device_id from WebSocket connection
state = self.ws_client.config.device_id

# ❌ Random UUID breaks delivery
state = str(uuid.uuid4())
```

### 8. **Captive Portal Can't Access Internet**
**Problem:** Portal tried to open OAuth URLs, failed silently

**Why:** AP mode has no upstream internet connection

**Solution:** Show QR codes for out-of-band auth, not direct links

### 9. **Anthropic Model Names: One Constant, Watch Retirements**
**Problem (2026-04):** Every voice command showed "something went wrong". `claude-3-haiku-20240307` was retired on 2026-04-20 and every call returned 404. The error was caught by a generic `except`, so nothing looked broken in the logs.

**Solution:**
- One constant: `HAIKU_MODEL` in `leeloo_intent.py` (now `claude-haiku-5-5`). All three Claude calls import it.
- `anthropic.NotFoundError` is caught separately and prints a loud "model NOT FOUND" banner.
- Haiku 5.5 thinks by default: send `thinking={"type": "disabled"}` for short calls (lower latency). It rejects `temperature`, `budget_tokens` and assistant prefill with HTTP 400.
- Read the first `type == "text"` block, never `content[0].text`.
- Check the deprecations page when a model is more than a year old: https://platform.claude.com/docs/en/about-claude/model-deprecations

### 10. **Pin Numbering: Physical vs BCM GPIO**
**Problem:** LEDs wired to Pin 11 (GPIO 17), code expects GPIO 12

**Why:** Physical pin numbers ≠ BCM GPIO numbers

**Solution:**
- Pin 11 (physical) = GPIO 17 (LCD touch)
- Pin 32 (physical) = GPIO 12 (LED DIN) ← **Move wire here!**
- Use `pinout` command to verify mapping

### 11. **Tiered Polling Saves 54% of API Calls**
**Problem:** 4,320 Spotify API calls/day per device at 30s polling

**Solution:** Adaptive intervals based on playback state:
- 20s when actively playing
- 60s when recently played (<5min)
- 120s when idle
- Result: ~2,000 calls/day (54% reduction)

### 12. **Screen Tearing Fix: fbcon=map:0**
**Problem:** Horizontal tearing when writing to framebuffer

**Why:** Console output to fb1 conflicts with display writes

**Solution:**
```bash
# /boot/firmware/cmdline.txt
fbcon=map:0  # Keep console on fb0 (HDMI)
```
Then: row-by-row writes to fb1, never memmap

### 13. **LLM Context Freshness**
**Problem:** Intent routing uses stale music data (wrong artist)

**Why:** Music data cached, not refreshed before Haiku call

**Solution:**
```python
# Force refresh BEFORE intent routing
self.last_music_fetch = 0
self._update_music()
intent = await self.intent_router.route(transcript)
```

### 14. **Crew Registration is Two-Phase**
**Problem:** Crew created locally but not on server

**Why:** Portal runs offline, server registration happens later

**Flow:**
1. Portal creates `crew_config.json` with `is_creator: true`
2. Brain connects to relay on first boot
3. If `rejoin_crew()` fails + `is_creator` → `create_crew()` with existing code
4. Server preserves portal-assigned crew code

### 15. **Deferred OAuth Token Delivery**
**Problem:** User completes OAuth while device offline

**Why:** Device may not have WebSocket connection during OAuth callback

**Solution:** Server stores tokens in `pendingSpotifyTokens` Map (1hr TTL)
- Delivered on next `register`, `create_crew`, or `join_crew` event
- Device must use same `device_id` as OAuth `state` parameter

## Display Rendering Patterns

### Row-by-Row Framebuffer Writes (No Tearing)
```python
# ✅ GOOD: Atomic row writes
with open('/dev/fb1', 'wb') as fb:
    for row in range(height):
        row_data = rgb565_array[row, :].tobytes()
        fb.write(row_data)

# ❌ BAD: Full memmap (causes tearing)
fb = np.memmap('/dev/fb1', dtype=np.uint16, shape=(320, 480))
fb[:] = rgb565_array  # Non-atomic!
```

### RGB565 Conversion (Pi Optimized)
```python
def rgb_to_rgb565_fast(img):
    import numpy as np
    arr = np.array(img)
    r = arr[:, :, 0].astype(np.uint16)
    g = arr[:, :, 1].astype(np.uint16)
    b = arr[:, :, 2].astype(np.uint16)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
```

## Spotify Integration Gotchas

### Token Refresh Pattern
```python
response = requests.get(url, headers={"Authorization": f"Bearer {token}"})

if response.status_code == 401:
    # Token expired (1hr lifespan)
    new_token = refresh_access_token()
    # Keep refresh_token even if not in response!
    response = requests.get(url, headers={"Authorization": f"Bearer {new_token}"})
```

### Scancode URL Format
```python
# Free, no auth needed
from urllib.parse import quote
encoded_uri = quote(spotify_uri, safe=':')
url = f"https://scannables.scdn.co/uri/plain/png/{bg_hex}/{fg_hex}/280/{encoded_uri}"

# Example:
# bg_hex="1A1D2E" (no # prefix)
# fg_hex="white"
# size=280
# uri="spotify%3Atrack%3A..."
```

### Music Display Priority
1. **Shared music** (from crew) → Shows for 30min OR until song changes
2. **Currently playing** (from Spotify) → Shows when nothing shared
3. **Last known** (fallback) → When playback stopped

```python
if existing_music['source'] == 'shared' and age < 1800:  # 30min
    if currently_playing['uri'] != existing_music['uri']:
        # Song changed → switch to currently playing
        return currently_playing
    return existing_music  # Keep showing shared
return currently_playing or existing_music  # Fallback chain
```

## Weather API Integration

### Current vs Forecast Data
```python
# Must request current=precipitation,weather_code
current_precip = data['current']['precipitation']  # inches/hr NOW
is_raining = current_precip > 0

# daily=precipitation_sum is 24hr FORECAST only
daily_rain = data['daily']['precipitation_sum'][0]

# Combine for slider
rain_slider = max(daily_rain, current_precip * 4)  # Show active rain prominently
```

### WMO Weather Codes
- 51-67: Rain/drizzle
- 80-82: Showers
- 95-99: Thunderstorms

## Debugging Techniques

```bash
# 1. Check file timestamps (find stale cached files)
stat album_art/filename.jpg

# 2. Verify process age (is old code still running?)
ps aux | grep python
# Check START time column

# 3. Test imports standalone
python3 -c "from leeloo_album_art import download_and_create_album_art; print('OK')"

# 4. Check image dimensions
file album_art/*.jpg | grep -v "244 x 304"

# 5. Trace WebSocket messages
grep "type.*song_push" < logfile

# 6. Find which script created a file
ls -lt album_art/ | head  # Most recent first

# 7. Check actual pin assignment
gpio readall  # Shows physical pin → GPIO mapping
```

## Boot Screen Optimization

**Before:** 800x610 PNG, 481KB, scaled down at runtime
**After:** 480x320 PNG, 5.8KB, native resolution

**Benefit:** 98% smaller, no scaling artifacts, faster boot

```python
# Create native resolution boot screen
img = Image.new('RGB', (480, 320), COLORS['bg'])
logo.thumbnail((420, 240))  # Scale logo to fit
img.paste(logo, center_position)
img.save('LeeLoo_boot.png')  # 5.8KB vs 481KB!
```

---

**Session Count:** Consolidated from 6+ debugging sessions
**Last Updated:** February 15, 2026
**Most Critical:** Items 1, 2, 3, 4 above (will save hours of debugging)

## 2026-10 Full Review: Fixes and Lessons

Tags: `#concurrency` `#security` `#wifi-setup`

### Self-cancelling expansion task
`self._expand_task = create_task(expand_frame(...))` and then, inside `expand_frame`, `self._expand_task.cancel()` cancels **the new task itself**, never the old one. Two messages within 20s drew on top of each other. Fix: `_start_expansion()` cancels all live expansions synchronously and runs the new one under an `asyncio.Lock`.

### `ensure_future` from an executor thread
`asyncio.ensure_future()` in a `run_in_executor` thread raises "There is no current event loop in thread". The display tick runs in a thread, so the ambient LED never changed between idle and playing. Fix: store `self._loop` in `run()` and use `asyncio.run_coroutine_threadsafe()`.

### Tap timing made double tap impossible
After a tap the loop was blind for ~0.74s (settle + drain) but the double-tap window was 0.5s. Always check: blind time + debounce < double-tap window. A fake accelerometer + fake clock test now proves double and triple taps resolve.

### A new tap cancelled the voice session
The tap callback ran inside the tap resolver task, and every new raw tap cancels that task. A tap during recording raised `CancelledError` and left `ui_state` stuck in LISTENING. Fix: run the callback in its own task; reset state in `finally`.

### Incoming events broke recording (DMA, gotcha #11)
Messages, nudges and song pushes started LED animations during `arecord`. Fix: `led.recording_started()` / `recording_finished()` guard.

### Root shell injection in WiFi setup
`subprocess.run(f"nmcli ... '{password}'", shell=True)` as root, with the password from an open WiFi portal. A password like `x'; cmd; '` ran commands as root, and a normal SSID with an apostrophe failed. Fix: argv lists only.

### Wrong WiFi password bricked the device
`setup_complete=True` was written before the connection was checked; after reboot the AP never came back. Fix: write it only after nmcli confirms the connection; recovery portal at boot if WiFi fails.

### Secrets in a public repo
The Spotify client secret was hardcoded and the repo is public. Fix: `.env` only. **Rotating the secret is still required** — removing it from the code does not remove it from git history.

### Verification approach that worked
- Every fix has a test that **fails on the old code** and passes after (check by reverting only the source file).
- Run the suite on Python 3.9, 3.11 and 3.13 (`uv python install 3.9 3.11`), the versions across Pi OS releases.
- Adversarial reviewers after each fix round found 13 more real issues after the first round passed its own tests.
