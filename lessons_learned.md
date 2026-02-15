# Lessons Learned - LEELOO Spotify Integration (Feb 14, 2026)

## Session Summary
Successfully deployed production relay server, implemented Spotify OAuth, and integrated "Currently Playing" feature with proper album art sizing.

---

## Key Lessons

### 1. **DNS Propagation Takes Time**
- DNS changes at Namecheap typically take 5-10 minutes
- Always wait for propagation before testing SSL certificates
- Use `dig` or online tools to verify DNS before proceeding

### 2. **WebSocket Connections Need Keepalive**
- Long-running WebSocket connections drop without periodic pings
- Implement `ping_interval` and `ping_timeout` in websockets library
- OAuth flows that wait for callbacks need robust connection handling

### 3. **Token Refresh is Critical for Spotify API**
- Access tokens expire after 1 hour (3600 seconds)
- Always implement automatic token refresh using refresh_token
- Handle 401 responses by refreshing token and retrying request
- Keep refresh_token even if not returned in refresh response

### 4. **OAuth State Parameter Must Match Device ID**
- The `state` parameter in OAuth URL identifies which device is authorizing
- Device must maintain WebSocket connection with same ID while user authorizes
- Connection drops = missed callback = failed authorization

### 5. **Image Sizing Must Be Centralized**
**Problem:** Multiple scripts created album art at different sizes, causing inconsistent display
**Solution:** 
- Create single source of truth: `leeloo_album_art.py`
- All scripts import from centralized utility
- Standard size: 243×304 (244px art + 60px bar)
- Never create images ad-hoc in individual scripts

### 6. **Cache Invalidation is Hard**
- Old cached files persist even after code changes
- Always clear cache when changing image dimensions
- Main loop must be restarted to load new code
- Check file timestamps to debug which code version created files

### 7. **Process Management Requires Restarts**
- Long-running processes (gadget_main.py) don't auto-reload code
- Check PID and start time to verify which version is running
- Use `sudo pkill -f` to kill by command name, not just PID
- Always verify process restarted before testing changes

### 8. **QR Code UX: Don't Compete with Main UI**
**Original approach:** Full-screen QR code takeover
**Better approach:** Show QR code in album art area only
- Preserves main UI (weather, time, contacts)
- User knows where to scan
- Less jarring experience

### 9. **Spotify API Scopes Are Specific**
- `user-read-currently-playing` - Get what's playing right now
- `user-read-playback-state` - Get full playback state (includes paused)
- Request minimal scopes needed for feature

### 10. **Priority Logic for Music Display**
**Implementation:**
1. Shared music (from crew) - shows for 30 minutes
2. Currently playing (from Spotify) - shows when nothing shared
3. Source flag (`source` field) determines display label:
   - `"currently_playing"` → "pushed by: You"
   - `"shared"` → "pushed by: [Name]"

---

## Technical Patterns

### Centralized Utilities Pattern
```python
# ❌ BAD: Each script creates images differently
def download_album_art_script1():
    img.resize((640, 640))  # Wrong size

def download_album_art_script2():
    img.resize((300, 300))  # Different wrong size

# ✅ GOOD: Single source of truth
from leeloo_album_art import download_and_create_album_art
album_art = download_and_create_album_art(url, uri, dir, source)
```

### Token Refresh Pattern
```python
response = requests.get(api_url, headers={"Authorization": f"Bearer {token}"})

if response.status_code == 401:
    # Token expired, refresh it
    new_token = refresh_access_token()
    # Retry with new token
    response = requests.get(api_url, headers={"Authorization": f"Bearer {new_token}"})
```

### WebSocket Keepalive Pattern
```python
async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
    while True:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=30)
            # Process message
        except asyncio.TimeoutError:
            # Send ping to keep connection alive
            await ws.send(json.dumps({"type": "ping"}))
```

---

## Architecture Decisions

### Why Relay Server?
- Raspberry Pi can't expose ports to internet (behind NAT)
- Spotify OAuth requires publicly accessible callback URL
- Relay server acts as middleman:
  1. Device connects via WebSocket
  2. OAuth callback received at public URL
  3. Tokens sent to device via existing WebSocket

