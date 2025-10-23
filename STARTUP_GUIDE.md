# 🚀 Crypto Price LTP - Quick Startup Guide

## Prerequisites

Before starting, ensure you have:
- ✅ Python 3.8+ installed
- ✅ Redis server running
- ✅ All dependencies installed (`pip install -r requirements.txt`)

## Quick Start (Recommended)

### Step 1: Check Redis
```bash
redis-cli ping
# Should return: PONG
```

If Redis is not running:
```bash
# macOS (using Homebrew)
brew services start redis

# Linux
sudo systemctl start redis

# Or manually
redis-server
```

### Step 2: Start the System
```bash
./start_system.sh
```

You should see:
```
==========================================
System Started Successfully!
==========================================
Web Dashboard: http://localhost:8080
API Docs: http://localhost:8080/docs

All services are in STOPPED state by default.
Use the web dashboard to start/stop individual services.
==========================================
```

### Step 3: Open Web Dashboard

Open your browser and go to:
```
http://localhost:8080
```

You'll see the dashboard with all services showing **STOPPED** status (red badges).

### Step 4: Start Services You Need

Click the **START** button on any service you want to run:

**Available Services:**
- **Bybit Spot** - Real-time spot prices (BTC, ETH, SOL, BNB, DOGE)
- **CoinDCX Futures LTP** - Futures last traded prices
- **CoinDCX Funding Rate** - Funding rates (updates every 30 min)
- **Delta Futures LTP** - Delta Exchange futures prices
- **Delta Options** - Options prices with Greeks (Delta, Gamma, Vega, Theta, IV)

The service will start within 2-3 seconds and status will change to **RUNNING** (green badge).

### Step 5: Monitor Data Collection

Watch the **Data Points** counter increase as prices are collected in real-time!

---

## Manual Start (Alternative Method)

If you prefer to run in separate terminals:

### Terminal 1 - Web Dashboard
```bash
cd /Users/anujsainicse/claude/crypto_price_ltp
python web_dashboard.py
```

### Terminal 2 - Service Manager
```bash
cd /Users/anujsainicse/claude/crypto_price_ltp
python manager.py
```

Then open http://localhost:8080 in your browser.

---

## Stopping the System

### Using Script (Recommended)
```bash
./stop_system.sh
```

### Manual Stop
```bash
# Kill both processes
pkill -f "web_dashboard.py"
pkill -f "manager.py"
```

### Force Stop (if processes hang)
```bash
pkill -9 -f "web_dashboard.py"
pkill -9 -f "manager.py"
```

---

## Usage Examples

### Starting Specific Services

**Option 1: Via Web Dashboard (Easy)**
- Open http://localhost:8080
- Click **START** button for the service you want

**Option 2: Via API (Advanced)**
```bash
# Start Bybit Spot service
curl -X POST http://localhost:8080/api/service/bybit_spot/start

# Start CoinDCX Futures
curl -X POST http://localhost:8080/api/service/coindcx_futures_ltp/start

# Start Delta Options
curl -X POST http://localhost:8080/api/service/delta_options/start
```

### Stopping Services

**Option 1: Via Web Dashboard**
- Click **STOP** button for the running service

**Option 2: Via API**
```bash
# Stop Bybit Spot service
curl -X POST http://localhost:8080/api/service/bybit_spot/stop
```

### Checking Status

**Via Web Dashboard:**
- Dashboard auto-refreshes every 2 seconds
- Status badges show current state (green=running, red=stopped)

**Via API:**
```bash
# Get all services status
curl http://localhost:8080/api/status | jq

# Health check
curl http://localhost:8080/api/health
```

---

## Accessing Collected Data

All price data is stored in Redis. You can access it in several ways:

### Method 1: Redis CLI
```bash
# List all keys
redis-cli KEYS "*"

# Get Bybit BTC spot price
redis-cli HGETALL bybit_spot:BTCUSDT

# Get CoinDCX futures data
redis-cli HGETALL coindcx_futures:B-BTC_USDT

# Get Delta options data
redis-cli HGETALL delta_options:C-BTC-108200-211025

# Monitor real-time updates
redis-cli MONITOR
```

