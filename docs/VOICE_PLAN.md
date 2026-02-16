# Voice Interaction Implementation Plan for TipTop UI

## Overview
Add voice interaction to TipTop UI retro gadget: user taps button → speaks question → receives text response on e-paper display. Uses local Vosk STT on Pi Zero W 2 + cloud Claude API with web search for band/artist information.

## Architecture Summary

```
[Button Tap] → LED ON → [Record Audio] → [Vosk STT (local)]
    → [Send to Backend /api/ask] → [Claude API + Wikipedia Search]
    → [Format Response] → [Return to Pi] → [Update Display] → LED OFF
```

**Key Decisions:**
- **Trigger:** Physical button (GPIO 17)
- **Input:** Voice → Text via Vosk (offline STT, <1s latency)
- **Output:** Text only (no voice response)
- **Content:** Real-time web search + Wikipedia via Claude API
- **Feedback:** LED indicator (GPIO 27) - solid when listening, blink when processing
- **Display:** Waveshare 3.5" color LCD (480x320px, 60Hz refresh) - can show full React UI or custom Python rendering
- **UI:** New 5th "conversation" box in Gadget UI

---

## Critical Files to Modify/Create

### New Files (Python Services on Pi)
1. `pi_services/voice_service.py` - Main orchestrator (button → STT → API → display)
2. `pi_services/audio_recorder.py` - Audio capture with silence detection
3. `pi_services/vosk_stt.py` - Vosk speech-to-text wrapper
4. `pi_services/led_controller.py` - GPIO LED control
5. `pi_services/button_handler.py` - GPIO button detection
6. `pi_services/api_client.py` - HTTP client for backend API
7. `pi_services/display_updater.py` - Interface with gadget_display.py
8. `pi_services/config.py` - Configuration (API URLs, GPIO pins)

### New Files (Backend Services)
9. `Retro-Music-Panel/server/services/claude_service.ts` - Claude API client with web search tool use
10. `Retro-Music-Panel/server/services/wikipedia_service.ts` - Wikipedia API integration
11. `Retro-Music-Panel/server/services/fallback_service.ts` - Offline error handling
12. `Retro-Music-Panel/server/utils/display_formatter.ts` - Format text for e-paper (300-400 char limit)

### New Files (Database Schema)
13. `Retro-Music-Panel/shared/conversation_schema.ts` - Add `conversations`, `voiceQueryLogs`, `searchCache` tables

### Files to Modify
14. `gadget_display.py` - Add `conversation_data` parameter, render conversation overlay
15. `Retro-Music-Panel/server/routes.ts` - Add `/api/ask`, `/api/conversation/:id`, `/api/health` endpoints
16. `Retro-Music-Panel/client/src/components/Gadget.tsx` - Add 5th conversation box, polling logic
17. `Retro-Music-Panel/server/storage.ts` - Add conversation CRUD methods to IStorage
18. `Retro-Music-Panel/shared/schema.ts` - Add conversation tables
19. `requirements.txt` - Add vosk, sounddevice, RPi.GPIO, requests
20. `Retro-Music-Panel/package.json` - Add @anthropic-ai/sdk, axios

---

## Implementation Details

### 1. Python Voice Service Architecture

**Main orchestrator** (`voice_service.py`):
```python
class VoiceService:
    def __init__(self):
        self.recorder = AudioRecorder()
        self.stt = VoskSTT(model_path="./models/vosk-small-en-us")
        self.led = LEDController(gpio_pin=27)
        self.button = ButtonHandler(gpio_pin=17, callback=self.on_button_press)
        self.api = APIClient(base_url="http://localhost:3000")
        self.display = DisplayUpdater()

    def on_button_press(self):
        self.led.on()  # Solid LED = listening
        audio_data = self.recorder.record_until_silence(max_duration=10)

        self.led.blink(frequency=3)  # Fast blink = processing
        text = self.stt.transcribe(audio_data)

        response = self.api.ask_question(text)
        self.display.update_conversation(question=text, response=response)

        self.led.off()
```

**Audio recording** with VAD (Voice Activity Detection):
- Record at 16kHz mono (optimal for Vosk)
- Stop after 1.5s of silence OR 10s max duration
- Use RMS energy detection for silence threshold