### Why 243×304 for Album Art?
- Display layout reserves 243px width for album art column
- 304px height = 244px art + 60px bar (scancode or "Now Playing")
- Matches existing placeholder dimensions exactly
- Consistent with shared music scancode format

### Why 30-Minute Priority for Shared Music?
- Shared music is intentional social action (more important)
- Currently playing is passive background info
- 30 minutes gives time to listen and engage
- After expiry, reverts to showing currently playing

---

## Debugging Techniques Used

1. **Check file timestamps** - `stat filename` to see when created
2. **Verify process age** - `ps aux` shows start time, identify old code
3. **Check image dimensions** - `file image.jpg` shows actual size
4. **Test imports** - Run utility standalone to verify it works
5. **Check API responses** - Print status codes and raw JSON
6. **Trace execution** - `grep -n` to find which functions are called
7. **Verify cache state** - List cached files to see what exists

---

## Production Deployment Checklist

- [ ] DNS configured and propagated
- [ ] SSL certificate obtained and auto-renewing
- [ ] PM2 configured for auto-restart on crash/reboot
- [ ] Firewall configured (only 22, 80, 443 open)
- [ ] Environment variables secured (not in Git)
- [ ] WebSocket keepalive implemented
- [ ] Token refresh implemented
- [ ] Cache clearing mechanism in place
- [ ] Process restart procedure documented
- [ ] Health check endpoint working

---

## Files Created This Session

### Production Server
- `/root/leeloo-relay/server.js` - WebSocket relay server
- `/root/leeloo-relay/.env` - Spotify credentials (secured)
- `/etc/nginx/sites-available/leeloobot.xyz` - Nginx config

### Pi Scripts
- `leeloo_album_art.py` - Centralized album art utility ⭐
- `leeloo_music_manager.py` - Currently playing integration
- `spotify_auth_qr.py` - QR code OAuth flow
- `spotify_tokens.json` - Cached Spotify tokens

### Documentation
- `RELAY_DEPLOYMENT_SUCCESS.md` - Complete deployment log
- `lessons_learned.md` - This file

---

## Key Metrics

- **Deployment time**: ~25 minutes (DNS to working relay)
- **OAuth flow time**: ~10 seconds (scan to tokens saved)
- **Music update interval**: 30 seconds
- **Shared music priority**: 30 minutes
- **Token lifespan**: 1 hour (auto-refresh)

---

## What Worked Well

1. ✅ Centralized album art utility prevents size inconsistencies
2. ✅ QR code in album art area preserves main UI
3. ✅ Automatic token refresh prevents auth failures
4. ✅ Priority system gives shared music preference
5. ✅ WebSocket keepalive prevents connection drops
6. ✅ PM2 process management ensures uptime

## What to Improve

