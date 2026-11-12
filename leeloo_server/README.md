# LEELOO Relay Server

WebSocket relay server for LEELOO music sharing devices.

## Features

- 🔌 **WebSocket connections** - Real-time device communication
- 🎵 **Music sharing** - Share Spotify tracks between crew members
- 🔐 **Spotify OAuth** - Secure authorization flow
- ❤️ **Reactions** - Send emoji reactions to crew
- 🚀 **Production ready** - PM2 process management, SSL, auto-restart

## Production Server

- **URL**: https://leeloobot.xyz
- **Server**: 138.197.75.152 (DigitalOcean)
- **Process Manager**: PM2

## Security Best Practices

1. **Never commit secrets** - `.env` is in `.gitignore`
2. **Use `.env.example`** - Template for what variables are needed
3. **Server-side only** - Credentials never leave the server
4. **SSL/TLS** - All connections over HTTPS/WSS
5. **Firewall** - Only ports 22, 80, 443 open

## Environment Variables

Create a `.env` file (never commit this!):

```bash
PORT=3000
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=https://leeloobot.xyz/spotify/callback
```

**⚠️ SECURITY: Never commit `.env` to Git!**

## State, offline messages and tests

- **State file**: crews (with the device roster and Telegram members), offline
  message queues and pending Spotify tokens are saved as JSON in
  `LEELOO_DATA_FILE` (default `./data/state.json` next to `server.js`). The
  directory is created on first save. It is written about 1 s after a change
  (temp file + rename) and on shutdown. Keep `data/` out of Git and back it up
  if you care about crews surviving a server rebuild.
- **Store-and-forward**: `text`, `song_push`, `hang_propose` and `hang_confirm`
  messages are kept for crew devices that are offline (50 per device, 24 h) and
  sent right after `crew_joined` with `queued: true`. `nudge` and `reaction`
  are live only.
- **Limits**: 10 failed crew-code attempts per client IP in 10 minutes
  (`rate_limited` error / HTTP 429), 64 KB per WebSocket message, 30 s
  WebSocket heartbeat. The client IP is the first address in
  `X-Forwarded-For`, so nginx must set that header.
- **Tests**: `npm install && npm test` (uses `node:test`, Node 18 or newer).

## Deployment

```bash
./deploy.sh
```

## Server Management

```bash
# Check status
pm2 status

# View logs
pm2 logs leeloo-relay

# Restart
pm2 restart leeloo-relay
```

Made with ♪ by squid-baby
