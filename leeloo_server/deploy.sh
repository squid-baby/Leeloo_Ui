#!/bin/bash
# LEELOO Relay Server + Telegram Bot Deployment Script

SERVER_IP="138.197.75.152"
SERVER_USER="root"
DOMAIN="leeloobot.xyz"

echo "LEELOO Relay Server Deployment"
echo "=================================="
echo ""

# Step 1: Install system packages
echo "Step 1: Installing system packages..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
apt update && apt upgrade -y
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs nginx certbot python3-certbot-nginx python3-pip python3-venv
npm install -g pm2
ENDSSH

echo ""
echo "System packages installed"
echo ""

# Step 2: Upload relay server files
echo "Step 2: Uploading relay server files..."
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p /root/leeloo-relay/public"
scp server.js ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp package.json ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp .env ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp public/index.html ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/public/

echo ""
echo "Relay server files uploaded"
echo ""

# Step 3: Upload Telegram bot
echo "Step 3: Uploading Telegram bot..."
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p /root/leeloo-telegram"
scp telegram_bot.py ${SERVER_USER}@${SERVER_IP}:/root/leeloo-telegram/

echo ""
echo "Telegram bot uploaded"
echo ""

# Step 4: Install dependencies
echo "Step 4: Installing dependencies..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cd /root/leeloo-relay
npm install

cd /root/leeloo-telegram
python3 -m venv venv 2>/dev/null || true
source venv/bin/activate
pip install python-telegram-bot aiohttp
ENDSSH

echo ""
echo "Dependencies installed"
echo ""

# Step 5: Configure Nginx
echo "Step 5: Configuring Nginx..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cat > /etc/nginx/sites-available/leeloobot.xyz << 'EOF'
# HTTP - redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name leeloobot.xyz www.leeloobot.xyz;

    # Allow Certbot to verify domain
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS (will be configured after SSL cert)
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name leeloobot.xyz www.leeloobot.xyz;

    # SSL certificates (placeholder, will be updated by Certbot)
    ssl_certificate /etc/ssl/certs/ssl-cert-snakeoil.pem;
    ssl_certificate_key /etc/ssl/private/ssl-cert-snakeoil.key;

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

    # Telegram API endpoints
    location /api/telegram/ {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
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
EOF

ln -sf /etc/nginx/sites-available/leeloobot.xyz /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
ENDSSH

echo ""
echo "Nginx configured"
echo ""

# Step 6: Get SSL certificate
echo "Step 6: Getting SSL certificate..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
certbot --nginx -d leeloobot.xyz -d www.leeloobot.xyz --non-interactive --agree-tos --register-unsafely-without-email
ENDSSH

echo ""
echo "SSL certificate obtained"
echo ""

# Step 7: Create Telegram bot systemd service
echo "Step 7: Setting up Telegram bot service..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cat > /etc/systemd/system/leeloo-telegram.service << 'EOF'
[Unit]
Description=LEELOO Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/leeloo-telegram
ExecStart=/root/leeloo-telegram/venv/bin/python3 telegram_bot.py
EnvironmentFile=/root/leeloo-relay/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable leeloo-telegram
ENDSSH

echo ""
echo "Telegram bot service created"
echo ""

# Step 8: Start relay server with PM2
echo "Step 8: Starting relay server..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cd /root/leeloo-relay
pm2 stop leeloo-relay 2>/dev/null || true
pm2 delete leeloo-relay 2>/dev/null || true
pm2 start server.js --name leeloo-relay
pm2 save
pm2 startup | tail -1 | bash
ENDSSH

echo ""
echo "Relay server started"
echo ""

# Step 9: Start Telegram bot
echo "Step 9: Starting Telegram bot..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
systemctl restart leeloo-telegram
ENDSSH

echo ""
echo "Telegram bot started"
echo ""

# Step 10: Configure firewall
echo "Step 10: Configuring firewall..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
ufw --force enable
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ENDSSH

echo ""
echo "Firewall configured"
echo ""

# Verification
echo "Verification:"
echo "=================================="
echo ""
echo "Server status:"
ssh ${SERVER_USER}@${SERVER_IP} "pm2 status && echo '' && systemctl status leeloo-telegram --no-pager -l | head -10"
echo ""
echo "Health check:"
sleep 3
curl -s https://leeloobot.xyz/health | python3 -m json.tool || echo "Waiting for DNS..."
echo ""
echo ""
echo "DEPLOYMENT COMPLETE!"
echo ""
echo "URLs:"
echo "   Landing page:  https://leeloobot.xyz"
echo "   WebSocket:     wss://leeloobot.xyz/ws"
echo "   Health check:  https://leeloobot.xyz/health"
echo "   Telegram API:  https://leeloobot.xyz/api/telegram/"
echo ""
echo "Required .env vars (add to /root/leeloo-relay/.env):"
echo "   LEELOO_BOT_TOKEN=<from @BotFather>"
echo "   RELAY_API_URL=http://localhost:3000"
echo "   TELEGRAM_API_SECRET=<generate with: openssl rand -hex 32>"
echo ""
echo "Quick commands:"
echo "   Restart relay:   ssh root@${SERVER_IP} 'pm2 restart leeloo-relay'"
echo "   Restart bot:     ssh root@${SERVER_IP} 'systemctl restart leeloo-telegram'"
echo "   Relay logs:      ssh root@${SERVER_IP} 'pm2 logs leeloo-relay'"
echo "   Bot logs:        ssh root@${SERVER_IP} 'journalctl -u leeloo-telegram -f'"
echo ""
