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

## Device keys and limits

- **Device key**: a `device_id` is public (the crew sees it), so it proves
  nothing. The first `register` / `create_crew` / `join_crew` of an id gets
  `device_key` (64 hex characters) in the reply (`registered`,
  `crew_created`, `crew_joined`). The device must save it and send it as
  `device_key` in every later one of these three messages. Without the right
  key the reply is `{type:'error', error:'device_key_required'}`, nothing is
  bound, the connected device stays connected, and the attempt counts for the
  per-IP limit. The relay saves only the SHA-256 hash (`deviceKeys` in the
  state file). Devices from before the keys get one at their next bind.
  **Update the device code before the relay**: an old client does not save
  the key and cannot connect a second time.
- **Devices from before the keys** (on a roster, no key stored): the first
  bind must name the crew code of that roster. That bind gets a key and
  nothing else: queued messages, pending Spotify tokens and pending pack
  installs of the id are discarded, and its agent links are deleted. (Somebody
  who knows the crew code could be first, so the relay gives that bind no
  secrets.) Pending Spotify tokens are deleted on every bind that makes a
  key: a device must have registered one time before its Spotify login.
- **Lost key** (new SD card, restored config): stop the relay, run
  `node reset_device_key.js data/state.json <device_id>`, start the relay.
  The tool removes the key hash and the agent links of that one device and
  prints what it did (never a key or a hash). The device then joins its crew
  again and gets a new key.
- **One crew per device**: a device that binds to another crew leaves the old
  roster, and its agent links and open connect codes are deleted.
- **Limits** (options of `createRelay`): `maxCrewMembers` 12 devices per crew.
  When the roster is full and a new device joins, a member can lose its
  place only if it is not connected and (a) was not seen for 30 days
  (`evictAfterMs`), or (b) is a "ghost": bound one time in its life, 24 h ago
  or more (`ghostAfterMs`). Ghosts go first. The one that leaves loses its
  queue, pack, agent links and pending Spotify tokens; its key stays. No
  such member: `crew_full`. Members from older state files count as real
  devices seen at the first start of this version.
  The first join of a device to a roster costs one unit of the per-IP
  budget. `maxCrews` 5000 (`server_full` error); a crew with no members, no
  Telegram users and no queue is removed after 24 h. `maxCrewSourceUrls` 6,
  `maxSourceUrlsPerIp` 6 (per client address; IPv6: per /64) and
  `maxSourceUrls` 50 source
  URLs polled at one time (sources over a limit are not polled, the pack
  stays active), `maxDevicePacks` 2000, `maxDeviceKeys` 20000. A new device
  id without a crew uses the per-IP budget of failed attempts. `pack_active`
  is accepted only from a device in a crew, 20 changes in 10 minutes per
  device id. `maxFetchesPerMinute` 60 source requests for the whole relay,
  and no URL twice within 10 s.
- **Messages**: the JSON of a `payload` is 2,048 bytes at most (9,000 for
  `pack_offer`), else `message_too_large`. `maxMessagesPerMinute` 30 relayed
  messages per device (`rate_limited`). Offline queues: 50 messages and
  `queueMaxBytes` 64 KB per device, `crewQueueMaxBytes` 512 KB per crew,
  `queuesMaxBytes` 20 MB for all. Over a limit the oldest messages go: of
  the device, of the crew, or (total) of the crew that holds the most bytes.
  The Telegram message route has the same size and rate limits (per
  Telegram user).
- **Network**: the relay listens on `127.0.0.1` (env `HOST`, option `host`)
  because nginx is in front of it. Set `HOST=0.0.0.0` only when there is no
  proxy. The per-IP limiter takes the client address from `X-Forwarded-For`,
  so nginx must SET that header (`proxy_set_header X-Forwarded-For
  $remote_addr;`), not append to it. IPv6 clients are limited per /64.

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
  `reset_pack`, `send_message`. Per link in 10 minutes: 20 crew
  messages/offers, 10 installs/resets. A link works only while its device is
  in the crew it was made for.
- **Packs**: `validatePack()` checks every rule of PACKS.md §1 (the relay
  accepts 7,800 bytes, the device 8,000). Invalid packs
  are never relayed, stored or installed. `pack_offer` messages are queued for
  offline devices like text messages. A `pack_install` / `pack_reset` for an
  offline device is kept (the newest one) until it connects.
- **Sources**: for the active packs (`pack_active`) of connected devices, the
  relay fetches each source URL one time per interval (the smallest `every`
  among its users) and sends `pack_data` to those devices. Only the values
  that the packs ask for are kept, not the replies. Limits: https only, host
  allowlist, no redirects, 10 s timeout, 2 MB.
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
