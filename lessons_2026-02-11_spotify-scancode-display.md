# Lessons: LEELOO Spotify Scancode Display - Text Box Constraint Bug

**Date:** 2026-02-11
**Goal:** Implement Spotify scancode display with album art and text info, constrained to 210px-wide text box

---

## Critical Issues & Solutions

### 1. Text Overflowing Album Frame (BLOCKER)

**Problem:** Text (artist name, track name, listeners) was rendering outside the album frame boundaries, despite implementing truncation.

**Root Cause:** Misunderstanding of two-step layout process:
1. Text was truncated to `text_max_width = 210px` ✅
2. BUT text was then centered using `box_width` (full frame width ~240px) ❌
3. The centering calculation used the larger box, causing text to extend beyond the 210px constraint

**Why we didn't catch this earlier:**
- Assumed that truncating text to 210px would automatically constrain its position
- Didn't consider that centering logic operates independently from truncation
- The `center_text_in_box()` function was receiving the wrong width parameter

**The Fix:**
```python
# BEFORE (WRONG):
box_width = box_right - 7  # Available width for centering
text_max_width = 210  # Fixed 210px constraint for text

artist_truncated = truncate_text(artist_text, self.font_large, text_max_width)
artist_x = 7 + center_text_in_box(artist_truncated, self.font_large, box_width)  # ❌ Wrong!

# AFTER (CORRECT):
text_box_width = 210  # Fixed 210px text box constraint
box_width = box_right - 7  # Total available width
text_box_left = 7 + (box_width - text_box_width) // 2  # Calculate text box position

artist_truncated = truncate_text(artist_text, self.font_large, text_box_width)
artist_x = text_box_left + center_text_in_box(artist_truncated, self.font_large, text_box_width)  # ✅ Correct!
```

**Lesson Learned:**
- ✅ When constraining UI elements, you need to manage BOTH size AND position
- ✅ Truncation limits the content size, but positioning is a separate concern
- ✅ Create a "text box" abstraction: calculate its position once, then position all text relative to it
- ✅ Use the same width value for truncation AND centering calculations

---

### 2. Display Oscillating Between Correct/Incorrect State (MAJOR)

**Problem:** After fixing the code and copying to Pi, the display was oscillating between correct (fixed text box) and incorrect (overflowing text) every second.

**Root Cause:** The main loop process (`gadget_main.py`) was still running with the old code loaded in memory. When the test script ran, it used the NEW code and rendered correctly. Then the main loop re-rendered using OLD code.

**Why we didn't catch this earlier:**
- Forgot that Python loads modules into memory at startup
- Didn't check if `gadget_main.py` was already running before testing
- Assumed copying files would immediately affect the running process

**The Fix:**
```bash
# Kill all running instances
sudo pkill -f gadget_main.py

# Restart with new code
cd /home/pi/leeloo-ui && nohup sudo python3 gadget_main.py > gadget_main.log 2>&1 &
```

**Lesson Learned:**
- ✅ ALWAYS check for running processes before testing code changes: `ps aux | grep gadget_main`
- ✅ Python processes don't auto-reload when you change source files - they keep old code in memory
- ✅ For daemon processes, implement a restart workflow: kill → copy files → restart
- ✅ Oscillating UI behavior is a red flag for multiple processes with different code versions

---

### 3. Monthly Listeners Not Showing (MINOR)

**Problem:** The monthly listeners count wasn't being displayed in the album info box.

