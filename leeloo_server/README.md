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
