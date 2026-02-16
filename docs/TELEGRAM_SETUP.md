# LEELOO Telegram Bot Setup

## What is the Telegram Bot?

The Telegram bot is an **optional alternative interface** for LEELOO. Instead of using voice commands on your device, you can:
- Send messages to your crew from your phone
- Share music via Telegram
- Check your crew status
- Get pairing codes for new devices

## Current Status

✅ **Telegram bot code exists** (`leeloo_server/telegram_bot.py`)
❌ **Not yet integrated with the relay server**
❌ **Not part of the captive portal setup**

## How It Works (Architecture)

```
You → Telegram Bot → Relay Server → Your Crew's LEELOOs
```

The bot acts as a bridge between Telegram and your LEELOO devices.

## Setting Up the Bot

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Choose a name: `LEELOO Music Bot` (or whatever you want)
4. Choose a username: `your_leeloo_bot` (must end in `_bot`)
5. BotFather will give you a **token** - save this!

Example token: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

### 2. Set Environment Variable

On your server (not the Pi), set the bot token:

```bash
export LEELOO_BOT_TOKEN="your_token_here"
```

Or add to your `.bashrc` / `.zshrc`:

```bash
echo 'export LEELOO_BOT_TOKEN="your_token_here"' >> ~/.bashrc
source ~/.bashrc
```

### 3. Run the Bot

```bash
cd /Users/nathanmills/Desktop/TipTop\ UI/leeloo_server
python3 telegram_bot.py
```

You should see:
```
Starting LEELOO Telegram Bot...
Bot is running! Press Ctrl+C to stop.
```

### 4. Test It

1. Open Telegram
2. Search for your bot: `@your_leeloo_bot`
3. Send `/start`
4. You should see the welcome menu!

## Features (Currently Standalone)

The bot currently works in **demo mode** without a backend server. It can:

✅ Create crew codes
✅ Join crews
✅ Show crew info
✅ Generate pairing codes
✅ Accept messages (but they don't go to devices yet)

## Integrating with LEELOO Devices

To make the bot actually send messages to your devices, you need:

1. **Relay Server** - Central server that coordinates messages
2. **WebSocket Connection** - Between devices and relay server
3. **Bot Bridge** - Connection between Telegram bot and relay server

### Missing Pieces:

- [ ] Relay server implementation (`leeloo_server/relay_server.py` exists but incomplete)
- [ ] WebSocket client on Pi devices
- [ ] Bot → Server → Device message flow

## Future Implementation

### Phase 1: Deploy Relay Server
```bash
# On a cloud server (not the Pi)
cd leeloo_server
python3 relay_server.py
```

### Phase 2: Update Pi Devices
Add WebSocket client to connect to relay server:
```python
# In gadget_main.py
from network import WebSocketClient
ws = WebSocketClient('wss://your-relay-server.com')
```

### Phase 3: Connect Telegram Bot
Update `telegram_bot.py` to send messages to relay server instead of just storing them locally.

## For Now: Manual Setup Flow

Since Telegram isn't integrated yet, here's the **manual flow**:

### Option A: Skip Telegram (Recommended for now)
- Use voice commands on the device
- Wait for full Telegram integration in a future update

### Option B: Run Bot for Testing
1. Set up bot as described above
2. Use it to generate crew codes
3. Manually enter codes on your LEELOO devices
4. Messages won't actually send to devices (yet)

## Next Steps

Would you like me to:

1. **Add Telegram to the captive portal** - Optional step during setup to connect your Telegram account
2. **Implement the relay server** - Get the full message flow working
3. **Skip Telegram for now** - Focus on getting voice/music features working first

Based on the architecture doc, Telegram is **Phase 7** (week 10), so it's not critical for initial functionality. The device works fine without it using voice commands!

## Questions?

Let me know if you want to:
- Add Telegram setup to the captive portal
- Deploy the relay server
- Or skip Telegram and focus on voice/music features first
