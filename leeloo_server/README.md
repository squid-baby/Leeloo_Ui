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
  message queues, pending Spotify tokens, agent link hashes, active packs
  and pending pack installs are saved as JSON in
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

## Agent connect and packs

Full contract: `../docs/PACKS.md`. All of it is in `server.js` (the deploy
copies only that file).

- **Connect**: the device sends `agent_connect_request` and gets a one-use
  code (8 characters, 15 minutes, kept in memory). `GET /connect/<code>` shows
  a page with one button. `POST /connect/<code>` makes the connector URL
  `PUBLIC_BASE_URL/mcp/<token>`. Only the SHA-256 hash of the token is saved.
  At most 5 links per device. `agent_disconnect` from the device deletes them.
- **MCP**: `POST /mcp/<token>` is a stateless MCP server (JSON-RPC 2.0, JSON
  replies, 64 KB body limit, no SSE: `GET` gives 405). Tools: `get_status`,
  `get_pack_guide`, `validate_pack`, `install_pack`, `share_pack`,
  `reset_pack`, `send_message`. An agent can send 20 crew messages/offers in
  10 minutes per link.
- **Packs**: `validatePack()` checks every rule of PACKS.md §1. Invalid packs
  are never relayed, stored or installed. `pack_offer` messages are queued for
  offline devices like text messages. A `pack_install` / `pack_reset` for an
  offline device is kept (the newest one) until it connects.
- **Sources**: for the active packs (`pack_active`) of connected devices, the
  relay fetches each source URL one time per interval (the smallest `every`
  among its users) and sends `pack_data` to those devices. Limits: https only,
  host allowlist, no redirects, 10 s timeout, 2 MB.
- **Environment**:
  - `PUBLIC_BASE_URL` (default `https://leeloobot.xyz`): base of the connect
    and connector links.
  - `LEELOO_SOURCE_HOSTS` (default `site.api.espn.com,api.open-meteo.com`):
    comma list of hosts that pack sources may use.
- **nginx**: `/connect/` and `/mcp/` must be proxied to the relay like the
  other routes, with `X-Forwarded-For` set. Do not write the path of `/mcp/`
  requests to the access log: the token in the URL is a key.
- The pack guide text and the example pack are copies of `docs/PACKS.md` §1
  and `packs/unc-hoops.json` inside `server.js`. `npm test` fails when they
  differ: update `PACK_GUIDE` / `EXAMPLE_PACK` after you change the docs.

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
