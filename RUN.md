# How to Run price_ltp

## Quick Start (One Command)

```bash
cd /Users/anujsainicse/claude/price_ltp && python manager.py
```

That's it! The system will start all 3 services automatically.

---

## Step-by-Step Instructions

### 1. Navigate to Project Directory

```bash
cd /Users/anujsainicse/claude/price_ltp
```

### 2. Ensure Redis is Running

```bash
# Check if Redis is running
redis-cli ping
# Should return: PONG

# If not running, start Redis:
redis-server
```

### 3. Start the System

```bash
python manager.py
```

You should see output like:

```
2025-10-18 23:44:20 | ServiceManager | INFO | ✓ Redis connection successful
2025-10-18 23:44:20 | ServiceManager | INFO | Initialized 3 services
2025-10-18 23:44:20 | ServiceManager | INFO | ================================================================================
2025-10-18 23:44:20 | ServiceManager | INFO | SERVICE SUMMARY
2025-10-18 23:44:20 | ServiceManager | INFO | ================================================================================
2025-10-18 23:44:20 | ServiceManager | INFO | 1. Bybit-Spot
2025-10-18 23:44:20 | ServiceManager | INFO | 2. CoinDCX-Futures-LTP
2025-10-18 23:44:20 | ServiceManager | INFO | 3. CoinDCX-Funding-Rate
2025-10-18 23:44:20 | ServiceManager | INFO | ================================================================================
2025-10-18 23:44:20 | Bybit-Spot | INFO | WebSocket connected successfully
2025-10-18 23:44:20 | CoinDCX-Futures-LTP | INFO | Socket.IO connected successfully
2025-10-18 23:44:20 | CoinDCX-Funding-Rate | INFO | Updated funding rates for 5 symbols
```

### 4. Verify Data is Flowing

Open a **new terminal** and run:

```bash
# Check Bybit BTC price
redis-cli HGETALL bybit_spot:BTC

# Check CoinDCX BTC price with funding rates
redis-cli HGETALL coindcx_futures:BTC

# See all available data
redis-cli KEYS "*"
```

### 5. Stop the System

Press **Ctrl+C** in the terminal running `manager.py`

Or from another terminal:

```bash
pkill -f "price_ltp.*manager.py"
```

---

## Running in Background

### Option 1: Using nohup

```bash
cd /Users/anujsainicse/claude/price_ltp
nohup python manager.py > output.log 2>&1 &
```

View logs:
```bash
tail -f output.log
```

Stop:
```bash
pkill -f "price_ltp.*manager.py"
```

### Option 2: Using screen

```bash
# Start a screen session
screen -S price_ltp

# Navigate and run
cd /Users/anujsainicse/claude/price_ltp
python manager.py

# Detach: Press Ctrl+A, then D
# Reattach: screen -r price_ltp
# Kill: screen -X -S price_ltp quit
```

### Option 3: Using tmux

```bash
# Start tmux session
tmux new -s price_ltp

# Navigate and run
cd /Users/anujsainicse/claude/price_ltp
python manager.py

# Detach: Press Ctrl+B, then D
# Reattach: tmux attach -t price_ltp
# Kill: tmux kill-session -t price_ltp
```

---

## Running Individual Services

You can run services independently for testing:

### Run Only Bybit Spot Service

```bash
cd /Users/anujsainicse/claude/price_ltp
python -m services.bybit_s.spot_service
```

### Run Only CoinDCX LTP Service

```bash
cd /Users/anujsainicse/claude/price_ltp
python -m services.coindcx_f.futures_ltp_service
```

### Run Only CoinDCX Funding Rate Service

```bash
cd /Users/anujsainicse/claude/price_ltp
python -m services.coindcx_f.funding_rate_service
```

---

## Checking Status

### Check if System is Running

```bash
ps aux | grep -E "price_ltp.*manager.py" | grep -v grep
```

### Check Service Logs

```bash
cd /Users/anujsainicse/claude/price_ltp

# Main manager log
tail -f logs/service_manager.log

# Individual service logs
tail -f logs/bybit-spot.log
tail -f logs/coindcx-futures-ltp.log
tail -f logs/coindcx-funding-rate.log

# Watch all logs together
tail -f logs/*.log
```

### Monitor Live Data

```bash
# Watch BTC price update in real-time
watch -n 1 'redis-cli HGET bybit_spot:BTC ltp'

# Monitor all prices
watch -n 2 'redis-cli KEYS "*:BTC" | xargs -I {} sh -c "echo {} && redis-cli HGET {} ltp"'
```

---

## Troubleshooting

### Redis Not Running

```bash
# macOS
brew services start redis

# Linux
sudo systemctl start redis-server

# Manual
redis-server
```

### Dependencies Missing

```bash
cd /Users/anujsainicse/claude/price_ltp
pip install -r requirements.txt
```

### Port Already in Use / Process Already Running

```bash
# Stop all instances
pkill -f "price_ltp.*manager.py"

# Wait a moment
sleep 2

# Start fresh
python manager.py
```

### No Data Appearing

```bash
# Check logs for errors
tail -100 logs/service_manager.log

# Verify Redis connection
redis-cli ping

# Check if services are connected (look for "connected successfully" messages)
grep "connected successfully" logs/*.log
```

---

## System Requirements

- **Python**: 3.8+
- **Redis**: 7.0+ (running on localhost:6379)
- **Internet**: Stable connection for WebSocket streams
- **Memory**: ~100MB
- **CPU**: Minimal (1-2% per service)

---

## Quick Reference

| Action | Command |
|--------|---------|
| **Start system** | `cd /Users/anujsainicse/claude/price_ltp && python manager.py` |
| **Stop system** | Press `Ctrl+C` or `pkill -f "price_ltp.*manager.py"` |
| **Check status** | `ps aux \| grep price_ltp` |
| **View logs** | `tail -f logs/service_manager.log` |
| **Check data** | `redis-cli HGETALL bybit_spot:BTC` |
| **List all data** | `redis-cli KEYS "*"` |

---

## Auto-Start on System Boot (Optional)

### macOS (launchd)

Create file: `~/Library/LaunchAgents/com.price_ltp.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.price_ltp</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/anujsainicse/claude/price_ltp/manager.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/anujsainicse/claude/price_ltp</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.price_ltp.plist
```

### Linux (systemd)

See SETUP_GUIDE.md for systemd service configuration.

---

**Need help? Check the logs in `logs/` directory!**
