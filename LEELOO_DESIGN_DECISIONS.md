# LEELOO Design Decisions - Questions to Answer

## Status: 🔴 PENDING ANSWERS
**Date Created:** Feb 4, 2026  
**Last Updated:** Feb 4, 2026

---

## IDLE STATE / CONSOLE DASHBOARD

### Q1: Console Layout
Does this layout work for the idle state?

```
╔══════════════════════════════════════════════════════════════╗
║  GADGET v1.0                          Chapel Hill, NC        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  ┌─ WEATHER ──────┐  ┌─ TODAY ────────┐  ┌─ TIME ────────┐ ║
║  │   .--.          │  │  ┌───┬───┬───┐ │  │               │ ║
║  │ .-(    ).       │  │  │ S │ M │ T │ │  │   2:47 PM     │ ║
║  │(___.__)__)      │  │  ├───┼───┼───┤ │  │               │ ║
║  │  72°F  ☁️       │  │  │ W │ T │ F │ │  │  Feb 4, 2026  │ ║
║  │  Partly Cloudy  │  │  │   │   │[S]│ │  │               │ ║
║  └─────────────────┘  └───────────────┘  └───────────────┘ ║
║                                                              ║
║  ┌─ TODAY'S MESSAGES ────────────────────────────────────┐  ║
║  │  • Amy (3 messages) - last at 2:34 PM                 │  ║
║  │  • Sarah (1 message) - last at 11:22 AM               │  ║
║  │  • Mike (2 messages) - last at 9:15 AM                │  ║
║  │                                                        │  ║
║  └────────────────────────────────────────────────────────┘  ║
║                                                              ║
║  [Tap to send a message]                                     ║
╚══════════════════════════════════════════════════════════════╝
```

**Answer:**  
_[Your thoughts here]_

**Suggested changes (if any):**  
_[Any modifications you want]_

---

### Q2: Album Art / Spotify Code Placement
Should the album art/Spotify code appear somewhere on the idle console?

**Options:**
- A) Only appear when someone shares a song (replaces console temporarily)
- B) Always visible in a dedicated area (where?)
- C) Not on idle screen at all

**Answer:**  
_[A, B, or C + details]_

---

### Q3: Weather Animation
Should the weather ASCII art animate?

**Options:**
- A) Static (no animation, simpler)
- B) Subtle animation (clouds drift slowly, rain falls gently)
- C) Dynamic (changes based on weather - sun rays, snowflakes, etc.)

**Answer:**  
_[A, B, or C]_

---

### Q4: Message Feed Style
How should the "Today's Messages" section look?

**Option A - Names + count only:**
```
┌─ TODAY'S MESSAGES ──────┐
│  • Amy (3)              │
│  • Sarah (1)            │
│  • Mike (2)             │
└─────────────────────────┘
```

**Option B - Names + last message preview:**
```
┌─ TODAY'S MESSAGES ──────────────┐
│  • Amy: "Dinner tonight?"       │
│  • Sarah: "See you later!"      │
│  • Mike: "Running late"         │
└─────────────────────────────────┘
```

**Answer:**  
_[A or B]_

---

## WAKE-UP ANIMATION (Incoming/Outgoing Messages)

### Q5: Wake-up Animation Speed
When a message arrives and the "pixel bars clap and reopen" - how fast?

**Options:**
- A) Fast (0.5 seconds total - snappy)
- B) Medium (1 second total - smooth)
- C) Slow (1.5 seconds total - dramatic)

**Answer:**  
_[A, B, or C]_

---

### Q6: Different Animations for Incoming vs Outgoing?
Should sending a message have a different animation than receiving one?

**Options:**
- A) Same animation for both
- B) Different animations (e.g., bars close vertically for incoming, horizontally for outgoing)

**Answer:**  
_[A or B + details if B]_

---

### Q7: Sound Effect Placeholder?
Even without a speaker now, should we plan for sound effects?

**Options:**
- A) Yes, add hooks in code for when we add speaker later
- B) No, keep it silent-first design

**Answer:**  
_[A or B]_

---

## MESSAGE DISPLAY SEQUENCE

### Q8: Typewriter Speed
When message types out character-by-character, how fast?

**Options:**
- A) Fast (50ms per character - feels digital)
- B) Medium (100ms per character - classic typewriter)
- C) Slow (200ms per character - dramatic/vintage)
- D) Word-by-word instead of character-by-character

**Answer:**  
_[A, B, C, or D]_

---

### Q9: ASCII Animation Duration
How long should flair animations (champagne toast, etc.) play?

**Options:**
- A) Quick (2-3 seconds)
- B) Medium (4-5 seconds)
- C) Long (6-8 seconds)

**Answer:**  
_[A, B, or C]_

---

### Q10: Message Persistence After Animation
After the flair animation plays and message reappears, how long before returning to console?

**Options:**
- A) Short (5-10 seconds)
- B) Medium (15-20 seconds)
- C) Long (30+ seconds)
- D) Until next tap (manual dismiss)

