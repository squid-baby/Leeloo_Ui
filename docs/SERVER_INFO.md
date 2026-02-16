# LEELOO Server Information

## DigitalOcean Droplet

**IP Address**: `138.197.75.152`
**Hostname**: `leeloo-relay`
**OS**: Ubuntu 24.04 LTS
**Size**: $6/month (1GB RAM, 25GB SSD, 1TB transfer)
**Region**: NYC3

## SSH Access

```bash
ssh root@138.197.75.152
```

Your SSH key is already configured (`~/.ssh/id_rsa.pub`).

## Domain

**Domain**: `leeloobot.xyz`
**Registrar**: Namecheap
**Cost**: $2.20/year

**DNS Configuration Needed:**
- A Record: `@` → `138.197.75.152`
- A Record: `www` → `138.197.75.152`
- CNAME: `relay` → `leeloobot.xyz`

## URLs (After Setup)

- **Landing page**: https://leeloobot.xyz
- **WebSocket relay**: wss://leeloobot.xyz/ws
- **Spotify OAuth callback**: https://leeloobot.xyz/spotify/callback
- **Health check**: https://leeloobot.xyz/health

## Quick Commands

```bash
# Connect to server
ssh root@138.197.75.152

# View relay logs
pm2 logs leeloo-relay

# Restart relay
pm2 restart leeloo-relay

# Check server status
pm2 status

# View Nginx logs
tail -f /var/log/nginx/error.log
```

## Files on Server

```
/root/leeloo-relay/
├── server.js              # Main relay server
├── package.json           # Dependencies
├── .env                   # Spotify credentials (TO ADD)
└── public/
    └── index.html         # Landing page
```

## Next Steps

1. **Configure DNS** (Namecheap)
   - Add A records pointing to 138.197.75.152
   - Wait 5-10 minutes for propagation

2. **Deploy Relay Server** (see RELAY_SERVER_SETUP.md)
   - Install Node.js, Nginx, Certbot
   - Create relay application
   - Configure SSL
   - Start with PM2

3. **Register Spotify App**
   - Go to developer.spotify.com/dashboard
   - Create app "LEELOO"
   - Add redirect URI: https://leeloobot.xyz/spotify/callback
   - Get Client ID and Secret
   - Update .env file

4. **Test Everything**
   - Visit https://leeloobot.xyz (landing page)
   - Test WebSocket connection
   - Test OAuth callback

## Spotify App Registration

**When ready to register:**
- App name: `LEELOO`
- App description: `Social music sharing device for collaborative listening`
- Website: `https://leeloobot.xyz`
- Redirect URI: `https://leeloobot.xyz/spotify/callback`
- APIs needed: Web API (for currently playing, playback control)

Store credentials in `/root/leeloo-relay/.env`:
```
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=https://leeloobot.xyz/spotify/callback
```

## Monitoring

**Server health:**
```bash
# CPU/Memory usage
htop

# Disk space
df -h

# Active connections
pm2 status

# WebSocket connections
curl https://leeloobot.xyz/health
```

## Costs

- **DigitalOcean**: $6/month
- **Domain**: $2.20/year
- **SSL**: Free (Let's Encrypt)

**Total: ~$6/month**

## Documentation

- Full setup guide: `RELAY_SERVER_SETUP.md`
- Bluetooth integration: `BLUETOOTH_PLAYBACK_INTEGRATION.md`
- Spotify testing: `SPOTIFY_SCANCODE_TEST.md`

## Support

If something breaks:
1. Check logs: `pm2 logs leeloo-relay`
2. Check Nginx: `systemctl status nginx`
3. Restart relay: `pm2 restart leeloo-relay`
4. Restart Nginx: `systemctl restart nginx`
