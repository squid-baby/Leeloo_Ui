const express = require('express');
const WebSocket = require('ws');
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
require('dotenv').config();

// Message types that are worth keeping for a device that is offline.
// Ephemeral ones (nudge, reaction) are only relayed live.
const QUEUEABLE_MSG_TYPES = new Set(['text', 'song_push', 'hang_propose', 'hang_confirm']);

const SPOTIFY_PENDING_TTL_MS = 3600000; // access tokens expire after 1 hour
const STATE_VERSION = 1;

function generateCrewCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // No I/O/0/1
  let code = '';
  for (let i = 0; i < 4; i++) {
    code += chars[crypto.randomInt(chars.length)];
  }
  return `LEELOO-${code}`;
}

function generateDeviceId() {
  return 'leeloo_' + crypto.randomBytes(6).toString('hex');
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Returns a trimmed, length-limited string, or '' when the value is not a usable string.
function cleanString(value, maxLength) {
  if (typeof value !== 'string') return '';
  return value.trim().slice(0, maxLength);
}

function normalizeCrewCode(value) {
  return cleanString(value, 32).toUpperCase();
}

function clientIp(req) {
  const forwarded = req.headers['x-forwarded-for'];
  const first = (Array.isArray(forwarded) ? forwarded[0] : forwarded || '').split(',')[0].trim();
  const ip = first || (req.socket && req.socket.remoteAddress) || 'unknown';
  return ip.startsWith('::ffff:') ? ip.slice(7) : ip;
}

/**
 * Build a relay instance. Nothing listens until `listen()` is called.
 *
 * Options (all optional):
 *   dataFile          path of the JSON state file, or null to disable persistence
 *   heartbeatMs       WebSocket ping interval (default 30000)
 *   saveDebounceMs    delay between a change and the write to disk (default 1000)
 *   sweepMs           interval of the expiry sweep (default 60000)
 *   queueLimit        max queued messages per device (default 50)
 *   queueTtlMs        max age of a queued message (default 24 h)
 *   joinMaxFailures   failed join attempts allowed per window (default 10)
 *   joinWindowMs      length of that window (default 10 min)
 *   maxPayload        max incoming WebSocket message size in bytes (default 64 KB)
 *   now               clock function returning ms (default Date.now)
 *   fetch             fetch implementation used for the Spotify token exchange
 *   log               logger with log/error (default console)
 *   telegramApiSecret, spotifyClientId, spotifyClientSecret, spotifyRedirectUri
 *                     default to the matching environment variables
 */
function createRelay(options = {}) {
  const now = options.now || Date.now;
  const log = options.log || console;
  const heartbeatMs = options.heartbeatMs ?? 30000;
  const saveDebounceMs = options.saveDebounceMs ?? 1000;
  const sweepMs = options.sweepMs ?? 60000;
  const queueLimit = options.queueLimit ?? 50;
  const queueTtlMs = options.queueTtlMs ?? 24 * 3600000;
  const joinMaxFailures = options.joinMaxFailures ?? 10;
  const joinWindowMs = options.joinWindowMs ?? 10 * 60000;
  const maxPayload = options.maxPayload ?? 64 * 1024;
  const fetchImpl = options.fetch || ((...args) => fetch(...args));
  const dataFile = options.dataFile === undefined
    ? (process.env.LEELOO_DATA_FILE || path.join(__dirname, 'data', 'state.json'))
    : options.dataFile;

  const SPOTIFY_CLIENT_ID = options.spotifyClientId ?? process.env.SPOTIFY_CLIENT_ID;
  const SPOTIFY_CLIENT_SECRET = options.spotifyClientSecret ?? process.env.SPOTIFY_CLIENT_SECRET;
  const SPOTIFY_REDIRECT_URI = options.spotifyRedirectUri ?? process.env.SPOTIFY_REDIRECT_URI;
  const TELEGRAM_API_SECRET = options.telegramApiSecret ?? process.env.TELEGRAM_API_SECRET ?? '';

  const app = express();
  const server = http.createServer(app);
  const wss = new WebSocket.Server({ server, maxPayload });

  // Live connections only (never persisted)
  const devices = new Map(); // device_id -> { ws, crew_code, device_name, connected_at }

  // Persisted state
  const crews = new Map();   // crew_code -> { created_at, telegram_users: Map<id, name>, members: Map<device_id, name> }
  const queues = new Map();  // device_id -> [ { crew_code, message } ]
  const pendingSpotifyTokens = new Map(); // device_id -> { tokens, timestamp }

  // Failed crew attempts: key -> [timestamps]
  const joinFailures = new Map();

  let closed = false;

  // --- Persistence ---

  let saveTimer = null;
  let dirty = false;

  function serializeState() {
    const crewsOut = {};
    crews.forEach((crew, code) => {
      crewsOut[code] = {
        created_at: crew.created_at,
        // Pairs keep the key type: Telegram user ids are numbers
        telegram_users: Array.from(crew.telegram_users.entries()),
        members: Object.fromEntries(crew.members)
      };
    });
    return {
      version: STATE_VERSION,
      saved_at: now(),
      crews: crewsOut,
      queues: Object.fromEntries(queues),
      pendingSpotifyTokens: Object.fromEntries(pendingSpotifyTokens)
    };
  }

  function loadState() {
    if (!dataFile) return;
    let raw;
    try {
      raw = fs.readFileSync(dataFile, 'utf8');
    } catch (error) {
      if (error.code !== 'ENOENT') log.error(`[STATE] Cannot read ${dataFile}:`, error.message);
      return;
    }

    let state;
    try {
      state = JSON.parse(raw);
      if (!isPlainObject(state)) throw new Error('state is not an object');
    } catch (error) {
      // Keep the bad file for inspection and start empty
      const backup = `${dataFile}.corrupt-${Date.now()}`;
      try { fs.renameSync(dataFile, backup); } catch (_) { /* ignore */ }
      log.error(`[STATE] ${dataFile} is corrupt (${error.message}); moved to ${backup}, starting empty`);
      return;
    }

    if (isPlainObject(state.crews)) {
      for (const [code, saved] of Object.entries(state.crews)) {
        if (!isPlainObject(saved)) continue;
        const crew = {
          created_at: Number(saved.created_at) || now(),
          telegram_users: new Map(),
          members: new Map()
        };
        if (Array.isArray(saved.telegram_users)) {
          for (const pair of saved.telegram_users) {
            if (Array.isArray(pair) && pair.length === 2) crew.telegram_users.set(pair[0], String(pair[1]));
          }
        }
        if (isPlainObject(saved.members)) {
          for (const [id, name] of Object.entries(saved.members)) crew.members.set(id, String(name));
        }
        crews.set(code, crew);
      }
    }

    if (isPlainObject(state.queues)) {
      for (const [id, entries] of Object.entries(state.queues)) {
        if (!Array.isArray(entries)) continue;
        const valid = entries.filter((e) => isPlainObject(e) && typeof e.crew_code === 'string' && isPlainObject(e.message));
        if (valid.length) queues.set(id, valid);
      }
    }

    if (isPlainObject(state.pendingSpotifyTokens)) {
      for (const [id, pending] of Object.entries(state.pendingSpotifyTokens)) {
        if (isPlainObject(pending) && isPlainObject(pending.tokens)) pendingSpotifyTokens.set(id, pending);
      }
    }

    log.log(`[STATE] Loaded ${crews.size} crews, ${queues.size} offline queues from ${dataFile}`);
  }

  // Synchronous atomic write: temp file in the same directory, then rename.
  function flush() {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    if (!dataFile || !dirty) return;
    try {
      fs.mkdirSync(path.dirname(dataFile), { recursive: true });
      const tmp = `${dataFile}.${process.pid}.tmp`;
      // 0600: the file can hold pending Spotify tokens
      fs.writeFileSync(tmp, JSON.stringify(serializeState()), { mode: 0o600 });
      fs.renameSync(tmp, dataFile);
      dirty = false;
    } catch (error) {
      log.error(`[STATE] Cannot save ${dataFile}:`, error.message);
    }
  }

  function scheduleSave() {
    dirty = true;
    if (!dataFile || saveTimer || closed) return;
    saveTimer = setTimeout(() => {
      saveTimer = null;
      flush();
    }, saveDebounceMs);
    saveTimer.unref();
  }

  // --- Helper functions ---

  function getOrCreateCrew(crewCode) {
    if (!crews.has(crewCode)) {
      crews.set(crewCode, {
        created_at: now(),
        telegram_users: new Map(), // user_id -> display_name
        members: new Map()         // device_id -> display name (remembered while offline)
      });
      scheduleSave();
    }
    return crews.get(crewCode);
  }

  function isOnline(deviceId, crewCode) {
    const device = devices.get(deviceId);
    return !!device && device.crew_code === crewCode && device.ws.readyState === WebSocket.OPEN;
  }

  function send(ws, message) {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }

  function broadcastToCrew(crewCode, message, excludeDeviceId = null) {
    let sentCount = 0;
    const msgJson = JSON.stringify(message);

    devices.forEach((device, id) => {
      if (id !== excludeDeviceId &&
          device.crew_code === crewCode &&
          device.ws.readyState === WebSocket.OPEN) {
        device.ws.send(msgJson);
        sentCount++;
      }
    });

    return sentCount;
  }

  function countOnline(crewCode) {
    let count = 0;
    devices.forEach((device) => {
      if (device.crew_code === crewCode) count++;
    });
    return count;
  }

  // --- Store-and-forward ---

  function isExpired(entry) {
    return now() - (entry.message.timestamp || 0) >= queueTtlMs;
  }

  // Queue a relayed message for every roster device of the crew that is offline.
  function queueForOffline(crewCode, message, excludeDeviceId = null) {
    if (!QUEUEABLE_MSG_TYPES.has(message.msg_type)) return 0;
    const crew = crews.get(crewCode);
    if (!crew) return 0;

    let queued = 0;
    crew.members.forEach((_name, id) => {
      if (id === excludeDeviceId || isOnline(id, crewCode)) return;
      const queue = (queues.get(id) || []).filter((entry) => !isExpired(entry));
      queue.push({ crew_code: crewCode, message });
      while (queue.length > queueLimit) queue.shift(); // drop oldest
      queues.set(id, queue);
      queued++;
    });
    if (queued) scheduleSave();
    return queued;
  }

  // Relay a crew message to online devices and queue it for offline ones.
  function relayCrewMessage(crewCode, message, excludeDeviceId = null) {
    const sent = broadcastToCrew(crewCode, message, excludeDeviceId);
    const queued = queueForOffline(crewCode, message, excludeDeviceId);
    return { sent, queued };
  }

  function deliverQueued(ws, deviceId, crewCode) {
    const queue = queues.get(deviceId);
    if (!queue) return;
    let delivered = 0;
    for (const entry of queue) {
      if (entry.crew_code !== crewCode || isExpired(entry)) continue;
      send(ws, { ...entry.message, queued: true });
      delivered++;
    }
    queues.delete(deviceId);
    scheduleSave();
    if (delivered) log.log(`[QUEUE] Delivered ${delivered} queued messages to ${deviceId}`);
  }

  function deliverPendingSpotifyTokens(ws, deviceId) {
    const pending = pendingSpotifyTokens.get(deviceId);
    if (!pending) return;
    // Only deliver if less than 1 hour old (token expiry)
    if (now() - pending.timestamp < SPOTIFY_PENDING_TTL_MS) {
      send(ws, { type: 'spotify_auth_complete', tokens: pending.tokens });
      log.log(`[SPOTIFY] Delivered pending tokens to ${deviceId}`);
    }
    pendingSpotifyTokens.delete(deviceId);
    scheduleSave();
  }

  // --- Rate limit of failed crew attempts ---

  function recentFailures(key) {
    const list = joinFailures.get(key);
    if (!list) return [];
    const cutoff = now() - joinWindowMs;
    const recent = list.filter((t) => t > cutoff);
    if (recent.length) joinFailures.set(key, recent);
    else joinFailures.delete(key);
    return recent;
  }

  function isRateLimited(key) {
    return recentFailures(key).length >= joinMaxFailures;
  }

  function recordFailure(key) {
    const recent = recentFailures(key);
    recent.push(now());
    joinFailures.set(key, recent);
    // Hard cap in case of a flood between two sweeps
    if (joinFailures.size > 20000) sweep();
  }

  // --- Periodic cleanup ---

  function sweep() {
    for (const key of Array.from(joinFailures.keys())) recentFailures(key);

    let changed = false;
    queues.forEach((queue, id) => {
      const kept = queue.filter((entry) => !isExpired(entry));
      if (kept.length === queue.length) return;
      if (kept.length) queues.set(id, kept);
      else queues.delete(id);
      changed = true;
    });
    pendingSpotifyTokens.forEach((pending, id) => {
      if (now() - pending.timestamp >= SPOTIFY_PENDING_TTL_MS) {
        pendingSpotifyTokens.delete(id);
        changed = true;
      }
    });
    if (changed) scheduleSave();
  }

  // --- Crew membership helpers ---

  // Names of every known crew member except the given device:
  // roster devices (online or not) plus Telegram users, without duplicates.
  function allMemberNames(crew, selfDeviceId) {
    const names = new Set();
    crew.members.forEach((name, id) => {
      if (id !== selfDeviceId) names.add(name);
    });
    crew.telegram_users.forEach((name) => names.add(name));
    return Array.from(names);
  }

  // Attach a device id to this socket (replacing any older socket with the same id).
  function bindDevice(ws, ctx, deviceId, crewCode, deviceName) {
    const previous = devices.get(deviceId);
    if (previous && previous.ws !== ws) {
      // Same device on a new socket: the old one is stale. Its close handler
      // will see that it no longer owns the id and will not announce anything.
      try { previous.ws.close(4000, 'replaced by a new connection'); } catch (_) { /* ignore */ }
    }

    // This socket changed its device id: release the old one
    if (ctx.deviceId && ctx.deviceId !== deviceId) {
      const old = devices.get(ctx.deviceId);
      if (old && old.ws === ws) devices.delete(ctx.deviceId);
    }

    ctx.deviceId = deviceId;
    devices.set(deviceId, {
      ws,
      crew_code: crewCode,
      device_name: deviceName,
      connected_at: now()
    });

    if (crewCode) {
      const crew = getOrCreateCrew(crewCode);
      if (crew.members.get(deviceId) !== deviceName) {
        crew.members.set(deviceId, deviceName);
        scheduleSave();
      }
    }
  }

  // create_crew / register with a client-supplied crew code never fail on an
  // unknown code, so they could be used to get around the join limit. Devices
  // that are not yet on the crew roster spend from the same per-IP budget.
  function crewClaimAllowed(crewCode, deviceId, ip) {
    const crew = crews.get(crewCode);
    if (crew && deviceId && crew.members.has(deviceId)) return true;
    const key = `ip:${ip}`;
    if (isRateLimited(key)) return false;
    recordFailure(key);
    return true;
  }

  function sendRateLimited(ws) {
    send(ws, {
      type: 'error',
      error: 'rate_limited',
      message: 'Too many attempts, try again later'
    });
  }

  // --- HTTP ---

  app.use(express.static(path.join(__dirname, 'public')));
  app.use(express.json());

  function telegramAuth(req, res, next) {
    if (TELEGRAM_API_SECRET) {
      const provided = req.headers['x-api-secret'];
      if (provided !== TELEGRAM_API_SECRET) {
        return res.status(401).json({ error: 'unauthorized' });
      }
    }
    if (!isPlainObject(req.body)) req.body = {};
    next();
  }

  app.get('/health', (req, res) => {
    res.json({
      status: 'ok',
      devices: devices.size,
      crews: crews.size,
      uptime: process.uptime()
    });
  });

  // --- Telegram API endpoints ---

  app.post('/api/telegram/crew/create', telegramAuth, (req, res) => {
    const { telegram_user_id, display_name } = req.body;

    if (!telegram_user_id) {
      return res.status(400).json({ error: 'telegram_user_id required' });
    }

    // Generate unique crew code
    let crewCode = generateCrewCode();
    while (crews.has(crewCode)) {
      crewCode = generateCrewCode();
    }

    const crew = getOrCreateCrew(crewCode);
    crew.telegram_users.set(telegram_user_id, cleanString(display_name, 64) || 'Unknown');
    scheduleSave();

    log.log(`[TELEGRAM] User ${telegram_user_id} created crew ${crewCode}`);
    res.json({ crew_code: crewCode });
  });

  app.post('/api/telegram/crew/join', telegramAuth, (req, res) => {
    const { telegram_user_id, display_name } = req.body;
    const code = normalizeCrewCode(req.body.crew_code);

    if (!telegram_user_id || !code) {
      return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
    }

    // With an API secret the caller is our own bot (all users share its IP), so
    // the Telegram user is the unit to limit. Without one, limit by client IP.
    const limitKey = TELEGRAM_API_SECRET ? `tg:${telegram_user_id}` : `ip:${clientIp(req)}`;
    const crew = crews.get(code);
    const alreadyMember = !!crew && crew.telegram_users.has(telegram_user_id);

    if (!alreadyMember && isRateLimited(limitKey)) {
      return res.status(429).json({ error: 'rate_limited', message: 'Too many attempts, try again later' });
    }

    if (!crew) {
      recordFailure(limitKey);
      return res.status(404).json({ error: 'crew_not_found', message: `Crew ${code} not found` });
    }

    crew.telegram_users.set(telegram_user_id, cleanString(display_name, 64) || 'Unknown');
    scheduleSave();

    const deviceCount = countOnline(code);
    log.log(`[TELEGRAM] User ${telegram_user_id} joined crew ${code} (${deviceCount} devices online)`);
    res.json({ success: true, crew_code: code, devices_online: deviceCount });
  });

  app.post('/api/telegram/message', telegramAuth, (req, res) => {
    const { telegram_user_id, payload } = req.body;
    const code = normalizeCrewCode(req.body.crew_code);
    const senderName = cleanString(req.body.sender_name, 64);
    const msgType = cleanString(req.body.msg_type, 32) || 'text';

    if (!telegram_user_id || !code) {
      return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
    }

    const crew = crews.get(code);

    if (!crew || !crew.telegram_users.has(telegram_user_id)) {
      return res.status(403).json({ error: 'not_in_crew', message: 'You are not a member of this crew' });
    }

    // Keep display name fresh (user may have changed their Telegram name)
    if (senderName && crew.telegram_users.get(telegram_user_id) !== senderName) {
      crew.telegram_users.set(telegram_user_id, senderName);
      scheduleSave();
    }

    // Build the message in the format devices expect (matches leeloo_client.py)
    const message = {
      type: 'message',
      from_device: 'telegram',
      from_name: senderName || 'Phone',
      msg_type: msgType,
      payload: isPlainObject(payload) ? payload : {},
      timestamp: now()
    };

    const { sent, queued } = relayCrewMessage(code, message);

    log.log(`[TELEGRAM] ${message.from_name} sent ${msgType} to crew ${code} (${sent} devices, ${queued} queued)`);
    res.json({ success: true, devices_reached: sent, devices_queued: queued });
  });

  app.get('/api/telegram/crew/status', telegramAuth, (req, res) => {
    const code = normalizeCrewCode(req.query.crew_code);

    if (!code) {
      return res.status(400).json({ error: 'crew_code required' });
    }

    let deviceCount = 0;
    const deviceNames = [];
    devices.forEach((device) => {
      if (device.crew_code === code) {
        deviceCount++;
        deviceNames.push(device.device_name);
      }
    });

    const crew = crews.get(code);
    const telegramUserNames = crew
      ? Array.from(crew.telegram_users.values())
      : [];
    res.json({
      crew_code: code,
      exists: !!(crew || deviceCount > 0),
      devices_online: deviceCount,
      device_names: deviceNames,
      telegram_users: telegramUserNames.length,
      telegram_user_names: telegramUserNames
    });
  });

  // --- Spotify OAuth callback ---

  app.get('/spotify/callback', async (req, res) => {
    const { code, state } = req.query;

    if (!code || typeof code !== 'string') {
      return res.status(400).send('No authorization code provided');
    }

    const deviceId = cleanString(state, 128);
    if (!deviceId) {
      return res.status(400).send('No device id (state) provided');
    }

    try {
      const tokenResponse = await fetchImpl('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Authorization': 'Basic ' + Buffer.from(SPOTIFY_CLIENT_ID + ':' + SPOTIFY_CLIENT_SECRET).toString('base64')
        },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          code: code,
          redirect_uri: SPOTIFY_REDIRECT_URI
        })
      });

      let tokens = null;
      try {
        tokens = await tokenResponse.json();
      } catch (_) {
        tokens = null;
      }

      if (!tokenResponse.ok || !isPlainObject(tokens) || tokens.error || !tokens.access_token) {
        const reason = isPlainObject(tokens) && tokens.error ? tokens.error : `HTTP ${tokenResponse.status}`;
        log.error(`[SPOTIFY] Token exchange failed for device ${deviceId}: ${reason}`);
        return res.status(502).type('text/plain')
          .send('Spotify did not accept the authorization. Please scan the code and try again.');
      }

      const tokenPayload = {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        expires_in: tokens.expires_in
      };

      const device = devices.get(deviceId);

      if (device && device.ws.readyState === WebSocket.OPEN) {
        // Device is online — send tokens immediately
        send(device.ws, { type: 'spotify_auth_complete', tokens: tokenPayload });
        log.log(`[SPOTIFY] Tokens sent to device ${deviceId}`);
      } else {
        // Device not connected yet (e.g. still in portal AP mode)
        // Store tokens for delivery when device connects
        pendingSpotifyTokens.set(deviceId, { tokens: tokenPayload, timestamp: now() });
        scheduleSave();
        log.log(`[SPOTIFY] Tokens stored for device ${deviceId} (pending delivery)`);
      }

      res.send(`
      <html>
        <head><title>LEELOO - Spotify Connected</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px; background: #1A1D2E; color: #7beec0;">
          <h1>Spotify Connected!</h1>
          <p style="color: #A7AFD4;">Your LEELOO will start showing your music once it boots up.</p>
          <p style="color: #A7AFD4;">You can close this window.</p>
        </body>
      </html>
    `);
    } catch (error) {
      log.error('Spotify OAuth error:', error);
      res.status(500).send('Failed to complete Spotify authorization');
    }
  });

  // --- WebSocket connection handling ---

  wss.on('connection', (ws, req) => {
    const ip = clientIp(req);
    log.log('New WebSocket connection from:', ip);

    const ctx = { deviceId: null };
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });

    // The device entry of this socket, or null if it has none (or was replaced)
    function currentDevice() {
      if (!ctx.deviceId) return null;
      const device = devices.get(ctx.deviceId);
      return device && device.ws === ws ? device : null;
    }

    function requestedDeviceId(data) {
      return cleanString(data.device_id, 128) || ctx.deviceId || generateDeviceId();
    }

    ws.on('message', (message) => {
      try {
        const data = JSON.parse(message);
        if (!isPlainObject(data)) return;

        switch (data.type) {
          case 'register': {
            const deviceId = cleanString(data.device_id, 128) || generateDeviceId();
            const crewCode = normalizeCrewCode(data.crew_code) || null;
            const deviceName = cleanString(data.device_name, 64) || 'LEELOO';

            if (crewCode && !crewClaimAllowed(crewCode, deviceId, ip)) {
              sendRateLimited(ws);
              break;
            }

            bindDevice(ws, ctx, deviceId, crewCode, deviceName);

            send(ws, { type: 'registered', device_id: deviceId });

            if (crewCode) deliverQueued(ws, deviceId, crewCode);
            deliverPendingSpotifyTokens(ws, deviceId);

            log.log(`Device registered: ${deviceId} (${deviceName}) crew=${crewCode}`);
            break;
          }

          case 'create_crew': {
            // Device creates a new crew (optionally with a pre-assigned code from portal setup)
            const requestedCode = normalizeCrewCode(data.crew_code);
            const deviceId = requestedDeviceId(data);
            const deviceName = cleanString(data.display_name, 64) || cleanString(data.device_name, 64) || 'LEELOO';

            let crewCode = requestedCode || generateCrewCode();
            while (!requestedCode && crews.has(crewCode)) {
              crewCode = generateCrewCode();
            }

            if (!crewClaimAllowed(crewCode, deviceId, ip)) {
              sendRateLimited(ws);
              break;
            }

            bindDevice(ws, ctx, deviceId, crewCode, deviceName);

            send(ws, { type: 'crew_created', device_id: deviceId, crew_code: crewCode });

            deliverQueued(ws, deviceId, crewCode);
            deliverPendingSpotifyTokens(ws, deviceId);

            log.log(`Device ${deviceId} created crew ${crewCode}`);
            break;
          }

          case 'join_crew': {
            const joinCode = normalizeCrewCode(data.crew_code);
            const deviceId = requestedDeviceId(data);
            const deviceName = cleanString(data.display_name, 64) || cleanString(data.device_name, 64) || 'LEELOO';
            const crew = crews.get(joinCode);

            // A device already on the roster is not guessing: never limit its rejoin
            const knownMember = !!crew && crew.members.has(deviceId);
            const limitKey = `ip:${ip}`;

            if (!knownMember && isRateLimited(limitKey)) {
              sendRateLimited(ws);
              break;
            }

            if (!crew) {
              recordFailure(limitKey);
              send(ws, {
                type: 'error',
                error: 'invalid_crew_code',
                message: `Crew ${joinCode} not found`
              });
              break;
            }

            bindDevice(ws, ctx, deviceId, joinCode, deviceName);

            // Count crew members and collect existing member names (excluding self)
            let memberCount = 0;
            const memberNames = [];
            devices.forEach((d, id) => {
              if (d.crew_code === joinCode) {
                memberCount++;
                if (id !== deviceId) memberNames.push(d.device_name);
              }
            });

            send(ws, {
              type: 'crew_joined',
              device_id: deviceId,
              crew_code: joinCode,
              crew_members: memberCount,
              member_names: memberNames,
              all_member_names: allMemberNames(crew, deviceId)
            });

            // Messages missed while offline come right after crew_joined
            deliverQueued(ws, deviceId, joinCode);
            deliverPendingSpotifyTokens(ws, deviceId);

            // Notify others
            broadcastToCrew(joinCode, {
              type: 'member_joined',
              device_id: deviceId,
              display_name: deviceName
            }, deviceId);

            log.log(`Device ${deviceId} joined crew ${joinCode} (${memberCount} members)`);
            break;
          }

          case 'message': {
            // Generic message relay (text, song_push, nudge, hang_propose, etc.)
            const msgSender = currentDevice();
            if (!msgSender || !msgSender.crew_code) break;

            const relayMsg = {
              type: 'message',
              from_device: ctx.deviceId,
              from_name: msgSender.device_name,
              msg_type: cleanString(data.msg_type, 32) || 'text',
              payload: isPlainObject(data.payload) ? data.payload : {},
              timestamp: now()
            };

            const { sent, queued } = relayCrewMessage(msgSender.crew_code, relayMsg, ctx.deviceId);
            log.log(`[MSG] ${msgSender.device_name} sent ${relayMsg.msg_type} to ${sent} devices (${queued} queued)`);
            break;
          }

          case 'share_music': {
            // Legacy music sharing
            const sender = currentDevice();
            if (!sender || !sender.crew_code) break;

            const sentCount = broadcastToCrew(sender.crew_code, {
              type: 'music_shared',
              spotify_uri: data.spotify_uri,
              artist: data.artist,
              track: data.track,
              album: data.album,
              pushed_by: sender.device_name,
              timestamp: now()
            }, ctx.deviceId);

            log.log(`Music shared from ${sender.device_name} to ${sentCount} devices`);
            break;
          }

          case 'reaction': {
            // Legacy reaction
            const reactor = currentDevice();
            if (!reactor || !reactor.crew_code) break;

            broadcastToCrew(reactor.crew_code, {
              type: 'reaction',
              reaction_type: data.reaction_type,
              from: reactor.device_name,
              timestamp: now()
            }, ctx.deviceId);
            break;
          }

          case 'ping':
            send(ws, { type: 'pong' });
            break;

          default:
            log.log('Unknown message type:', data.type);
        }
      } catch (error) {
        log.error('Error handling message:', error.message);
      }
    });

    ws.on('close', () => {
      // Only clean up if this socket still owns the device id. After a
      // reconnect the id belongs to the new socket and must stay registered.
      const device = currentDevice();
      if (!device) return;

      devices.delete(ctx.deviceId);
      if (device.crew_code) {
        broadcastToCrew(device.crew_code, {
          type: 'member_offline',
          device_id: ctx.deviceId,
          display_name: device.device_name
        }, ctx.deviceId);
      }
      log.log(`Device disconnected: ${ctx.deviceId}`);
    });

    ws.on('error', (error) => {
      log.error('WebSocket error:', error.message);
    });
  });

  // --- Heartbeat: drop sockets that did not answer the previous ping ---

  const heartbeatTimer = setInterval(() => {
    wss.clients.forEach((ws) => {
      if (ws.isAlive === false) {
        ws.terminate();
        return;
      }
      ws.isAlive = false;
      try { ws.ping(); } catch (_) { /* socket is closing */ }
    });
  }, heartbeatMs);
  heartbeatTimer.unref();

  const sweepTimer = setInterval(sweep, sweepMs);
  sweepTimer.unref();

  loadState();

  // --- Lifecycle ---

  function listen(port = 0, host) {
    return new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, host, () => {
        server.removeListener('error', reject);
        resolve(server.address().port);
      });
    });
  }

  // Stop timers, write the state to disk and close every connection.
  function close() {
    if (closed) return Promise.resolve();
    closed = true;
    clearInterval(heartbeatTimer);
    clearInterval(sweepTimer);
    flush();

    return new Promise((resolve) => {
      wss.clients.forEach((client) => client.terminate());
      wss.close(() => {
        if (!server.listening) return resolve();
        server.close(() => resolve());
        if (server.closeAllConnections) server.closeAllConnections();
      });
    });
  }

  return {
    app,
    server,
    wss,
    listen,
    close,
    flush,
    sweep,
    dataFile,
    // Exposed for tests and debugging
    state: { devices, crews, queues, pendingSpotifyTokens, joinFailures }
  };
}

module.exports = { createRelay, generateCrewCode, QUEUEABLE_MSG_TYPES };

if (require.main === module) {
  const PORT = process.env.PORT || 3000;
  const relay = createRelay();

  relay.server.listen(PORT, () => {
    console.log(`LEELOO Relay Server running on port ${PORT}`);
    console.log(`WebSocket endpoint: ws://localhost:${PORT}`);
    console.log(`Telegram API: http://localhost:${PORT}/api/telegram/...`);
    console.log(`State file: ${relay.dataFile || '(persistence disabled)'}`);
  });

  // Graceful shutdown (PM2 sends SIGINT, systemd/docker send SIGTERM)
  let shuttingDown = false;
  const shutdown = () => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log('Shutting down gracefully...');
    relay.close().then(() => process.exit(0));
    setTimeout(() => process.exit(0), 1500).unref();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