**Vosk model:** `vosk-model-small-en-us-0.15` (40MB, ~300MB runtime memory, <1s latency)

**GPIO pins:**
- Button: GPIO 17 (INPUT with pull-up resistor)
- LED: GPIO 27 (OUTPUT with PWM for blinking)

### 2. Backend API Design

**New route** (`server/routes.ts`):
```typescript
app.post('/api/ask', async (req, res) => {
  const { question } = req.body;

  // Call Claude API with web search tool
  const response = await claudeService.answerQuestion(question);

  // Format for e-paper display (300-400 char limit)
  const formatted = DisplayFormatter.formatResponse(response, 400);

  // Store conversation history
  await storage.saveConversation({ question, response: formatted });

  res.json({ response: formatted });
});
```

**Claude API integration** (`services/claude_service.ts`):
- Use `claude-3-5-sonnet-20241022` model
- Implement tool calling for web search:
  - Tool: `web_search` with parameters `query` and `source` (wikipedia/web)
  - Execute Wikipedia API lookups when Claude requests searches
  - Format response to fit e-paper: remove URLs, markdown, truncate to 400 chars
- System prompt: "You are a music encyclopedia for a retro gadget display. Answer concisely in ~300 characters."

**Wikipedia service** (`services/wikipedia_service.ts`):
```typescript
async searchArtist(query: string): Promise<string> {
  const response = await axios.get(
    `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(query)}`
  );
  return response.data.extract;  // First paragraph summary
}
```