### Method 2: Python Script
```python
import redis

# Connect to Redis
r = redis.Redis(decode_responses=True)

# Get Bybit BTC spot price
btc_data = r.hgetall('bybit_spot:BTCUSDT')
print(f"BTC Price: ${btc_data['ltp']}")
print(f"Timestamp: {btc_data['timestamp']}")

# Get all Bybit spot symbols
keys = r.keys('bybit_spot:*')
for key in keys:
    data = r.hgetall(key)
    print(f"{data['original_symbol']}: ${data['ltp']}")
```

### Method 3: Redis Desktop Manager
- Download: https://github.com/RedisInsight/RedisInsight
- Connect to: localhost:6379
- Browse keys visually

---

## Monitoring & Logs

### View Logs in Real-time

**Service Manager:**
```bash
tail -f logs/service_manager.log
```

**Web Dashboard:**
```bash
tail -f logs/web_dashboard.log
```

**Individual Services:**
```bash
tail -f logs/bybit-spot.log
tail -f logs/coindcx-futures-ltp.log
tail -f logs/delta-options.log
```

### Check System Health
```bash
# Check if processes are running
ps aux | grep -E "(manager|web_dashboard)" | grep python

# Check Redis connection
redis-cli ping

# Check data being collected
redis-cli DBSIZE
```

---

## Troubleshooting

### Issue: Port 8080 already in use
**Solution:** The system now auto-kills conflicting processes! Just run `./start_system.sh` again.

Manual fix:
```bash
# Kill process on port 8080
lsof -ti :8080 | xargs kill -9
```

### Issue: Redis not responding
**Solution:** Start Redis server
```bash
redis-server
# Or using Homebrew: brew services start redis
```

### Issue: Services show "unknown" status
**Solution:** Manager not running. Restart the system:
```bash
./stop_system.sh
./start_system.sh
```

### Issue: Service won't start
**Solution:** Check logs:
```bash
# Check manager logs for errors
tail -50 logs/service_manager.log

# Check specific service logs
tail -50 logs/bybit-spot.log
```

### Issue: No data being collected
**Solutions:**
1. Verify service is running (green badge in dashboard)
2. Check internet connection
3. Verify symbols are valid in `config/exchanges.yaml`
4. Check service logs for errors

---

## Configuration

### Modify Symbols

Edit `config/exchanges.yaml`:
```yaml
bybit:
  services:
    spot:
      symbols:
        - "BTCUSDT"
        - "ETHUSDT"
        # Add more symbols here
```

After modifying:
```bash
./stop_system.sh
./start_system.sh
```

---

## Production Deployment

### Using systemd (Linux)

**Create service file:** `/etc/systemd/system/crypto-price-ltp.service`
```ini
[Unit]
Description=Crypto Price LTP System
After=network.target redis.service

[Service]
Type=forking
User=youruser
WorkingDirectory=/path/to/crypto_price_ltp
ExecStart=/path/to/crypto_price_ltp/start_system.sh
ExecStop=/path/to/crypto_price_ltp/stop_system.sh
Restart=always

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl enable crypto-price-ltp
sudo systemctl start crypto-price-ltp
sudo systemctl status crypto-price-ltp
```

### Using screen (Any OS)

```bash
# Start in detached screen
screen -dmS crypto-ltp bash -c "cd /path/to/crypto_price_ltp && ./start_system.sh && tail -f logs/service_manager.log"

# View screen
screen -r crypto-ltp

# Detach: Ctrl+A then D

# Stop
screen -S crypto-ltp -X quit
```

---

## Quick Reference

| Command | Action |
|---------|--------|
| `./start_system.sh` | Start the complete system |
| `./stop_system.sh` | Stop the complete system |
| `redis-cli ping` | Check if Redis is running |
| `curl http://localhost:8080/api/status` | Check system status |
| `tail -f logs/service_manager.log` | View manager logs |
| `redis-cli KEYS "*"` | List all collected data |

---

## Support

For issues or questions:
1. Check logs in `logs/` directory
2. Verify Redis is running: `redis-cli ping`
3. Check process status: `ps aux | grep python`
4. Review configuration: `config/exchanges.yaml`

---

**You're all set! Happy trading! 📈**
