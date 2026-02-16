# Plan: LEELOO Relay Server Setup on DigitalOcean

## Context

User needs a backend relay server to support LEELOO devices. The relay server will:
1. **WebSocket connections** - Allow LEELOO devices to connect and communicate (share music, send reactions)
2. **Spotify OAuth callback** - Handle Spotify authorization flow and send tokens back to devices
3. **Domain setup** - Configure `leeloobot.xyz` to point to the server with SSL

The server is a fresh Ubuntu 24.04 droplet at `138.197.75.152`.

---

## Implementation Plan

### Phase 1: Server Initial Setup (5 minutes)

**Install required packages:**
```bash
# Update system
apt update && apt upgrade -y

# Install Node.js 20 (for relay server)
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

# Install Nginx (for reverse proxy + SSL)
apt install -y nginx

# Install Certbot (for Let's Encrypt SSL)
apt install -y certbot python3-certbot-nginx

# Install PM2 (process manager for Node.js)
npm install -g pm2
```

---

### Phase 2: Relay Server Application (Node.js + WebSockets)

**Tech Stack:**
- **Express.js** - HTTP server for OAuth callback
- **ws** - WebSocket library for device connections
- **uuid** - Generate unique device/crew IDs

**Project Structure:**
```
/root/leeloo-relay/
├── server.js              # Main server file
├── package.json           # Dependencies
├── .env                   # Environment variables (Spotify credentials)
├── devices.json           # Connected devices (persisted)
└── public/
    └── index.html         # Simple landing page
```

**server.js** (Core relay logic):
```javascript
const express = require('express');
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');
const path = require('path');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Configuration
const PORT = process.env.PORT || 3000;
const SPOTIFY_CLIENT_ID = process.env.SPOTIFY_CLIENT_ID;
const SPOTIFY_CLIENT_SECRET = process.env.SPOTIFY_CLIENT_SECRET;
const SPOTIFY_REDIRECT_URI = process.env.SPOTIFY_REDIRECT_URI;

// In-memory storage (will persist to JSON)
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
    const deviceId = state; // state contains device_id from OAuth init
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
  console.log('New WebSocket connection');

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

          console.log(`Device registered: ${deviceId}`);
          break;

        case 'share_music':
          // Music sharing between devices in same crew
          const sender = devices.get(deviceId);
          if (!sender || !sender.crew_code) {
            break;
          }

          // Send to all devices in same crew
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
            }
          });
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
                reaction_type: data.reaction_type, // 'love', 'fire', 'haha', 'wave'
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
```

**package.json:**
```json
{
  "name": "leeloo-relay",
  "version": "1.0.0",
  "description": "LEELOO WebSocket relay server",
  "main": "server.js",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "express": "^4.18.2",
    "ws": "^8.14.2",
    "dotenv": "^16.3.1"
  }
}
```

**.env:**
```bash
PORT=3000
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=https://leeloobot.xyz/spotify/callback
```