**Answer:**  
_[A, B, C, or D]_

---

## CONVERSATIONAL AI BEHAVIOR

### Q11: Device Response Display
When the device "talks back" (e.g., "What's the message?"), should it:

**Options:**
- A) Type out character-by-character (like incoming messages)
- B) Appear instantly (faster interaction)

**Answer:**  
_[A or B]_

---

### Q12: Response Latency
Each back-and-forth (voice → transcribe → GPT → respond) takes ~3-5 seconds. Is this acceptable?

**Answer:**  
_[Yes/No + any concerns]_

---

### Q13: All-in-One Commands
What if user says everything at once?

Example: "Send a message to the group with champagne, anyone want dinner?"

**Options:**
- A) AI should parse it all and just confirm
- B) Still walk through steps (ask for flair even if they mentioned it)
- C) Smart hybrid (if flair mentioned, skip asking; if not, ask)

**Answer:**  
_[A, B, or C]_

---

### Q14: GPT Model Choice
Which AI model for conversation handling?

**Options:**
- A) GPT-4o (smarter, ~$0.01 per conversation)
- B) GPT-4o-mini (cheaper, ~$0.001 per conversation, slightly less capable)

**Answer:**  
_[A or B]_

---

### Q15: Conversation Context Duration
How long should the AI remember conversation context?

**Options:**
- A) Until message is sent (then reset)
- B) For entire "session" until screen returns to idle
- C) Forever (device remembers you were sending messages even hours later)

**Answer:**  
_[A, B, or C]_

---

## ASCII FLAIR ANIMATIONS

### Q16: Number of Animations for MVP
How many flair animations should we build initially?

**Suggestions:**
- Champagne toast (celebrate)
- Coffee/tea (casual meetup)
- Hearts (love/friendship)
- Birthday cake (celebrations)
- Party popper (excitement)
- Pizza slice (food related)
- Star/sparkles (magic/special)

**Answer:**  
_[Pick 3-5 to start with]_

---

### Q17: Animation Looping
Should animations loop or play once?

**Options:**
- A) Play once (clean, finite)
- B) Loop 2-3 times (emphasis)
- C) Loop continuously until next screen (could get annoying?)

**Answer:**  
_[A, B, or C]_

---

## TECHNICAL RENDERING

### Q18: Rendering Method
Confirmed: We'll simulate Textualize aesthetic with Pillow/PIL (not actual Textualize library).

**Using:**
- Monospace font (DejaVu Sans Mono)
- Box-drawing characters (╔═╗ style)
- Green/amber terminal colors on black background

**Anything else you want in the visual style?**  
_[Any specific colors, font size, border styles?]_

---

## ADDITIONAL QUESTIONS

### Q19: Left Column Layout
Which information sections should be included? (pick all that apply)

**Current design has:**
- ✅ Weather (with ASCII art)
- ✅ Time/Date
- ✅ Messages (today's count)
- ✅ Now Playing (song info)
- ✅ Activity bars (visual indicator)

**Alternatives:**
- Add "Last Message" preview box?
- Remove Activity bars for more space?
- Combine Time + Weather into one box?

**Answer:**  
_[Keep current / suggest changes]_

---

### Q20: Animated Elements
Which elements should animate on the idle screen?

**Options:**
- A) Activity bars pulse with message/music activity
- B) Weather (clouds drift, rain falls)
- C) "TAP TO SEND" fades/pulses
- D) Message count blinks when new
- E) All of the above
- F) None (static is cleaner)

**Answer:**  
_[Pick options]_

---

### Q21: Color Theme
Should we support multiple color themes or stick with one?

**Current:** Multi-color (Cyan/Magenta/Yellow/Green/Orange)

**Alternatives:**
- Matrix theme only (keep current)
- Add switchable themes (Amber CRT, Hacker Green, Synthwave)
- User customizable colors?

**Answer:**  
_[One theme / multiple themes]_

---

### Q22: Album Art Always Visible?
Keep the album art + Spotify code persistent on the right side?

**Options:**
- A) Yes, always visible (current design)
- B) Hide when new message arrives, show message full-screen
- C) Shrink album art when message arrives, show both
- D) Only show album art when music is actively shared

**Answer:**  
_[A, B, C, or D]_

---

_[New questions will be added here as they come up]_

---

## NEXT STEPS AFTER DECISIONS ARE MADE

1. ✅ Update README with confirmed design
2. Set up development environment (Pi libraries)
3. Build console dashboard renderer
4. Implement wake-up animation
5. Create ASCII art library with chosen animations
6. Integrate Whisper API for voice transcription
7. Build conversational AI with GPT
8. Set up Firebase for group messaging
9. Wire up accelerometer tap detection
10. Test complete flow end-to-end

---

## NOTES / THOUGHTS
_[Use this space for any additional ideas, concerns, or things to remember]_
