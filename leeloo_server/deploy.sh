#!/bin/bash
# LEELOO Relay Server Deployment Script

SERVER_IP="138.197.75.152"
SERVER_USER="root"
DOMAIN="leeloobot.xyz"

echo "🚀 LEELOO Relay Server Deployment"
echo "=================================="
echo ""

# Step 1: Install system packages
echo "📦 Step 1: Installing system packages..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
apt update && apt upgrade -y
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs nginx certbot python3-certbot-nginx
npm install -g pm2
ENDSSH

echo ""
echo "✅ System packages installed"
echo ""

# Step 2: Upload relay server files
echo "📤 Step 2: Uploading relay server files..."
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p /root/leeloo-relay/public"
scp server.js ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp package.json ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp .env ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/
scp public/index.html ${SERVER_USER}@${SERVER_IP}:/root/leeloo-relay/public/

echo ""
echo "✅ Files uploaded"
echo ""

# Step 3: Install Node dependencies
echo "📦 Step 3: Installing Node.js dependencies..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cd /root/leeloo-relay
npm install
ENDSSH

echo ""
echo "✅ Dependencies installed"
echo ""

# Step 4: Configure Nginx
echo "⚙️  Step 4: Configuring Nginx..."
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
echo "✅ Nginx configured"
echo ""

# Step 5: Get SSL certificate
echo "🔒 Step 5: Getting SSL certificate..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
certbot --nginx -d leeloobot.xyz -d www.leeloobot.xyz --non-interactive --agree-tos --register-unsafely-without-email
ENDSSH

echo ""
echo "✅ SSL certificate obtained"
echo ""

# Step 6: Start relay server with PM2
echo "🚀 Step 6: Starting relay server..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
cd /root/leeloo-relay
pm2 stop leeloo-relay 2>/dev/null || true
pm2 delete leeloo-relay 2>/dev/null || true
pm2 start server.js --name leeloo-relay
pm2 save
pm2 startup | tail -1 | bash
ENDSSH

echo ""
echo "✅ Relay server started"
echo ""

# Step 7: Configure firewall
echo "🔥 Step 7: Configuring firewall..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
ufw --force enable
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ENDSSH

echo ""
echo "✅ Firewall configured"
echo ""

# Verification
echo "🧪 Verification:"
echo "=================================="
echo ""
echo "Server status:"
ssh ${SERVER_USER}@${SERVER_IP} "pm2 status"
echo ""
echo "Health check:"
sleep 3
curl -s https://leeloobot.xyz/health | python3 -m json.tool || echo "Waiting for DNS..."
echo ""
echo ""
echo "✅ DEPLOYMENT COMPLETE!"
echo ""
echo "🌐 URLs:"
echo "   Landing page: https://leeloobot.xyz"
echo "   WebSocket:    wss://leeloobot.xyz/ws"
echo "   Health check: https://leeloobot.xyz/health"
echo ""
echo "📝 Next steps:"
echo "   1. Register Spotify app at https://developer.spotify.com/dashboard"
echo "   2. Update /root/leeloo-relay/.env with Spotify credentials"
echo "   3. Restart: ssh root@138.197.75.152 'pm2 restart leeloo-relay'"
echo ""
