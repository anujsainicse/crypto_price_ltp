# 🚀 START HERE - Crypto Price LTP

## The Easiest Way to Start/Stop

### ⚡ One Command to Rule Them All

```bash
./run.sh
```

That's it! This single command will:
- ✅ Start the web dashboard
- ✅ Start the service manager
- ✅ Show you the dashboard URL
- ✅ Display live logs
- ✅ Handle **Ctrl+C** gracefully to stop everything

---

## 🎮 How to Use

### Start the System
```bash
cd /Users/anujsainicse/claude/crypto_price_ltp
./run.sh
```

You'll see:
```
==========================================
  System Started Successfully!
==========================================

  🌐 Web Dashboard: http://localhost:8080
  📚 API Docs: http://localhost:8080/docs

  All services are in STOPPED state.
  Open the dashboard to start services.

==========================================
  Press Ctrl+C to stop all services
==========================================
```

### Stop the System

Just press **Ctrl+C** in the terminal where `run.sh` is running!

```
^C
==========================================
Shutting down Crypto Price LTP System...
==========================================
✓ Stopping Web Dashboard (PID: 12345)...
✓ Stopping Service Manager (PID: 12346)...
✓ System stopped successfully!
```

**Everything stops cleanly!** ✨

---

## 📱 Access Your Dashboard

Once started, open your browser:
```
http://localhost:8080
```

### What You'll See:
- **All services showing STOPPED** (red badges)
- Click **START** button to activate any service
- Watch **Data Points** counter increase as data is collected
- Click **STOP** button to deactivate a service

---

## 🎯 Quick Reference

| Action | Command |
|--------|---------|
| **Start everything** | `./run.sh` |
| **Stop everything** | Press `Ctrl+C` |
| **View dashboard** | http://localhost:8080 |
| **Check logs** | Already showing in terminal! |
| **View data** | `redis-cli KEYS "*"` |

---

## 📊 Available Services

Once you open the dashboard, you can start any of these:

1. **Bybit Spot** - Real-time spot prices (BTC, ETH, SOL, BNB, DOGE)
2. **CoinDCX Futures LTP** - Futures last traded prices
3. **CoinDCX Funding Rate** - Funding rates (updates every 30 min)
4. **Delta Futures LTP** - Delta Exchange futures prices
5. **Delta Options** - Options with Greeks (Delta, Gamma, Vega, Theta, IV)

---

## 🔍 View Collected Data

### From Redis CLI:
```bash
# Get Bitcoin price
redis-cli HGETALL bybit_spot:BTCUSDT

# List all data
redis-cli KEYS "*"

# Watch real-time updates
redis-cli MONITOR
```

### From Python:
```python
import redis
r = redis.Redis(decode_responses=True)

# Get BTC price
btc = r.hgetall('bybit_spot:BTCUSDT')
print(f"BTC: ${btc['ltp']}")
print(f"Time: {btc['timestamp']}")
```

---

## ⚙️ Prerequisites

Before running `./run.sh`, make sure:

1. **Redis is running**
   ```bash
   redis-cli ping
   # Should return: PONG
   ```

   If not:
   ```bash
   # macOS
   brew services start redis

   # Linux
   sudo systemctl start redis

   # Or manually
   redis-server
   ```

2. **Dependencies installed**
   ```bash
   pip install -r requirements.txt
   ```

---

## 🆘 Troubleshooting

### Problem: Script won't start
**Solution:**
```bash
# Make sure it's executable
chmod +x run.sh

# Check Redis is running
redis-cli ping
```

### Problem: Port 8080 in use
**Solution:** The script automatically kills conflicting processes! Just run `./run.sh` again.

### Problem: Services won't start from dashboard
**Solution:** Check the logs shown in the terminal where `run.sh` is running.

---

## 📝 Advanced Usage

### Run in Background with Screen
```bash
screen -S crypto-ltp
./run.sh

# Detach: Press Ctrl+A then D
# Reattach: screen -r crypto-ltp
# Stop: Reattach and press Ctrl+C
```

### Run in Background with nohup
```bash
nohup ./run.sh > output.log 2>&1 &

# Get the PID
echo $!

# Stop later
kill <PID>
```

---

## 🎉 That's It!

**You're ready to go!**

1. Run `./run.sh`
2. Open http://localhost:8080
3. Click START on the services you want
4. Press Ctrl+C when done

**Simple as that!** 🚀

---

## 📚 More Documentation

- **Detailed Guide:** See `STARTUP_GUIDE.md`
- **Quick Reference:** See `QUICK_START.txt`
- **Project Structure:** See `PROJECT_STRUCTURE.md`

---

**Need help? Check the logs in the terminal where `run.sh` is running!**
