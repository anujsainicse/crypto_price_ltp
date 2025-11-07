# VPS Deployment Guide - Crypto Price LTP

Complete guide for deploying the Crypto Price LTP collector on a VPS server using Docker Compose.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [VPS Setup](#vps-setup)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Deployment](#deployment)
7. [Bot Integration](#bot-integration)
8. [Monitoring & Maintenance](#monitoring--maintenance)
9. [Troubleshooting](#troubleshooting)
10. [Security Best Practices](#security-best-practices)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                        VPS Server                        │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │          Docker Compose Network                     │ │
│  │                                                     │ │
│  │  ┌──────────────┐         ┌──────────────────┐   │ │
│  │  │    Redis     │◄────────┤  Price Collector │   │ │
│  │  │   Container  │         │    Container     │   │ │
│  │  │ Port: 6379   │         │  Port: 8080      │   │ │
│  │  └──────┬───────┘         └──────────────────┘   │ │
│  │         │                                          │ │
│  └─────────┼──────────────────────────────────────────┘ │
│            │                                             │
│            │ localhost:6379 (password-protected)        │
│            │                                             │
│  ┌─────────▼─────────┐                                  │
│  │  Your Bot App     │                                  │
│  │  (connects via    │                                  │
│  │   localhost)      │                                  │
│  └───────────────────┘                                  │
└─────────────────────────────────────────────────────────┘
```

**Key Points:**
- Redis and Price Collector run in Docker containers
- Both services bound to `localhost` only (secure)
- Your bot application connects to Redis on `localhost:6379`
- Dashboard accessible at `http://localhost:8080` (SSH tunnel for remote access)

---

## Prerequisites

### VPS Requirements

**Minimum Specifications:**
- **CPU:** 2 cores
- **RAM:** 2GB
- **Storage:** 20GB SSD
- **OS:** Ubuntu 22.04 LTS (recommended) or Ubuntu 20.04
- **Network:** 5 Mbps bandwidth

**Recommended Specifications:**
- **CPU:** 2-4 cores
- **RAM:** 4GB
- **Storage:** 40GB SSD
- **Network:** 10 Mbps bandwidth

**Popular VPS Providers:**
- **Hetzner:** CPX11 (~€5/month) - Best value
- **DigitalOcean:** Basic Droplet ($12/month)
- **Linode:** Nanode 2GB ($12/month)
- **Vultr:** Regular Performance ($12/month)

### Software Requirements

- Ubuntu 22.04 LTS or similar
- Docker 20.10+
- Docker Compose 2.0+
- SSH access with key-based authentication
- Basic familiarity with Linux command line

---

## VPS Setup

### 1. Initial Server Configuration

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install essential tools
sudo apt install -y curl git vim wget ufw

# Set timezone (optional)
sudo timedatectl set-timezone Asia/Kolkata  # Change to your timezone
```

### 2. Create Non-Root User (Optional but Recommended)

```bash
# Create user
sudo adduser deploy

# Add to sudo group
sudo usermod -aG sudo deploy

# Switch to new user
su - deploy
```

### 3. Configure SSH Access

```bash
# Copy SSH keys (if using non-root user)
sudo mkdir -p /home/deploy/.ssh
sudo cp /root/.ssh/authorized_keys /home/deploy/.ssh/
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

### 4. Configure Firewall

```bash
# Allow SSH
sudo ufw allow 22/tcp

# Deny direct access to Redis and Dashboard (accessed via localhost only)
sudo ufw deny 6379/tcp
sudo ufw deny 8080/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status
```

### 5. Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add current user to docker group
sudo usermod -aG docker $USER

# Apply group changes (or log out and back in)
newgrp docker

# Verify installation
docker --version
docker compose version
```

**Expected Output:**
```
Docker version 24.0.x, build xxx
Docker Compose version v2.x.x
```

---

## Installation

### 1. Clone Repository

```bash
# Navigate to home directory
cd ~

# Clone the repository
git clone https://github.com/yourusername/crypto_price_ltp.git

# Change to project directory
cd crypto_price_ltp
```

### 2. Set Up Directory Structure

```bash
# Create logs directory
mkdir -p logs

# Set permissions
chmod 755 logs
```

---

## Configuration

### 1. Generate Secure Redis Password

```bash
# Generate a strong password
openssl rand -base64 32
```

**Example output:** `k7wX9mP4nL2qR8tY5vZ1aB6cD3eF0gH4iJ7kL9mN2oP5=`

**Important:** Save this password securely - you'll need it for:
- `.env.production` configuration
- Bot application Redis connection

### 2. Configure Production Environment

```bash
# Copy production template
cp .env.production .env.production.local

# Edit with your secure password
nano .env.production.local
```

Update the `REDIS_PASSWORD` line:
```env
REDIS_PASSWORD=k7wX9mP4nL2qR8tY5vZ1aB6cD3eF0gH4iJ7kL9mN2oP5=
```

**Save and exit:** Press `Ctrl+X`, then `Y`, then `Enter`

### 3. Secure the Configuration File

```bash
# Set restrictive permissions
chmod 600 .env.production.local

# Verify permissions
ls -la .env.production.local
# Should show: -rw------- (owner read/write only)
```

### 4. Configure Exchange Services (Optional)

Edit `config/exchanges.yaml` to enable/disable services:

```bash
nano config/exchanges.yaml
```

**Default auto-start services:**
- ✅ Bybit Spot Service
- ✅ CoinDCX Futures LTP Service
- ✅ CoinDCX Funding Rate Service
- ❌ Delta Futures Service (manual start)
- ❌ Delta Options Service (manual start)

---

## Deployment

### 1. Test Configuration (Optional)

```bash
# Validate docker-compose file
docker compose --env-file .env.production.local config

# This should display the configuration without errors
```

### 2. Pull Docker Images

```bash
# Pull Redis image
docker compose --env-file .env.production.local pull redis

# This downloads the Redis 7 Alpine image (~30MB)
```

### 3. Build Application Image

```bash
# Build the price collector image
docker compose --env-file .env.production.local build

# This may take 2-5 minutes on first build
```

### 4. Start Services

```bash
# Start in detached mode (background)
docker compose --env-file .env.production.local up -d

# View startup logs
docker compose --env-file .env.production.local logs -f
```

**Expected Output:**
```
[+] Running 3/3
 ✔ Network crypto_price_ltp_crypto_network    Created
 ✔ Container crypto_ltp_redis                 Started
 ✔ Container crypto_ltp_app                   Started
```

### 5. Verify Deployment

```bash
# Check container status
docker ps

# Should show 2 containers running:
# - crypto_ltp_redis
# - crypto_ltp_app

# Check Redis connection
docker exec crypto_ltp_redis redis-cli -a "YOUR_PASSWORD" ping
# Should return: PONG

# Check application health
curl http://localhost:8080/api/health
# Should return: {"status":"healthy",...}

# Check service status
curl http://localhost:8080/api/status | jq
```

### 6. Monitor Initial Startup

```bash
# Watch logs for 2-3 minutes to ensure services start
docker compose --env-file .env.production.local logs -f

# Look for messages like:
# - "Service Manager started"
# - "Starting auto-enabled services..."
# - "Connected to exchange WebSocket"
# - "Price update received"
```

**Press Ctrl+C to stop viewing logs (containers keep running)**

---

## Bot Integration

### Redis Connection Configuration

Your bot application should connect to Redis using these settings:

**Connection Parameters:**
```
Host: localhost (or 127.0.0.1)
Port: 6379
Password: <your-redis-password-from-.env.production.local>
Database: 0
```

**Connection String Format:**
```
redis://:YOUR_PASSWORD@localhost:6379/0
```

### Python Example

```python
import redis

# Initialize Redis connection
redis_client = redis.Redis(
    host='localhost',
    port=6379,
    password='k7wX9mP4nL2qR8tY5vZ1aB6cD3eF0gH4iJ7kL9mN2oP5=',  # Your password
    db=0,
    decode_responses=True
)

# Test connection
try:
    redis_client.ping()
    print("✓ Connected to Redis")
except Exception as e:
    print(f"✗ Redis connection failed: {e}")

# Read BTC spot price from Bybit
btc_data = redis_client.hgetall('bybit_spot:BTCUSDT')
if btc_data:
    print(f"BTC Price: ${btc_data.get('ltp')}")
    print(f"24h Volume: {btc_data.get('volume_24h')}")
    print(f"Last Update: {btc_data.get('timestamp')}")
else:
    print("No data available yet")
```

### Available Data Keys

**Bybit Spot Prices:**
```
bybit_spot:BTCUSDT
bybit_spot:ETHUSDT
bybit_spot:SOLUSDT
bybit_spot:BNBUSDT
bybit_spot:DOGEUSDT
```

**CoinDCX Futures (includes funding rates):**
```
coindcx_futures:B-BTC_USDT
coindcx_futures:B-ETH_USDT
coindcx_futures:B-SOL_USDT
coindcx_futures:B-BNB_USDT
coindcx_futures:B-DOGE_USDT
```

**Delta Exchange Futures:**
```
delta_futures:BTCUSD
delta_futures:ETHUSD
delta_futures:SOLUSD
delta_futures:BNBUSD
delta_futures:DOGEUSD
```

**Delta Options (if enabled):**
```
delta_options:C-BTC-108200-211025  # Call option
delta_options:P-BTC-108200-211025  # Put option
```

### Data Structure

Each key contains a Redis hash with fields like:

```python
{
    'ltp': '106881.2',                    # Last traded price
    'timestamp': '2025-11-07T12:00:00Z',  # Update timestamp
    'original_symbol': 'BTCUSDT',         # Original symbol name
    'volume_24h': '12345.67',             # 24h volume
    'high_24h': '107000.00',              # 24h high
    'low_24h': '105500.00',               # 24h low
    'price_change_percent': '0.0234',     # Price change %
    # Additional fields for futures:
    'current_funding_rate': '-0.00003681',
    'estimated_funding_rate': '-0.00003468',
    # Additional fields for options:
    'delta': '0.332',
    'gamma': '0.00012',
    'vega': '45.23',
    'theta': '-12.45',
    'implied_volatility': '0.65'
}
```

### Bot Configuration Example

**In your bot's `.env` file:**
```env
# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=k7wX9mP4nL2qR8tY5vZ1aB6cD3eF0gH4iJ7kL9mN2oP5=
REDIS_DB=0
```

---

## Monitoring & Maintenance

### Accessing the Dashboard

The dashboard runs on `localhost:8080` and is not exposed to the internet for security.

**Option 1: SSH Tunnel (Recommended)**

```bash
# From your local machine
ssh -L 8080:localhost:8080 user@your-vps-ip

# Then open in browser: http://localhost:8080
```

**Option 2: Direct Access from VPS**

```bash
# SSH into VPS
ssh user@your-vps-ip

# Use curl to check status
curl http://localhost:8080/api/status | jq

# Or use text-based browser
sudo apt install lynx
lynx http://localhost:8080
```

### Health Check Script

Use the included health check script:

```bash
# Make executable
chmod +x health_check.sh

# Run check
./health_check.sh
```

**Example Output:**
```
=== Crypto Price LTP Health Check ===

1. Docker Containers:
NAMES              STATUS              PORTS
crypto_ltp_redis   Up 2 hours          127.0.0.1:6379->6379/tcp
crypto_ltp_app     Up 2 hours          127.0.0.1:8080->8080/tcp

2. Redis Connection:
✓ Redis: Connected

3. Dashboard API:
✓ Dashboard: Healthy

4. Data Collection:
Total data keys: 10
  - bybit_spot: 5 symbols
  - coindcx_futures: 5 symbols
  - delta_futures: 0 symbols
  - delta_options: 0 symbols

5. Disk Usage:
250M    logs/

6. Resource Usage:
CONTAINER          CPU %    MEM USAGE / LIMIT    MEM %
crypto_ltp_redis   0.15%    45MiB / 512MiB      8.8%
crypto_ltp_app     2.34%    312MiB / 1GiB       30.5%
```

### Common Commands

**View Logs:**
```bash
# All logs (follow mode)
docker compose logs -f

# Specific container
docker compose logs -f app
docker compose logs -f redis

# Last 100 lines
docker compose logs --tail 100

# Application logs (from mounted volume)
tail -f logs/service_manager.log
tail -f logs/bybit-spot.log
```

**Check Status:**
```bash
# Container status
docker ps

# Resource usage
docker stats

# Service status via API
curl http://localhost:8080/api/status | jq
```

**Control Services:**
```bash
# Start individual service via API
curl -X POST http://localhost:8080/api/service/delta-futures-ltp/start

# Stop individual service
curl -X POST http://localhost:8080/api/service/bybit-spot/stop

# Or use the dashboard at http://localhost:8080
```

**Restart Containers:**
```bash
# Restart all
docker compose restart

# Restart specific container
docker compose restart app
docker compose restart redis
```

**Stop/Start System:**
```bash
# Stop all containers
docker compose --env-file .env.production.local down

# Start again
docker compose --env-file .env.production.local up -d
```

**Update Application:**
```bash
# Pull latest code
cd ~/crypto_price_ltp
git pull origin main

# Rebuild and restart
docker compose --env-file .env.production.local down
docker compose --env-file .env.production.local build --no-cache
docker compose --env-file .env.production.local up -d

# Verify
docker compose logs -f
```

### Automated Monitoring

**Create systemd service for auto-start on reboot:**

```bash
# Create service file
sudo nano /etc/systemd/system/crypto-ltp.service
```

**Content:**
```ini
[Unit]
Description=Crypto Price LTP Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/deploy/crypto_price_ltp
ExecStart=/usr/bin/docker compose --env-file .env.production.local up -d
ExecStop=/usr/bin/docker compose --env-file .env.production.local down
User=deploy
Group=deploy

[Install]
WantedBy=multi-user.target
```

**Enable service:**
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start
sudo systemctl enable crypto-ltp.service

# Start service
sudo systemctl start crypto-ltp.service

# Check status
sudo systemctl status crypto-ltp.service
```

### Log Management

**Docker manages log rotation automatically** with the configured settings:
- Redis logs: Max 10MB per file, 3 files
- App logs: Max 50MB per file, 5 files

**Manual cleanup if needed:**
```bash
# Clear old application logs (7+ days old)
find logs/ -name "*.log.*" -mtime +7 -delete

# Check current log sizes
du -sh logs/*
```

---

## Troubleshooting

### Services Not Starting

**Issue:** Containers exit immediately after starting

**Solution:**
```bash
# Check logs for errors
docker compose logs

# Common issues:
# 1. Redis password mismatch
#    - Verify REDIS_PASSWORD in .env.production.local
#    - Ensure no special characters need escaping

# 2. Port already in use
#    - Check: sudo lsof -i :6379
#    - Check: sudo lsof -i :8080
#    - Stop conflicting services

# 3. Permission issues
#    - Fix: sudo chown -R $USER:$USER logs/
#    - Fix: chmod 755 logs/
```

### Redis Connection Failed

**Issue:** Bot can't connect to Redis

**Symptoms:**
```
redis.exceptions.AuthenticationError: invalid password
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```

**Solution:**
```bash
# 1. Verify Redis is running
docker ps | grep redis

# 2. Test Redis connection with correct password
docker exec crypto_ltp_redis redis-cli -a "YOUR_PASSWORD" ping

# 3. Verify port binding
docker ps | grep 6379
# Should show: 127.0.0.1:6379->6379/tcp

# 4. Check if bot is using correct password
# Update bot's Redis password to match .env.production.local
```

### No Data in Redis

**Issue:** Bot queries return empty results

**Solution:**
```bash
# 1. Check if services are running
curl http://localhost:8080/api/status | jq '.services[].status'

# 2. Check Redis keys
docker exec crypto_ltp_redis redis-cli -a "YOUR_PASSWORD" KEYS "*"

# 3. If empty, check service logs
docker compose logs app | grep -i error

# 4. Manually start services via dashboard
curl -X POST http://localhost:8080/api/service/bybit-spot/start

# 5. Wait 30 seconds and check again
docker exec crypto_ltp_redis redis-cli -a "YOUR_PASSWORD" KEYS "bybit_spot:*"
```

### High Memory Usage

**Issue:** Container using too much RAM

**Solution:**
```bash
# 1. Check current usage
docker stats --no-stream

# 2. If exceeding limits, adjust docker-compose.yml:
#    - Increase memory limits
#    - Or reduce REDIS_TTL to store less data

# 3. Restart with new limits
docker compose --env-file .env.production.local down
docker compose --env-file .env.production.local up -d
```

### Disk Space Full

**Issue:** Logs consuming too much disk space

**Solution:**
```bash
# 1. Check disk usage
df -h
du -sh logs/

# 2. Clear old logs
find logs/ -name "*.log.*" -mtime +3 -delete

# 3. Rotate current logs
docker compose restart

# 4. Verify log rotation is working
docker inspect crypto_ltp_app | grep -A 10 LogConfig
```

### Dashboard Not Accessible

**Issue:** Cannot access http://localhost:8080

**Solution:**
```bash
# 1. Check if container is running
docker ps | grep crypto_ltp_app

# 2. Check if port is bound correctly
docker port crypto_ltp_app
# Should show: 8080/tcp -> 127.0.0.1:8080

# 3. Test from VPS directly
curl http://localhost:8080/api/health

# 4. For remote access, use SSH tunnel:
ssh -L 8080:localhost:8080 user@vps-ip
# Then access: http://localhost:8080 from your local browser
```

---

## Security Best Practices

### 1. Password Management

- ✅ Use strong passwords (32+ characters)
- ✅ Generate with: `openssl rand -base64 32`
- ✅ Never commit `.env.production.local` to git
- ✅ Store backup copy in secure location (password manager)
- ⚠️ Change password if suspected compromise

### 2. File Permissions

```bash
# Secure environment files
chmod 600 .env.production.local

# Secure logs directory
chmod 755 logs/
chmod 644 logs/*.log

# Verify
ls -la .env.production.local
# Should show: -rw-------
```

### 3. Firewall Configuration

```bash
# Verify firewall is enabled
sudo ufw status

# Should show:
# 22/tcp     ALLOW
# 6379/tcp   DENY
# 8080/tcp   DENY
```

### 4. SSH Security

```bash
# Disable password authentication (key-only)
sudo nano /etc/ssh/sshd_config

# Set:
# PasswordAuthentication no
# PermitRootLogin no

# Restart SSH
sudo systemctl restart sshd
```

### 5. Regular Updates

```bash
# Update system monthly
sudo apt update && sudo apt upgrade -y

# Update Docker images
docker compose --env-file .env.production.local pull
docker compose --env-file .env.production.local up -d
```

### 6. Backup Important Files

```bash
# Create backup directory
mkdir -p ~/backups

# Backup configuration
cp .env.production.local ~/backups/.env.production.backup.$(date +%Y%m%d)
cp config/exchanges.yaml ~/backups/exchanges.yaml.$(date +%Y%m%d)
cp docker-compose.yml ~/backups/docker-compose.yml.$(date +%Y%m%d)

# Backup Redis data (optional)
docker exec crypto_ltp_redis redis-cli -a "YOUR_PASSWORD" BGSAVE
sleep 5
docker cp crypto_ltp_redis:/data/dump.rdb ~/backups/redis.dump.$(date +%Y%m%d).rdb
```

### 7. Monitor Logs for Errors

```bash
# Set up daily error report
crontab -e

# Add line:
0 9 * * * grep -i error ~/crypto_price_ltp/logs/*.log | mail -s "Crypto LTP Errors" your@email.com
```

---

## Quick Reference

### Essential Commands

```bash
# Start system
docker compose --env-file .env.production.local up -d

# Stop system
docker compose --env-file .env.production.local down

# View logs
docker compose logs -f

# Check status
docker ps
curl http://localhost:8080/api/status | jq

# Health check
./health_check.sh

# Restart
docker compose restart

# Update
git pull && docker compose build && docker compose up -d
```

### Important Files

```
.env.production.local      # Production configuration (keep secure!)
docker-compose.yml         # Container orchestration
config/exchanges.yaml      # Service configuration
logs/                      # Application logs
health_check.sh           # Health monitoring script
```

### Support & Resources

- **Project Repository:** https://github.com/yourusername/crypto_price_ltp
- **Issues:** Report bugs on GitHub Issues
- **Documentation:** See README.md for additional details

---

## Next Steps

After successful deployment:

1. ✅ Verify all services are running
2. ✅ Check data is being collected in Redis
3. ✅ Update your bot application with Redis connection details
4. ✅ Test bot can read price data from Redis
5. ✅ Set up automated monitoring (systemd service)
6. ✅ Configure backups for critical files
7. ✅ Document your Redis password in secure location
8. ✅ Set up SSH tunnel for dashboard access
9. ✅ Test full restart to ensure auto-start works

---

**Congratulations!** Your Crypto Price LTP system is now running on VPS. 🚀

For questions or issues, check the troubleshooting section or open an issue on GitHub.