**Root Cause:**
1. Spotify's track pages don't contain monthly listeners (it's on the artist page)
2. Web scraping approach tried to find listeners on track page
3. Spotify pages are heavily JavaScript-rendered, so data may not be in initial HTML

**The Fix:**
```python
# Try to get artist URI from track page, then fetch artist page
artist_uri_match = re.search(r'spotify:artist:([a-zA-Z0-9]+)', response.text)
if artist_uri_match:
    artist_id = artist_uri_match.group(1)
    artist_url = f"https://open.spotify.com/artist/{artist_id}"
    artist_response = requests.get(artist_url, timeout=10)
    listeners_match = re.search(r'([\d,]+)\s+monthly listeners', artist_response.text)

# Fallback to placeholder if scraping fails
'listeners': listeners if listeners else '○○○'
```

**Lesson Learned:**
- ✅ Web scraping is fragile - always implement graceful fallbacks
- ✅ Data location matters: track page ≠ artist page
- ✅ JavaScript-rendered content may require headless browser (Selenium/Puppeteer)
- ✅ For production, use official APIs with OAuth instead of scraping

---

### 4. Album Name Missing from Display (MINOR)

**Problem:** Album name wasn't showing in the display, only artist and track.

**Root Cause:** Simply forgot to add the album field to the rendering logic after adding it to the data structure.

**The Fix:**
```python
# Added album name rendering between artist and track
album_text = album_data.get('album', '')
if album_text:
    album_truncated = truncate_text(album_text, self.font_tiny, text_box_width)
    album_x = text_box_left + center_text_in_box(album_truncated, self.font_tiny, text_box_width)
    self.draw.text((album_x, content_y), album_truncated, font=self.font_tiny, fill=COLORS['green'])
    content_y += 14

# Also updated album_data dict in gadget_main.py and test script
album_data = {
    'artist': music_data.get('artist', ''),
    'album': music_data.get('album', ''),  # Added this line
    'track': music_data.get('track', ''),
    # ...
}
```

**Lesson Learned:**
- ✅ When adding new data fields, check ALL places: data source → data structure → rendering logic
- ✅ Don't assume scraping will get all fields - verify what's actually available

---

## Key Takeaways

### 🎯 Debug Smarter
1. **Oscillating UI = Multiple processes with different code** - Always check `ps aux` before debugging UI issues
2. **Test layout constraints with long text** - "Si Te Pica Es Porque Eres Tú" helped expose the text overflow bug
3. **Memory vs disk state** - Copying files doesn't update running Python processes; restart required

### 🎯 Build Better
1. **UI constraints need position + size** - Create a "box" abstraction with calculated position, then render relative to it
2. **Separate truncation from positioning** - They're independent operations that both need the same width value
3. **Web scraping needs fallbacks** - Always provide placeholder data when scraping fails
4. **Data consistency across layers** - When adding fields like 'album', update: scraper → data dict → rendering → persistence

### 🎯 Layout Math Pattern
```python
# THE PATTERN: Fixed-width box inside variable-width frame
text_box_width = 210           # Your constraint
frame_width = box_right - 7     # Available space
text_box_left = 7 + (frame_width - text_box_width) // 2  # Center the box

# Then for each text element:
truncated = truncate_text(text, font, text_box_width)      # Fit to constraint
x_offset = center_text_in_box(truncated, font, text_box_width)  # Center in box
x_pos = text_box_left + x_offset                           # Final position
```

---

## Files Changed

| File | Changes | Why |
|------|---------|-----|
| `gadget_display.py` | Fixed text box constraint logic - calculate `text_box_left` once, position all text relative to it | Text was overflowing frame |
| `gadget_display.py` | Added album name rendering | Missing from display |
| `test_spotify_display.py` | Added artist page scraping for monthly listeners | Data not on track page |
| `test_spotify_display.py` | Added 'album' field to album_data | Support album display |
| `gadget_main.py` | Added 'album' field to album_data | Support album display |
| `leeloo_config.py` | Fixed path detection for macOS vs Pi | Previous session |

---

## Technical Context

- **Display:** 480x320 LCD framebuffer
- **Sharing image:** 243x304px (4:5 aspect ratio)
- **Text box constraint:** 210px wide, centered in frame
- **Scancode:** Full width (243px), pinned to bottom
- **Album art:** Fills space above scancode (243x244px)
- **Fonts:** `font_large` for artist/track, `font_tiny` for album/listeners
- **Framework:** PIL/Pillow for image manipulation, custom framebuffer rendering

---

**Tags:** #leeloo #spotify #ui-layout #text-constraints #pillow #raspberry-pi #process-management #web-scraping

**Keywords:** text overflow, centering bugs, constrained layout, PIL text rendering, process reload, oscillating display, truncate_text, center_text_in_box, Spotify API, web scraping fallbacks