1. 🔄 Add visual indicator when token is expired
2. 🔄 Implement token expiry tracking (don't wait for 401)
3. 🔄 Add retry logic for network failures
4. 🔄 Cache album art for offline display
5. ✅ ~~Add album art for shared music with scancodes~~

---

# Session 2 - Album Info & Monthly Listeners (Feb 14, 2026 evening)

## Session Summary
Added real monthly listeners to the album info display by web scraping Spotify artist pages. Replaced the plain-text "Now Playing" bar with a custom pixel-art `nowplaying.png`. Refined album info layout with color-coded, left-justified text.

---

## Key Lessons

### 11. **Spotify API Does NOT Return Followers/Popularity**
**Problem:** The `/v1/artists/{id}` endpoint returns an empty `followers: {}` object — no `total`, no `popularity`, no `genres`. This affects both user tokens AND client credentials tokens, even for major artists (Drake, Taylor Swift).
**Solution:** Web scrape the public Spotify artist page (`open.spotify.com/artist/{id}`). The `og:description` meta tag reliably contains `"Artist · X.XM monthly listeners."` — parse with regex.

### 12. **Web Scraping Spotify for Monthly Listeners Works Reliably**
- The `og:description` meta tag format: `"Artist · 3.6M monthly listeners."`
- Regex: `r'<meta\s+property="og:description"\s+content="[^"]*?(\d[\d,.]*[MKB]?)\s*monthly\s*listener'`
- Exact count also in page body: `3,625,445 monthly listeners`
- Simple `requests.get()` with User-Agent header — no JS rendering needed
- Works on Pi with no extra dependencies

### 13. **Cache Scraped Data to Avoid Rate Limiting**
- Monthly listeners don't change frequently — cache per artist for 1 hour
- In-memory dict: `_listeners_cache = {artist_id: (listeners_str, timestamp)}`
- Prevents hitting Spotify's web page every 30-second music update cycle
- Cache survives across update cycles within same process

### 14. **Running Process Uses Old Code Until Restarted**
- Editing files on Pi does NOT affect the running `gadget_main.py` process
- The process loaded code at startup and keeps using it
- Must `sudo pkill -f gadget_main.py` — auto-restarts with new code within seconds
- Always verify new PID and start time with `ps aux`

### 15. **Album Art Cache Must Be Cleared When Changing Image Composition**
- Cached `.jpg` files in `album_art/` are the final composited images (art + bar)
- Changing `nowplaying.png` or the composition code requires clearing the cache
- `rm -f /home/pi/leeloo-ui/album_art/*.jpg` forces regeneration on next cycle
- The `download_and_create_album_art()` function skips download if cache exists

### 16. **Custom Image Assets Need Aspect-Aware Scaling**
- `nowplaying.png` is 800x201, target bar is 243x60
- Scale to fit width first, then check if height exceeds bar — if so, fit to height instead
- Center the scaled image in the bar area for clean positioning
- Handle RGBA mode for transparency support in PNG overlays

### 17. **Shared Music Also Needs Listeners Data**
- When crew members push songs, the payload doesn't include listeners
- Added `get_listeners_for_artist_name()` — searches Spotify for artist ID, then scrapes
- `update_music_display()` backfills listeners on shared music that arrives without them
- Uses client credentials token for search (no user scope needed)

---

## Technical Patterns

### Web Scraping Pattern for Spotify Data
```python
def scrape_monthly_listeners(artist_id):
    url = f"https://open.spotify.com/artist/{artist_id}"
    headers = {"User-Agent": "Mozilla/5.0 ..."}
    resp = requests.get(url, headers=headers, timeout=10)

    # Parse og:description meta tag
    match = re.search(
        r'<meta\s+property="og:description"\s+content="[^"]*?'
        r'(\d[\d,.]*[MKB]?)\s*monthly\s*listener',
        resp.text, re.IGNORECASE
    )
    if match:
        return match.group(1)  # e.g., "3.6M"
```

### Deploy-to-Pi Pattern
```bash
# 1. Copy files
sshpass -p 'gadget' scp file.py pi@leeloo.local:/home/pi/leeloo-ui/

# 2. Clear cache if image composition changed
sshpass -p 'gadget' ssh pi@leeloo.local "rm -f /home/pi/leeloo-ui/album_art/*.jpg"

# 3. Restart process (auto-restarts within seconds)
sshpass -p 'gadget' ssh pi@leeloo.local "sudo pkill -f gadget_main.py"

# 4. Verify
sshpass -p 'gadget' ssh pi@leeloo.local "ps aux | grep gadget_main"
```

---

## Files Modified This Session

### Pi Scripts (deployed to `/home/pi/leeloo-ui/`)
- `leeloo_music_manager.py` - Replaced broken API call with web scraping for real monthly listeners
- `leeloo_album_art.py` - Now uses `nowplaying.png` instead of plain text for "Now Playing" bar
- `gadget_display.py` - Monthly listeners in yellow, "pushed by" + "ask for more info" left-aligned

### New Assets
- `nowplaying.png` - Custom pixel-art "NOW PLAYING" bar (800x201, scaled to 243x60)

### React UI
- `Retro-Music-Panel/client/src/index.css` - Added `--color-gadget-yellow: #D4A84B`
- `Retro-Music-Panel/client/src/components/Gadget.tsx` - Listeners in yellow, pushed by left-aligned

---

## Album Info Layout (in album frame)
```
  Artist Name          (centered, green, bold)
  Album Name           (centered, green)
  "Track Name"         (centered, green, bold, in quotes)
  3.6M monthly listeners  (left, yellow, 4px from frame)
  pushed by Amy           (left, rose, 4px from frame)
  ask for more info       (left, lavender/purple, 4px from frame)
```

---

## Color Reference
| Element | Color Name | Hex | Usage |
|---------|-----------|-----|-------|
| Monthly listeners | yellow | `#D4A84B` | Left-aligned in album box |
| Pushed by | rose | `#D6697F` | Left-aligned in album box |
| Ask for more info | lavender | `#d978f9` | Left-aligned in album box |
| Artist/Album/Track | green | `#7beec0` | Centered in album box |
| Weather | tan | `#C2995E` | Weather box data |
| Time | purple | `#9C93DD` | Time box data |
| Messages | lavender | `#d978f9` | Messages box frame |

---

---

# Session 3 — Voice Interaction System Build + Tuning (Feb 14, 2026 night)

## Session Summary
Built and deployed the complete voice interaction system (Phases 1-6 of the LEELOO plan): tap detection, Deepgram STT, Claude Haiku intent routing, frame expand/typewriter display, Spotify song search+push, and WebSocket integration. Then spent the latter half tuning tap sensitivity, fixing text overflow, and debugging display refresh issues.

---

## Key Lessons

### 18. **Asyncio Event Loop Blocking Kills Tap Detection**
**Problem:** Tap detection (polling at 40ms) intermittently stopped working. Accelerometer was fine but taps weren't registering.
**Root Cause:** `_render_normal()` (PIL + numpy + framebuffer write) runs synchronously — takes 200-500ms on Pi Zero, starving the tap polling coroutine.
**Solution:** Run blocking display work in a thread executor: `await loop.run_in_executor(None, self._display_tick)`
**Lesson:** On Pi Zero, ANY synchronous work >50ms on the asyncio event loop will starve other coroutines. Always offload blocking I/O and CPU-heavy work to executors.

### 19. **Accelerometer Tap Detection Needs Rebound Absorption**
**Problem:** Single physical taps registered as double taps from mechanical rebound.
**Solution:** Three-layer approach: (1) SETTLE_TIME 0.50s sleep after spike, (2) DEBOUNCE_TIME 0.50s between raw events, (3) SETTLE_DRAIN_READS=5 to reset prev_magnitude baseline.
**Lesson:** The drain reads were the key insight — without them, first read after settle sees a delta from stale prev_magnitude.

### 20. **TAP_THRESHOLD Must Be Tuned Empirically**
Went from 2.5 → 1.8 → 1.2. Added near-miss logging (delta > 0.6) to make tuning data-driven. Always log near-misses when tuning thresholds.

### 21. **Force-Refresh Context Before LLM Calls**
**Problem:** "Tell me about this band" returned wrong artist — Claude had stale music context (30s cache).
**Solution:** Force `self.last_music_fetch = 0; self._update_music()` right before calling the intent router.
**Lesson:** When an LLM needs current state, always refresh immediately before the call, not on a timer.

### 22. **Scancode URL Must Be Passed Explicitly for Shared Music**
**Problem:** Voice-pushed songs showed album art but no Spotify scancode.
**Solution:** Generate URL: `https://scannables.scdn.co/uri/plain/png/{bg}/{fg}/{size}/{uri}`, pass as `scancode_url` param. Clear stale cache first.
**Lesson:** The album art cache serves stale versions without scancode unless explicitly cleared.

### 23. **Text Overflow Should Scroll, Not Truncate**
Typewriter writes visible portion, then if overflow, pre-renders ALL lines to off-screen PIL image and scrolls at 1px/frame (~14fps). Better UX than "..." truncation for variable-length content.

### 24. **Spotify Client Credentials Work for Search**
`grant_type=client_credentials` is sufficient for `/v1/search`. No user token or OAuth needed for song lookup.

---

## Files Created This Session

| File | Purpose |
|------|---------|
| `leeloo_brain.py` | Asyncio orchestrator — main entry point |
| `leeloo_tap.py` | ADXL345 tap detection (single/double/triple) |
| `leeloo_voice.py` | INMP441 mic → Deepgram Nova-2 streaming STT |
| `leeloo_intent.py` | Claude 3.5 Haiku intent classification |
| `leeloo_led.py` | WS2812B LED animations (3 LEDs) |
| `leeloo_messages.py` | Message storage + unread counts |

## Tuning Values (Final)

| Parameter | Value | Notes |
|-----------|-------|-------|
| TAP_THRESHOLD | 1.2 m/s² | Delta magnitude to count as tap |
| SETTLE_TIME | 0.50s | Rebound dampening wait |
| DEBOUNCE_TIME | 0.50s | Min time between raw events |
| SETTLE_DRAIN_READS | 5 | Baseline reset reads after settle |
| SILENCE_THRESHOLD | 30 RMS | Speech ~40-70, ambient ~3-5 |
| SILENCE_DURATION | 1.5s | Silence to stop recording |
| EXPANDED_HOLD_DURATION | 30.0s | Expanded frame display time |

## Known Issues (Remaining)
1. **LEDs not working** — GPIO 12, WS2812B, needs root for PWM. Not debugged yet.
2. **Tap occasionally misses** — Very light taps still below 1.2 threshold.

---

---

# Session 4 — Captive Portal, First Boot Welcome, Crew Registration & Message-to-Music (Feb 15, 2026)

## Session Summary
Recovered a bricked Pi (portal left NetworkManager stopped), deployed full captive portal setup flow, implemented first-boot welcome screen with QR code + crew code, fixed crew registration on relay server, added Telegram messaging end-to-end, and built music detection in incoming crew messages (natural language → Spotify album art + scancode).

---

## Key Lessons

### 25. **NetworkManager Must Be Restarted After AP Mode**
**Problem:** Pi bricked after portal — `connect_saved_wifi.py` stopped hostapd/dnsmasq but never restarted NetworkManager, leaving WiFi permanently dead.
**Solution:** `sudo systemctl start NetworkManager` in `stop_ap_mode()` before calling `nmcli device wifi connect`.
**Lesson:** Any script that stops NetworkManager for AP mode MUST restart it. This is the single most critical line in the portal flow.

### 26. **systemd.mask in cmdline.txt Persists Until Reboot**
**Problem:** Added `systemd.mask=leeloo.service` to cmdline.txt for safe recovery, then removed it — but service stayed masked at runtime.
**Root Cause:** Kernel cmdline parameters create generator-based masks processed at boot. Removing from cmdline.txt doesn't unmask until next reboot.
**Lesson:** `systemd.mask=` in cmdline.txt requires a reboot to clear. Can't unmask at runtime with `systemctl unmask`.

### 27. **QR Code Belongs in the Album Art Box, Not the Messages Frame**
**Problem:** First attempt put QR code inside the messages expand frame — cluttered and wrong visual area.
**User feedback:** "nope. the qr code goes in where the Album art / scan code is located. message box is for text and reaction animation"
**Solution:** Generate 243×304 QR image, swap into `self.album_art_path`, re-render normal UI, then expand messages with text only. Restore original album art after collapse.
**Lesson:** Album art box = visual media (art, QR, scancode). Messages frame = text only.

### 28. **Portal Creates Crew Locally But Must Register on Server**
**Problem:** Portal saved crew config (`crew_config.json` with `is_creator: true, crew_code: LEELOO-WFJS`) but the relay server had no record of it. `rejoin_crew()` always failed with "crew not found."
**Solution:** Modified `_ws_connection_loop` to call `create_crew()` if `rejoin_crew()` fails and `is_creator=True`. Modified server.js to accept optional `crew_code` parameter so portal-assigned codes persist.
**Lesson:** Two-phase crew setup: (1) portal creates crew config locally, (2) brain registers crew on relay server at first WebSocket connection.

### 29. **Server Response Fields Vary — Use .get() Defensively**
**Problem:** `data['crew_id']` KeyError crashed WebSocket loop. Server `crew_joined` response sends `crew_code` but NOT `crew_id`.
**Solution:** `data.get('crew_id', data.get('crew_code', self.config.crew_id))`
**Lesson:** Never use dict bracket access on WebSocket/API responses. Always `.get()` with fallbacks.

### 30. **Anthropic Model Names Differ Between SDK Versions**
**Problem:** Music detection failed with `404 - model: claude-3-5-haiku-latest`. The Pi's older Anthropic SDK doesn't support the `latest` alias.
**Solution:** Use the explicit version `claude-3-haiku-20240307` matching what intent router already uses.
**Lesson:** Always match model names to what's already working in the codebase. Check existing code before using new aliases.

### 31. **Claude Haiku Can Classify Music Mentions in Casual Messages**
Simple system prompt extracts Spotify search queries from natural language:
- "Yall have to listen to that new Fred again song Feisty" → `{"music": true, "query": "Fred again Feisty"}`
- "hey whats up tonight" → `{"music": false}`
Cost: ~0.001¢ per classification. Latency: <2s on Pi Zero (via thread executor).

### 32. **Three-Path Music Detection Covers All Cases**
1. **Spotify links** (`open.spotify.com/track/...`) → regex extract track ID → direct API lookup
2. **Spotify URIs** (`spotify:track:...`) → use directly with tracks endpoint
3. **Natural language** → Claude Haiku classification → Spotify text search
Path 1 & 2 are instant (no LLM call). Path 3 adds ~2s but runs in background while message displays.

### 33. **Telegram-Only Crew Members Work Without a Device**
Friends can join a crew via Telegram bot (`/start {crew_code}`) without owning a LEELOO device. Messages flow Telegram → relay server → device. This enables testing crew messaging with a single Pi.

---

## Technical Patterns

### Defensive WebSocket Response Parsing
```python
# ❌ BAD: Crashes on missing fields
self.config.crew_id = data['crew_id']

# ✅ GOOD: Fallback chain
self.config.crew_id = data.get('crew_id', data.get('crew_code', self.config.crew_id))
```

### Two-Phase Crew Registration
```python
# Phase 1: Portal saves locally
crew_config.json = {"crew_code": "LEELOO-WFJS", "is_creator": true}

# Phase 2: Brain registers on server at first connect
joined = await ws_client.rejoin_crew()
if not joined and crew_config.get('is_creator'):
    await ws_client.create_crew(display_name)  # passes existing crew_code
```

### Background Music Detection in Messages
```python
def _on_ws_message(self, sender, text):
    # Show message immediately (don't block on music detection)
    asyncio.ensure_future(self._handle_message_with_music(sender, text))

async def _handle_message_with_music(self, sender, text):
    # 1. Expand message frame with text (instant)
    # 2. Detect music in background (Claude Haiku, ~2s)
    # 3. If found, search Spotify + update album art (no crew push)
    # 4. Force display refresh to show new album art
```

---

## Files Modified This Session

| File | Changes |
|------|---------|
| `leeloo_brain.py` | Welcome screen with QR, crew creation fallback, message-to-music detection |
| `leeloo_client.py` | Defensive `.get()` for crew_id, pass existing crew_code in create_crew |
| `leeloo_server/server.js` | Accept optional `crew_code` in create_crew |
| `connect_saved_wifi.py` | Critical fix: restart NetworkManager after AP mode |
| `leeloo_boot.py` | Full portal boot flow (first run detection, WiFi disconnect for AP) |
| `captive_portal.py` | Pre-scan WiFi before AP mode, crew creation UI |

---

Made with ♪ by squid-baby & Claude Opus 4.6