**Response formatting** (`utils/display_formatter.ts`):
- Remove URLs, markdown, special characters
- Break lines at ~50-60 chars (Waveshare 3.5" LCD width allows more text)
- Limit to 800-1000 chars (LCD can display more than e-paper, can scroll if needed)
- Smart word boundary breaking
- Support for color formatting hints (Claude can return formatted text)

### 3. Database Schema Updates

Add to `shared/schema.ts`:
```typescript
export const conversations = pgTable("conversations", {
  id: varchar("id").primaryKey().default(sql`gen_random_uuid()`),
  deviceId: text("device_id").notNull(),
  question: text("question").notNull(),
  response: text("response").notNull(),
  createdAt: timestamp("created_at").defaultNow(),
});

export const searchCache = pgTable("search_cache", {
  id: varchar("id").primaryKey(),
  query: text("query").notNull().unique(),
  results: text("results").notNull(),  // JSON
  expiresAt: timestamp("expires_at").notNull(),
});
```

Create migration: `npx drizzle-kit generate`

### 4. Frontend Display Updates

**Add 5th box to Gadget.tsx:**
```typescript
type ExpandedBox = 'weather' | 'time' | 'messages' | 'album' | 'conversation' | null;

const [conversationState, setConversationState] = useState({
  question: '',
  response: '',
  status: 'listening' | 'processing' | 'complete'
});

// Poll for updates every 1 second
useEffect(() => {
  const interval = setInterval(async () => {
    const data = await fetch('/api/conversation/latest').then(r => r.json());
    if (data.question) {
      setConversationState(data);
      setExpandedBox('conversation');
    }
  }, 1000);
  return () => clearInterval(interval);
}, []);
```

**Conversation box UI:**
- Collapsed: Show "● listening..." / "⟳ thinking..." / truncated question
- Expanded: Full question + response with typewriter effect
- Auto-expand when new conversation detected
- Auto-collapse after 30s of inactivity
- Color: `text-gadget-orange` (border), `text-gadget-lavender` (response text)

### 5. Display Integration

**Modify `gadget_display.py`:**
```python
def render(self, weather_data, time_data, messages, album_data, conversation_data=None):
    # Existing rendering...

    if conversation_data and conversation_data['status'] == 'complete':
        self.draw_conversation_overlay(conversation_data)

def draw_conversation_overlay(self, conv_data):
    # Waveshare 3.5" LCD: 480x320px color display
    # Can render full color UI with larger fonts
    # Title: "You asked:" (14px font, orange)
    # Question (12px font, lavender)
    # Response (11px font, white, word-wrapped)
    # Status indicator with color: ● green (listening) / ⟳ orange (processing) / ✓ blue (complete)
    # Optional: Add scrolling for long responses (LCD supports smooth refresh)
```

---

## Dependencies to Add

### Python (`requirements.txt`)
```
vosk==0.3.45              # Offline speech-to-text
sounddevice==0.4.6        # Audio recording
numpy>=1.24.0             # Required by sounddevice
requests==2.31.0          # API client
RPi.GPIO>=0.7.1           # GPIO control (Pi-specific)
python-dotenv==1.0.0      # Config management
```

### Node.js (`package.json`)
```json
{
  "dependencies": {
    "@anthropic-ai/sdk": "^0.30.0",
    "axios": "^1.6.0"
  }
}
```

---

## Edge Cases & Error Handling

### 1. Offline Fallback (WiFi Down)
- **Detection:** Try `GET /api/health` with 2s timeout
- **Behavior:** LED blinks red (3 quick blinks), display shows "Offline - Check WiFi"
- **Recovery:** Queue questions, retry when connection restored

### 2. STT Errors (Unintelligible Speech)
- **Detection:** Empty transcription or <3 chars
- **Behavior:** Display "Didn't catch that. Try again?", LED off
- **Recovery:** User taps button again

### 3. API Timeout
- **Backend timeout:** 10s for Claude API (web search can be slow)
- **Retry:** 2 attempts with exponential backoff (1s, 2s)
- **Fallback:** Return cached response if query similar to previous, or "Request timed out. Try again."

### 4. Display Overflow (Response Too Long)
- **Strategy:** Scrollable text area (Waveshare 3.5" LCD supports smooth scrolling)
- **Primary:** Show first ~800 chars, auto-scroll or manual scroll with button
- **Fallback:** Truncate at 1000 chars with "..."

### 5. Multi-User Scenarios (Multiple Button Taps)
- **Behavior:** Ignore new taps while `is_listening = True`
- **Reason:** Prevents race conditions, confusion

### 6. Memory Constraints (Pi Zero W 2 - 512MB RAM)
- **Budget:** Linux (150MB) + Vosk model (300MB) + Python (50MB) = ~500MB used, 12MB headroom
- **Mitigation:** Use smallest Vosk model, lazy-load model on first tap, monitor with `psutil`

---

## Testing Strategy

### Without Pi Hardware
**Mock audio input:**
```python
# tests/fixtures/who_is_cinnamon_chasers.wav
def test_transcription():
    with open('tests/fixtures/sample.wav', 'rb') as f:
        audio_data = f.read()
    text = vosk_stt.transcribe(audio_data)
    assert "cinnamon chasers" in text.lower()
```

**Mock GPIO:**
```python
from unittest.mock import patch

@patch('RPi.GPIO.output')
def test_led_control(mock_output):
    led = LEDController()
    led.on()
    mock_output.assert_called_with(27, GPIO.HIGH)
```

### Backend Tests
```typescript
// Mock Claude API responses
jest.spyOn(claudeClient.messages, 'create').mockResolvedValue({
  content: [{ text: 'Cinnamon Chasers is an electronica act...' }]
});

const result = await claudeService.answerQuestion('Who is Cinnamon Chasers?');
expect(result.length).toBeLessThanOrEqual(400);
```

### End-to-End Test Scenario
1. Button press detected → LED on
2. Audio recorded → STT transcribes
3. API called with question text
4. Response returned (mocked Claude API)
5. Display updated with conversation overlay
6. LED off

---

## Implementation Sequence

### Phase 1: Backend Foundation
1. Add conversation schema, run migration
2. Implement `ClaudeService` with web search tool
3. Create `WikipediaService` for artist lookups
4. Add `/api/ask` endpoint in `routes.ts`
5. Test with curl/Postman: `POST /api/ask {"question": "Who is Radiohead?"}`

### Phase 2: Python Services
6. Download Vosk model: `wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip`
7. Implement `AudioRecorder` with silence detection
8. Create `VoskSTT` wrapper
9. Build `APIClient` for backend communication
10. Test STT → API flow on dev machine (no GPIO yet)

### Phase 3: GPIO Integration
11. Wire button to GPIO 17, LED to GPIO 27
12. Implement `ButtonHandler` and `LEDController`
13. Create main `VoiceService` orchestrator
14. Test complete flow on Pi hardware

### Phase 4: Display Integration
15. Choose rendering path (browser kiosk vs Python)
16. If browser: Set up Chromium kiosk mode on Pi
17. Modify `gadget_display.py` for Waveshare 3.5" LCD (if using Python rendering)
18. Create `DisplayUpdater` service
19. Test rendering on LCD display
20. Optimize text formatting and scrolling

### Phase 5: Frontend
21. Add conversation box to `Gadget.tsx`
22. Implement polling for `/api/conversation/latest`
23. Add expanded conversation view with typewriter effect
24. Add scrolling support for long responses
25. Test UI transitions on Waveshare 3.5" LCD

### Phase 6: Polish
26. Handle all edge cases (offline, timeouts, memory limits)
27. Write comprehensive tests
28. Performance tuning (cache common queries, optimize Vosk loading)
29. Set up systemd service for auto-start on Pi boot
30. Optimize display refresh rate and backlight settings

---

## Verification & Testing

### End-to-End Test
1. **Setup:** Pi Zero W 2 with mic, button, LED, e-paper display connected
2. **Test scenario:**
   - Tap button → LED turns on solid
   - Speak: "Who is Cinnamon Chasers?"
   - LED blinks (processing)
   - Display shows:
     ```
     You asked: "Who is Cinnamon Chasers?"

     Cinnamon Chasers is an electronica
     act from London, UK, produced by
     Russ Davies. Known for tracks like
     "Luv Deluxe" featured in media...
     ```
   - LED turns off
3. **Validation:**
   - Response appears on e-paper within 6-12 seconds
   - Text is readable (proper line breaks, no overflow)
   - Conversation stored in database
   - API logs show Claude web search tool use

### Performance Benchmarks
- **STT latency:** <1s (Vosk local)
- **API response:** 3-6s (Claude + Wikipedia)
- **Display update:** <2s (e-paper refresh)
- **Total time:** 6-12s (button press to display update)
- **Memory usage:** <450MB (safe margin on 512MB Pi)

---

## Configuration

### Environment Variables
Create `.env` in `Retro-Music-Panel/`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
BACKEND_URL=http://localhost:3000
DATABASE_URL=postgresql://...
```

Create `pi_services/config.py`:
```python
BACKEND_URL = "http://localhost:3000"
VOSK_MODEL_PATH = "./models/vosk-small-en-us"
BUTTON_GPIO = 17
LED_GPIO = 27
MAX_RECORDING_DURATION = 10  # seconds
SILENCE_THRESHOLD = 500       # RMS amplitude
SILENCE_DURATION = 1.5        # seconds
```

## Display Rendering Options

With Waveshare 3.5" color LCD (480x320px, SPI interface), you have two implementation paths:

### Option A: Python Rendering with Pillow (Lightweight)
**Approach:** Direct rendering to LCD via Python + Pillow
```python
# Use existing gadget_display.py pattern
from PIL import Image, ImageDraw, ImageFont
import spidev  # For Waveshare LCD

class GadgetDisplay:
    def __init__(self):
        self.width = 480
        self.height = 320
        self.display = Waveshare35LCD()  # Initialize SPI LCD

    def render(self, weather, time, messages, album, conversation=None):
        image = Image.new('RGB', (self.width, self.height), color='#1A1D2E')
        draw = ImageDraw.Draw(image)

        # Draw 5 boxes with more space (480x320 vs 264x176)
        # Can use larger fonts (12-16px instead of 7-9px)

        if conversation:
            self.draw_conversation_box(draw, conversation)

        self.display.show(image)
```

**Pros:**
- Lightweight (~30MB memory overhead)
- Fast boot time (~20 seconds)
- No browser dependencies
- Direct hardware control
- Lower power consumption

**Cons:**
- More work to recreate React UI in Python
- No access to existing Gadget.tsx component
- Manual text wrapping, scrolling implementation
- Harder to debug/preview (no browser DevTools)

---

### Option B: Browser Kiosk Mode (Full Web UI)
**Approach:** Run Chromium in fullscreen kiosk, display React app
```bash
# Install Chromium on Pi
sudo apt-get install chromium-browser unclutter xdotool

# Create autostart script (/home/pi/.config/lxsession/LXDE-pi/autostart)
@xset s off
@xset -dpms
@xset s noblank
@chromium-browser --kiosk --app=http://localhost:3000 \
  --start-fullscreen --start-maximized \
  --window-size=480,320 \
  --disable-infobars --noerrdialogs \
  --disable-session-crashed-bubble
@unclutter -idle 0

# Start Express server on boot (systemd)
# Voice service still runs as separate Python process
```

**Pros:**
- Reuse existing Gadget.tsx React component (zero UI work!)
- Radix UI components available
- Existing typewriter effect, animations work
- Easy to debug (browser DevTools)
- Responsive updates via polling or WebSocket
- Can test on desktop before deploying to Pi

**Cons:**
- Chromium uses ~120-150MB RAM
- Slower boot time (~45-60 seconds to X server + Chromium)
- Higher power consumption (rendering engine)
- Need X server running

---

### Recommendation: Start with Option B (Browser)

**Reasoning:**
1. **Faster development:** Gadget.tsx already exists, just add 5th box
2. **Better UX:** Existing animations, typewriter effect work out-of-box
3. **Easier testing:** Test on desktop, deploy to Pi
4. **Memory headroom:** Pi Zero W 2 has 512MB, Chromium (150MB) + Vosk (300MB) + OS (150MB) = ~600MB, but Chromium can be configured to use less with flags like `--disable-gpu --single-process`

**Migration path:** If memory becomes an issue later, recreate UI in Python (Option A) once design is finalized.

---

### Browser Optimization Flags

To reduce Chromium memory on Pi Zero W 2:
```bash
chromium-browser --kiosk --app=http://localhost:3000 \
  --disable-gpu \                    # Save ~30MB (no GPU on Pi Zero)
  --disable-software-rasterizer \
  --disable-dev-shm-usage \          # Use /tmp instead of /dev/shm
  --disable-background-networking \
  --disable-sync \
  --disable-translate \
  --disable-extensions \
  --no-sandbox \                     # Save ~20MB (security tradeoff)
  --single-process \                 # Run as single process (saves memory)
  --window-size=480,320
```

Expected memory: ~80-100MB (vs 150MB default)

---

## Estimated Costs

### API Usage (Monthly)
- **Claude API:** ~$3/month (assumes 100 queries/day @ $0.003/1K input tokens)
- **Wikipedia API:** Free
- **Total:** ~$3/month for moderate usage

### Hardware
- Raspberry Pi Zero W 2: $15
- USB microphone: $20-40
- Push button: $2
- LED + resistor: $1
- Total: ~$40 one-time

---

## Future Enhancements

1. **WebSocket for real-time updates** (replace polling)
2. **Voice output** with Piper TTS (local, offline)
3. **Multi-turn conversations** (context awareness: "Tell me more")
4. **Response pagination** ("Tap to see more" for long responses)
5. **Offline caching** of popular queries (reduce API costs)
6. **Wake word detection** with openWakeWord ("Hey TipTop")
7. **Custom wake sound** (audio feedback when button pressed)

---

## Key Design Constraints

- **Waveshare 3.5" LCD:** 480x320px color, 60Hz refresh, SPI interface
  - ~50-60 chars/line × 20+ lines = 800-1000 char display capacity
  - Supports smooth scrolling and real-time updates
  - Higher resolution allows larger fonts and better readability
- **Pi Zero W 2 memory:** 512MB total, 300MB for Vosk model, 12MB headroom
- **Latency target:** 6-12 seconds total (acceptable for museum display UX)
- **Network:** Requires WiFi for Claude API + Wikipedia lookups
- **Power:** ~250-350mA average with LCD backlight (12-18 hours on 5000mAh battery)

---

## Existing Components to Reuse

From current codebase:
- `gadget_display.py` - Python display renderer (add conversation overlay)
- `Gadget.tsx` - React component (add 5th box for conversation)
- Express server setup (add new routes)
- PostgreSQL + Drizzle ORM (add conversation tables)
- Radix UI components (use for expanded conversation view)
- Typewriter effect logic (reuse for conversation text reveal)