**public/index.html** (Landing page):
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LEELOO - Social Music Sharing Device</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: linear-gradient(135deg, #1A1D2E 0%, #2A2D3E 100%);
      color: #fff;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .container {
      max-width: 600px;
      text-align: center;
    }
    h1 {
      font-size: 3rem;
      margin-bottom: 1rem;
      background: linear-gradient(45deg, #9C93DD, #D6697F);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    p {
      font-size: 1.2rem;
      line-height: 1.6;
      color: #A7AFD4;
      margin-bottom: 2rem;
    }
    .feature {
      background: rgba(255,255,255,0.05);
      padding: 1rem;
      margin: 1rem 0;
      border-radius: 8px;
      border-left: 4px solid #719253;
    }
    .feature h3 {
      color: #719253;
      margin-bottom: 0.5rem;
    }
    footer {
      margin-top: 3rem;
      color: #666;
      font-size: 0.9rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>LEELOO</h1>
    <p>Social music sharing device for collaborative listening</p>

    <div class="feature">
      <h3>🎵 Share Music</h3>
      <p>Send Spotify tracks to your crew with scannable codes</p>
    </div>

    <div class="feature">
      <h3>📱 Retro Display</h3>
      <p>Terminal-style UI with album art and real-time updates</p>
    </div>

    <div class="feature">
      <h3>🔊 Bluetooth Playback</h3>
      <p>Touch to play shared tracks through your speaker</p>
    </div>

    <footer>
      Made with ♪ by squid-baby
    </footer>
  </div>
</body>
</html>
```

---

### Phase 3: Nginx Reverse Proxy Configuration

**Create Nginx config** at `/etc/nginx/sites-available/leeloobot.xyz`:

```nginx
# HTTP - redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name leeloobot.xyz www.leeloobot.xyz;

    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name leeloobot.xyz www.leeloobot.xyz;

    # SSL certificates (will be added by Certbot)
    ssl_certificate /etc/letsencrypt/live/leeloobot.xyz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/leeloobot.xyz/privkey.pem;

    # Static files (landing page)
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # WebSocket endpoint
    location /ws {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeouts
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Spotify OAuth callback
    location /spotify/callback {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Health check
    location /health {
        proxy_pass http://localhost:3000;
        access_log off;
    }
}
```

**Enable site:**
```bash
ln -s /etc/nginx/sites-available/leeloobot.xyz /etc/nginx/sites-enabled/
nginx -t  # Test config
systemctl reload nginx
```

---

### Phase 4: SSL Certificate Setup

**Get SSL certificate from Let's Encrypt:**
```bash
# Stop nginx temporarily
systemctl stop nginx

# Get certificate for domain
certbot certonly --standalone -d leeloobot.xyz -d www.leeloobot.xyz

# Start nginx
systemctl start nginx

# Auto-renewal (already set up by certbot)
certbot renew --dry-run  # Test renewal
```

---

### Phase 5: DNS Configuration (Namecheap)

**Add these DNS records in Namecheap Advanced DNS:**

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A Record | @ | `138.197.75.152` | Automatic |
| A Record | www | `138.197.75.152` | Automatic |
| CNAME | relay | `leeloobot.xyz` | Automatic |

**Wait 5-10 minutes** for DNS propagation.

---

### Phase 6: PM2 Process Management

**Start relay server with PM2:**
```bash
cd /root/leeloo-relay
pm2 start server.js --name leeloo-relay
pm2 save  # Save process list
pm2 startup  # Enable startup on boot
```

**PM2 commands:**
```bash
pm2 status           # Check status
pm2 logs leeloo-relay  # View logs
pm2 restart leeloo-relay  # Restart server
pm2 stop leeloo-relay  # Stop server
```

---

## Deployment Steps (Execution Order)

### 1. Server Setup
```bash
ssh root@138.197.75.152

# Install packages
apt update && apt upgrade -y
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs nginx certbot python3-certbot-nginx
npm install -g pm2
```

### 2. Create Relay Server
```bash
mkdir -p /root/leeloo-relay/public
cd /root/leeloo-relay

# Create files (server.js, package.json, .env, public/index.html)
# (I'll provide commands to create these)

npm install
```

### 3. Configure Nginx
```bash
# Create nginx config
nano /etc/nginx/sites-available/leeloobot.xyz
# (paste config from Phase 3)

ln -s /etc/nginx/sites-available/leeloobot.xyz /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 4. Get SSL Certificate
```bash
systemctl stop nginx
certbot certonly --standalone -d leeloobot.xyz -d www.leeloobot.xyz
systemctl start nginx
```

### 5. Configure DNS
- Log into Namecheap
- Add A records pointing to 138.197.75.152
- Wait 5-10 minutes

### 6. Start Relay Server
```bash
cd /root/leeloo-relay
pm2 start server.js --name leeloo-relay
pm2 save
pm2 startup
```

---

## Verification Steps

### 1. Test Landing Page
```bash
curl https://leeloobot.xyz
# Should return HTML landing page
```

### 2. Test WebSocket Connection
```bash
# From local machine
npm install -g wscat
wscat -c wss://leeloobot.xyz/ws

# Send test message:
{"type":"register","device_id":"test123","device_name":"TestDevice"}
```

### 3. Test Spotify OAuth
- Visit: `https://leeloobot.xyz/spotify/callback?code=test&state=device123`
- Should show success page (even without valid code, proves endpoint works)

### 4. Test Health Check
```bash
curl https://leeloobot.xyz/health
# Should return: {"status":"ok","devices":0,"uptime":123}
```

---

## Environment Variables Needed

After deployment, you'll need to update `.env` with real Spotify credentials:

1. Go to https://developer.spotify.com/dashboard
2. Create app "LEELOO"
3. Get Client ID and Client Secret
4. Update `/root/leeloo-relay/.env`
5. Restart: `pm2 restart leeloo-relay`

---

## Post-Deployment

### Firewall Setup
```bash
# Allow HTTP, HTTPS, SSH
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw enable
```

### Monitoring
```bash
# View logs
pm2 logs leeloo-relay

# Monitor resources
htop

# Check Nginx logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

---

## Integration with LEELOO Device

**Device connection code** (to add to LEELOO):
```python
import asyncio
import websockets
import json

async def connect_to_relay():
    uri = "wss://leeloobot.xyz/ws"

    async with websockets.connect(uri) as websocket:
        # Register device
        await websocket.send(json.dumps({
            "type": "register",
            "device_id": "leeloo_001",
            "crew_code": "alpha",
            "device_name": "LEELOO-Living-Room"
        }))

        # Listen for messages
        async for message in websocket:
            data = json.loads(message)

            if data['type'] == 'music_shared':
                # Update display with shared track
                print(f"New track: {data['track']} by {data['artist']}")
                # Call update_display(data)

            elif data['type'] == 'spotify_auth_complete':
                # Save Spotify tokens
                save_tokens(data['tokens'])

# Run in background
asyncio.run(connect_to_relay())
```

---

## Critical Files Summary

### On Server (`138.197.75.152`):
1. `/root/leeloo-relay/server.js` - Main relay server
2. `/root/leeloo-relay/package.json` - Dependencies
3. `/root/leeloo-relay/.env` - Spotify credentials
4. `/root/leeloo-relay/public/index.html` - Landing page
5. `/etc/nginx/sites-available/leeloobot.xyz` - Nginx config
6. `/etc/letsencrypt/live/leeloobot.xyz/` - SSL certificates

### DNS (Namecheap):
- `leeloobot.xyz` → `138.197.75.152`
- `www.leeloobot.xyz` → `138.197.75.152`

---

## Estimated Timeline

- Server setup: 5 minutes
- Relay server creation: 10 minutes
- Nginx + SSL: 5 minutes
- DNS configuration: 2 minutes (+ 5-10 min propagation)
- Testing: 5 minutes

**Total: ~30 minutes** (plus DNS wait time)

---

## Next Steps After Relay is Live

1. Register Spotify app and add credentials
2. Update LEELOO device code to connect to relay
3. Test music sharing between two LEELOO devices
4. Add device registration/authentication (optional)
5. Add analytics/monitoring (optional)
