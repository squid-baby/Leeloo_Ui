const express = require('express');
const WebSocket = require('ws');
const http = require('http');
const crypto = require('crypto');
const path = require('path');
require('dotenv').config();

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Configuration
const PORT = process.env.PORT || 3000;
const SPOTIFY_CLIENT_ID = process.env.SPOTIFY_CLIENT_ID;
const SPOTIFY_CLIENT_SECRET = process.env.SPOTIFY_CLIENT_SECRET;
const SPOTIFY_REDIRECT_URI = process.env.SPOTIFY_REDIRECT_URI;
const TELEGRAM_API_SECRET = process.env.TELEGRAM_API_SECRET || '';

// In-memory storage
const devices = new Map(); // device_id -> { ws, crew_code, device_name, connected_at }
const crews = new Map();   // crew_code -> { created_at, telegram_users: Set<int> }
const pendingSpotifyTokens = new Map(); // device_id -> { tokens, timestamp }

// Serve static landing page
app.use(express.static('public'));
app.use(express.json());

// --- Helper functions ---

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

function getOrCreateCrew(crewCode) {
  if (!crews.has(crewCode)) {
    crews.set(crewCode, {
      created_at: Date.now(),
      telegram_users: new Set()
    });
  }
  return crews.get(crewCode);
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

// --- Telegram API auth middleware ---

function telegramAuth(req, res, next) {
  if (TELEGRAM_API_SECRET) {
    const provided = req.headers['x-api-secret'];
    if (provided !== TELEGRAM_API_SECRET) {
      return res.status(401).json({ error: 'unauthorized' });
    }
  }
  next();
}

// --- Health check ---

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
  const { telegram_user_id } = req.body;

  if (!telegram_user_id) {
    return res.status(400).json({ error: 'telegram_user_id required' });
  }

  // Generate unique crew code
  let crewCode = generateCrewCode();
  while (crews.has(crewCode)) {
    crewCode = generateCrewCode();
  }

  const crew = getOrCreateCrew(crewCode);
  crew.telegram_users.add(telegram_user_id);

  console.log(`[TELEGRAM] User ${telegram_user_id} created crew ${crewCode}`);
  res.json({ crew_code: crewCode });
});

app.post('/api/telegram/crew/join', telegramAuth, (req, res) => {
  const { telegram_user_id, crew_code } = req.body;

  if (!telegram_user_id || !crew_code) {
    return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
  }

  const code = crew_code.toUpperCase();

  // Check if any device is registered with this crew code, or if the crew exists
  let crewExists = crews.has(code);
  if (!crewExists) {
    devices.forEach((device) => {
      if (device.crew_code === code) crewExists = true;
    });
  }

  if (!crewExists) {
    return res.status(404).json({ error: 'crew_not_found', message: `Crew ${code} not found` });
  }

  const crew = getOrCreateCrew(code);
  crew.telegram_users.add(telegram_user_id);

  // Count connected devices in this crew
  let deviceCount = 0;
  devices.forEach((device) => {
    if (device.crew_code === code) deviceCount++;
  });

  console.log(`[TELEGRAM] User ${telegram_user_id} joined crew ${code} (${deviceCount} devices online)`);
  res.json({ success: true, crew_code: code, devices_online: deviceCount });
});

app.post('/api/telegram/message', telegramAuth, (req, res) => {
  const { telegram_user_id, crew_code, sender_name, msg_type, payload } = req.body;

  if (!telegram_user_id || !crew_code) {
    return res.status(400).json({ error: 'telegram_user_id and crew_code required' });
  }

  const code = crew_code.toUpperCase();
  const crew = crews.get(code);

  if (!crew || !crew.telegram_users.has(telegram_user_id)) {
    return res.status(403).json({ error: 'not_in_crew', message: 'You are not a member of this crew' });
  }

  // Build the message in the format devices expect (matches leeloo_client.py)
  const message = {
    type: 'message',
    from_device: 'telegram',
    from_name: sender_name || 'Phone',
    msg_type: msg_type || 'text',
    payload: payload || {},
    timestamp: Date.now()
  };

  const sentCount = broadcastToCrew(code, message);

  console.log(`[TELEGRAM] ${sender_name || 'Phone'} sent ${msg_type || 'text'} to crew ${code} (${sentCount} devices)`);
  res.json({ success: true, devices_reached: sentCount });
});

