# Natural Language Music Requests for LEELOO

## Vision

Users should be able to say:
```
"share sabotage by beastie boys with my homies"
"play mr brightside by the killers"
"send bohemian rhapsody to my crew"
```

And LEELOO will:
1. Parse the natural language request
2. Search Spotify for the track
3. Display the scancode on screen
4. Share with the user's crew via Telegram

## Current Status

✅ **Implemented:**
- Natural language parser (`music_request_parser.py`)
- Handles "share [track] by [artist]" format
- Removes filler words ("with my homies", etc.)
- Scancode display with correct dimensions (304x304)

⚠️ **Requires Spotify OAuth:**
- Spotify search API requires authentication
- Need to set up OAuth credentials

🔨 **Workaround for Testing:**
For now, you can use direct Spotify URLs:

```bash
# Find the track on Spotify web, copy the URL
python3 test_spotify_display.py "https://open.spotify.com/track/5eLnp81u8w5mg9HxdAUPQA"
```

## Setting Up Spotify OAuth (Future)

To enable natural language search, we'll need to:

1. Create a Spotify app at https://developer.spotify.com/dashboard
2. Get Client ID and Client Secret
3. Implement OAuth flow in `leeloo_spotify.py`
4. Store credentials in environment variables

## Quick Reference

### Supported Natural Language Formats

```python
"share [track] by [artist]"
"play [track] by [artist]"
"send [track] by [artist]"
"[track] by [artist]"
```

### Filler Words (Automatically Removed)

- "with my homies"
- "with my crew"
- "with my friends"
- "to my crew"

### Examples

```bash
# These all work the same:
python3 test_spotify_display.py share sabotage by beastie boys
python3 test_spotify_display.py share sabotage by beastie boys with my homies
python3 test_spotify_display.py play sabotage by beastie boys
python3 test_spotify_display.py sabotage by beastie boys

# Direct URL (works without OAuth):
python3 test_spotify_display.py https://open.spotify.com/track/5eLnp81u8w5mg9HxdAUPQA
```

## Integration with Telegram Bot

Once the Telegram bot is set up, users will be able to:

1. Send a voice message: "share sabotage by beastie boys with my homies"
2. Bot transcribes speech → parses request → searches Spotify
3. Bot sends track to LEELOO device
4. LEELOO displays scancode on screen
5. Bot shares track with user's crew

This creates the full "music sharing" experience!
