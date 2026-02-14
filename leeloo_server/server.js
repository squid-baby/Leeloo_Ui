const express = require('express');
const WebSocket = require('ws');
const http = require('http');
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

// In-memory storage
const devices = new Map(); // device_id -> { ws, crew_code, device_name }

// Serve static landing page
app.use(express.static('public'));
app.use(express.json());

// Health check
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    devices: devices.size,
    uptime: process.uptime()
  });
});

// Spotify OAuth callback
app.get('/spotify/callback', async (req, res) => {
  const { code, state } = req.query;

  if (!code) {
    return res.status(400).send('No authorization code provided');
  }

  try {
    // Exchange code for tokens
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

    // Send tokens to device via WebSocket (using state as device_id)
    const deviceId = state;
    const device = devices.get(deviceId);

    if (device && device.ws.readyState === WebSocket.OPEN) {
      device.ws.send(JSON.stringify({
        type: 'spotify_auth_complete',
        tokens: {
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
          expires_in: tokens.expires_in
        }
      }));
    }

    // Show success page
    res.send(`
      <html>
        <head><title>LEELOO - Spotify Connected</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
          <h1>✅ Spotify Connected!</h1>
          <p>You can close this window and return to your LEELOO device.</p>
        </body>
      </html>
    `);
  } catch (error) {
    console.error('Spotify OAuth error:', error);
    res.status(500).send('Failed to complete Spotify authorization');
  }
});

// WebSocket connection handling
wss.on('connection', (ws, req) => {
  console.log('New WebSocket connection from:', req.socket.remoteAddress);

  let deviceId = null;

  ws.on('message', (message) => {
    try {
      const data = JSON.parse(message);

      switch (data.type) {
        case 'register':
          // Device registration
          deviceId = data.device_id || generateDeviceId();
          devices.set(deviceId, {
            ws: ws,
            crew_code: data.crew_code || null,
            device_name: data.device_name || 'LEELOO',
            connected_at: Date.now()
          });

          ws.send(JSON.stringify({
            type: 'registered',
            device_id: deviceId
          }));

          console.log(`Device registered: ${deviceId} (${data.device_name})`);
          break;

        case 'share_music':
          // Music sharing between devices in same crew
          const sender = devices.get(deviceId);
          if (!sender || !sender.crew_code) {
            break;
          }

          // Send to all devices in same crew
          let sentCount = 0;
          devices.forEach((device, id) => {
            if (id !== deviceId &&
                device.crew_code === sender.crew_code &&
                device.ws.readyState === WebSocket.OPEN) {

              device.ws.send(JSON.stringify({
                type: 'music_shared',
                spotify_uri: data.spotify_uri,
                artist: data.artist,
                track: data.track,
                album: data.album,
                pushed_by: sender.device_name,
                timestamp: Date.now()
              }));
              sentCount++;
            }
          });
          console.log(`Music shared from ${sender.device_name} to ${sentCount} devices`);
          break;

        case 'reaction':
          // Send reaction to crew
          const reactor = devices.get(deviceId);
          if (!reactor || !reactor.crew_code) {
            break;
          }

          devices.forEach((device, id) => {
            if (id !== deviceId &&
                device.crew_code === reactor.crew_code &&
                device.ws.readyState === WebSocket.OPEN) {

              device.ws.send(JSON.stringify({
                type: 'reaction',
                reaction_type: data.reaction_type,
                from: reactor.device_name,
                timestamp: Date.now()
              }));
            }
          });
          break;

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
      devices.delete(deviceId);
      console.log(`Device disconnected: ${deviceId}`);
    }
  });

  ws.on('error', (error) => {
    console.error('WebSocket error:', error);
  });
});

// Helper: Generate unique device ID
function generateDeviceId() {
  return 'leeloo_' + Math.random().toString(36).substring(2, 15);
}

// Start server
server.listen(PORT, () => {
  console.log(`LEELOO Relay Server running on port ${PORT}`);
  console.log(`WebSocket endpoint: ws://localhost:${PORT}`);
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