app.get('/api/telegram/crew/status', telegramAuth, (req, res) => {
  const code = (req.query.crew_code || '').toUpperCase();

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
  res.json({
    crew_code: code,
    exists: !!(crew || deviceCount > 0),
    devices_online: deviceCount,
    device_names: deviceNames,
    telegram_users: crew ? crew.telegram_users.size : 0
  });
});

// --- Spotify OAuth callback ---

app.get('/spotify/callback', async (req, res) => {
  const { code, state } = req.query;

  if (!code) {
    return res.status(400).send('No authorization code provided');
  }

  try {
    const tokenResponse = await fetch('https://accounts.spotify.com/api/token', {
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

    const tokens = await tokenResponse.json();

    const deviceId = state;
    const tokenPayload = {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
      expires_in: tokens.expires_in
    };

    const device = devices.get(deviceId);

    if (device && device.ws.readyState === WebSocket.OPEN) {
      // Device is online — send tokens immediately
      device.ws.send(JSON.stringify({
        type: 'spotify_auth_complete',
        tokens: tokenPayload
      }));
      console.log(`[SPOTIFY] Tokens sent to device ${deviceId}`);
    } else {
      // Device not connected yet (e.g. still in portal AP mode)
      // Store tokens for delivery when device connects
      pendingSpotifyTokens.set(deviceId, {
        tokens: tokenPayload,
        timestamp: Date.now()
      });
      console.log(`[SPOTIFY] Tokens stored for device ${deviceId} (pending delivery)`);
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
    console.error('Spotify OAuth error:', error);
    res.status(500).send('Failed to complete Spotify authorization');
  }
});

// --- WebSocket connection handling ---

wss.on('connection', (ws, req) => {
  console.log('New WebSocket connection from:', req.socket.remoteAddress);

  let deviceId = null;

  ws.on('message', (message) => {
    try {
      const data = JSON.parse(message);

      switch (data.type) {
        case 'register': {
          deviceId = data.device_id || generateDeviceId();
          const crewCode = (data.crew_code || '').toUpperCase() || null;

          devices.set(deviceId, {
            ws: ws,
            crew_code: crewCode,
            device_name: data.device_name || 'LEELOO',
            connected_at: Date.now()
          });

          // Ensure crew exists in our map if device has one
          if (crewCode) {
            getOrCreateCrew(crewCode);
          }

          ws.send(JSON.stringify({
            type: 'registered',
            device_id: deviceId
          }));

          // Deliver any pending Spotify tokens
          if (pendingSpotifyTokens.has(deviceId)) {
            const pending = pendingSpotifyTokens.get(deviceId);
            // Only deliver if less than 1 hour old (token expiry)
            if (Date.now() - pending.timestamp < 3600000) {
              ws.send(JSON.stringify({
                type: 'spotify_auth_complete',
                tokens: pending.tokens
              }));
              console.log(`[SPOTIFY] Delivered pending tokens to ${deviceId}`);
            }
            pendingSpotifyTokens.delete(deviceId);
          }

          console.log(`Device registered: ${deviceId} (${data.device_name}) crew=${crewCode}`);
          break;
        }

        case 'create_crew': {
          // Device creates a new crew (optionally with a pre-assigned code from portal setup)
          let crewCode = data.crew_code ? data.crew_code.toUpperCase() : generateCrewCode();
          while (!data.crew_code && crews.has(crewCode)) {
            crewCode = generateCrewCode();
          }

          getOrCreateCrew(crewCode);

          deviceId = data.device_id || deviceId || generateDeviceId();
          devices.set(deviceId, {
            ws: ws,
            crew_code: crewCode,
            device_name: data.display_name || data.device_name || 'LEELOO',
            connected_at: Date.now()
          });

          ws.send(JSON.stringify({
            type: 'crew_created',
            device_id: deviceId,
            crew_code: crewCode
          }));

          // Deliver any pending Spotify tokens
          if (pendingSpotifyTokens.has(deviceId)) {
            const pending = pendingSpotifyTokens.get(deviceId);
            if (Date.now() - pending.timestamp < 3600000) {
              ws.send(JSON.stringify({
                type: 'spotify_auth_complete',
                tokens: pending.tokens
              }));
              console.log(`[SPOTIFY] Delivered pending tokens to ${deviceId}`);
            }
            pendingSpotifyTokens.delete(deviceId);
          }

          console.log(`Device ${deviceId} created crew ${crewCode}`);
          break;
        }

        case 'join_crew': {
          const joinCode = (data.crew_code || '').toUpperCase();

          // Check crew exists (either in crews map or a device has it)
          let found = crews.has(joinCode);
          if (!found) {
            devices.forEach((d) => {
              if (d.crew_code === joinCode) found = true;
            });
          }

          if (!found) {
            ws.send(JSON.stringify({
              type: 'error',
              error: 'invalid_crew_code',
              message: `Crew ${joinCode} not found`
            }));
            break;
          }

          getOrCreateCrew(joinCode);

          deviceId = data.device_id || deviceId || generateDeviceId();
          devices.set(deviceId, {
            ws: ws,
            crew_code: joinCode,
            device_name: data.display_name || data.device_name || 'LEELOO',
            connected_at: Date.now()
          });

          // Count crew members
          let memberCount = 0;
          devices.forEach((d) => {
            if (d.crew_code === joinCode) memberCount++;
          });

          ws.send(JSON.stringify({
            type: 'crew_joined',
            device_id: deviceId,
            crew_code: joinCode,
            crew_members: memberCount
          }));

          // Deliver any pending Spotify tokens
          if (pendingSpotifyTokens.has(deviceId)) {
            const pending = pendingSpotifyTokens.get(deviceId);
            if (Date.now() - pending.timestamp < 3600000) {
              ws.send(JSON.stringify({
                type: 'spotify_auth_complete',
                tokens: pending.tokens
              }));
              console.log(`[SPOTIFY] Delivered pending tokens to ${deviceId}`);
            }
            pendingSpotifyTokens.delete(deviceId);
          }

          // Notify others
          broadcastToCrew(joinCode, {
            type: 'member_joined',
            device_id: deviceId,
            display_name: data.display_name || data.device_name || 'LEELOO'
          }, deviceId);

          console.log(`Device ${deviceId} joined crew ${joinCode} (${memberCount} members)`);
          break;
        }

        case 'message': {
          // Generic message relay (text, song_push, nudge, hang_propose, etc.)
          const msgSender = devices.get(deviceId);
          if (!msgSender || !msgSender.crew_code) break;

          const relayMsg = {
            type: 'message',
            from_device: deviceId,
            from_name: msgSender.device_name,
            msg_type: data.msg_type || 'text',
            payload: data.payload || {},
            timestamp: Date.now()
          };

          const count = broadcastToCrew(msgSender.crew_code, relayMsg, deviceId);
          console.log(`[MSG] ${msgSender.device_name} sent ${data.msg_type || 'text'} to ${count} devices`);
          break;
        }

        case 'share_music': {
          // Legacy music sharing
          const sender = devices.get(deviceId);
          if (!sender || !sender.crew_code) break;

          const sentCount = broadcastToCrew(sender.crew_code, {
            type: 'music_shared',
            spotify_uri: data.spotify_uri,
            artist: data.artist,
            track: data.track,
            album: data.album,
            pushed_by: sender.device_name,
            timestamp: Date.now()
          }, deviceId);

          console.log(`Music shared from ${sender.device_name} to ${sentCount} devices`);
          break;
        }

        case 'reaction': {
          // Legacy reaction
          const reactor = devices.get(deviceId);
          if (!reactor || !reactor.crew_code) break;

          broadcastToCrew(reactor.crew_code, {
            type: 'reaction',
            reaction_type: data.reaction_type,
            from: reactor.device_name,
            timestamp: Date.now()
          }, deviceId);
          break;
        }

        case 'ping':
          ws.send(JSON.stringify({ type: 'pong' }));
          break;

        default:
          console.log('Unknown message type:', data.type);
      }
    } catch (error) {
      console.error('Error handling message:', error);
    }
  });

  ws.on('close', () => {
    if (deviceId) {
      const device = devices.get(deviceId);
      if (device && device.crew_code) {
        broadcastToCrew(device.crew_code, {
          type: 'member_offline',
          device_id: deviceId,
          display_name: device.device_name
        }, deviceId);
      }
      devices.delete(deviceId);
      console.log(`Device disconnected: ${deviceId}`);
    }
  });

  ws.on('error', (error) => {
    console.error('WebSocket error:', error);
  });
});

// Start server
server.listen(PORT, () => {
  console.log(`LEELOO Relay Server running on port ${PORT}`);
  console.log(`WebSocket endpoint: ws://localhost:${PORT}`);
  console.log(`Telegram API: http://localhost:${PORT}/api/telegram/...`);
});

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('Shutting down gracefully...');
  wss.clients.forEach((client) => {
    client.close();
  });
  server.close(() => {
    process.exit(0);
  });
});
