# How to Run This Project

## ✅ Quick Start (Recommended - Docker)

Your project is now properly configured and running with Docker!

### Start the Project
```bash
cd /Users/anujsainicse/claude/crypto_price_ltp
docker-compose up -d
```

### Access the Dashboard
Open your browser to: **http://localhost:8080**

API Documentation: **http://localhost:8080/docs**

### Check Status
```bash
# View logs
docker-compose logs -f

# Check running containers
docker ps

# Check service status
curl http://localhost:8080/api/status
```

### Stop the Project
```bash
docker-compose down
```

---

## 🔧 What's Running

When you start with `docker-compose up -d`, the following happens automatically:

### Containers Started:
1. **Redis Container** (`crypto_ltp_redis`)
   - Port: `127.0.0.1:6379`
   - Stores all cryptocurrency price data
   - Auto-persistence enabled
   - No password (secure for localhost)

2. **Application Container** (`crypto_ltp_app`)
   - Port: `127.0.0.1:8080`
   - Web Dashboard + API
   - Service Manager

### Services Auto-Started:
These services start automatically and begin collecting data:

1. **Bybit Spot** ✅
   - Collecting: BTC, ETH, SOL, BNB, DOGE prices
   - WebSocket: Live price updates
   - Redis keys:
     - `bybit_spot:*` - Last traded price (LTP)
     - `bybit_spot_ob:*` - Orderbook data (50-level bids/asks, spread, mid_price)
     - `bybit_spot_trades:*` - Recent trades (rolling 50 trades)

2. **CoinDCX Futures LTP** ✅
   - Collecting: BTC, ETH, SOL, BNB, DOGE futures prices
   - WebSocket: Live futures data
   - Redis keys: `coindcx_futures:*`

3. **CoinDCX Funding Rate** ✅
   - Collecting: Funding rates for all futures
   - Updates: Every 30 minutes
   - Enriches futures data

### Services Available (Manual Start):
These services are configured but not auto-started:

- **Delta Futures LTP** ⏸️ (start via dashboard)
- **Delta Options** ⏸️ (start via dashboard)

---

## 📊 Viewing Your Data

### Option 1: Web Dashboard
```bash
open http://localhost:8080
```

The dashboard shows:
- Service status (running/stopped)
- Data counts per exchange
- Start/stop controls
- Real-time updates

### Option 2: RedisInsight (GUI Tool)
1. **Download**: https://redis.com/redis-enterprise/redis-insight/
   - Or: `brew install --cask redis-insight`

2. **Connect**:
   - Host: `localhost`
   - Port: `6379`
   - Password: (leave empty)
   - Database: `0`

3. **View Live Data**:
   - Browse 15+ keys
   - Watch prices update in real-time
   - Query specific cryptocurrencies

### Option 3: Command Line
```bash
# List all keys
docker exec crypto_ltp_redis redis-cli KEYS "*"

# Get BTC price
docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:BTC"

# Watch live updates
docker exec crypto_ltp_redis redis-cli MONITOR
```

---

## 🔄 Common Operations

### Restart Everything
```bash
docker-compose restart
```

### Rebuild After Code Changes
```bash
# Stop containers
docker-compose down

# Rebuild image
docker-compose build

# Start with new image
docker-compose up -d
```

### View Logs
```bash
# All logs
docker-compose logs -f

# App only
docker logs crypto_ltp_app -f

# Redis only
docker logs crypto_ltp_redis -f

# Last 50 lines
docker logs crypto_ltp_app --tail 50
```

### Clean Start (Remove All Data)
```bash
# Stop and remove volumes
docker-compose down -v

# Rebuild and start
docker-compose build
docker-compose up -d
```

---

## 🛠️ Troubleshooting

### Issue: Port Conflict (6379 or 8080)

**Symptom**: `bind: address already in use`

**Solution**:
```bash
# Check what's using the port
lsof -i :6379
lsof -i :8080

# Stop local Redis (if running)
brew services stop redis

# Or kill specific process
kill <PID>
```

### Issue: Containers Not Starting

**Solution**:
```bash
# Check container status
docker ps -a

# Check logs for errors
docker-compose logs

# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

### Issue: No Data in Redis

**Solution**:
```bash
# 1. Check services are running
curl http://localhost:8080/api/status

# 2. Check app logs
docker logs crypto_ltp_app --tail 50

# 3. Verify Redis is accessible
docker exec crypto_ltp_redis redis-cli PING

# 4. Check keys exist
docker exec crypto_ltp_redis redis-cli KEYS "*"

# 5. Restart services if needed
docker-compose restart app
```

### Issue: Old/Stale Data

**Symptom**: RedisInsight shows old timestamps

**Solution**:
1. Refresh RedisInsight (click refresh icon)
2. Close and reopen the key
3. Verify data is actually updating:
```bash
# Check current timestamp
docker exec crypto_ltp_redis redis-cli HGET "bybit_spot:BTC" "timestamp"

# Should show very recent time
```

---

## 🎯 Current Configuration

### Redis
- **Host**: `redis` (container name) inside Docker network
- **Host**: `localhost` outside Docker (for GUI tools)
- **Port**: `6379`
- **Password**: None (empty)
- **Database**: `0`
- **Data TTL**: 7200 seconds (2 hours)
- **Persistence**: Enabled (AOF + RDB snapshots)

### Environment Variables
File: `.env` (copied from `.env.docker`)
```
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
REDIS_TTL=7200
LOG_LEVEL=INFO
```

### Service Configuration
File: `config/exchanges.yaml`
- Bybit Spot: `auto_start: true`
- CoinDCX Futures LTP: `auto_start: true`
- CoinDCX Funding Rate: `auto_start: true`
- Delta Futures: `auto_start: false`
- Delta Options: `auto_start: false`

---

## 📁 Important Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Docker services configuration |
| `.env` | Environment variables (Docker) |
| `Dockerfile` | Application container definition |
| `docker-entrypoint.sh` | Container startup script |
| `config/exchanges.yaml` | Service configuration |
| `HOW_TO_RUN.md` | This guide |
| `REDIS_CONNECTION_GUIDE.md` | Detailed Redis connection info |
| `GUI_TOOLS_SETUP.md` | GUI tool setup guides |

---

## 🚀 Alternative: Local Development (Without Docker)

If you prefer running without Docker:

### Prerequisites
```bash
# Install Redis
brew install redis

# Start Redis
brew services start redis

# Install Python dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env to use localhost
nano .env
# Set: REDIS_HOST=localhost
```

### Start
```bash
./run.sh
```

### Stop
Press `Ctrl+C` in the terminal

---

## 📝 Quick Reference Commands

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# Restart
docker-compose restart

# Rebuild
docker-compose build

# Logs
docker-compose logs -f

# Status
docker ps
curl http://localhost:8080/api/status

# View data
docker exec crypto_ltp_redis redis-cli KEYS "*"
docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:BTC"

# Access dashboard
open http://localhost:8080

# Access API docs
open http://localhost:8080/docs

# Clean everything
docker-compose down -v
```

---

## 🎉 Success Checklist

Your project is working correctly if:

- ✅ `docker ps` shows 2 running containers (redis + app)
- ✅ `docker logs crypto_ltp_app` shows "All services started successfully!"
- ✅ `curl http://localhost:8080/api/status` returns service info
- ✅ `docker exec crypto_ltp_redis redis-cli KEYS "*"` shows 15+ keys
- ✅ `docker exec crypto_ltp_redis redis-cli HGET "bybit_spot:BTC" "timestamp"` shows recent time
- ✅ http://localhost:8080 shows the dashboard
- ✅ RedisInsight can connect and shows live data

---

## 💡 Tips

1. **Always use Docker for deployment** - It ensures consistency and includes Redis
2. **Use RedisInsight for data exploration** - Much easier than command line
3. **Monitor logs when starting** - `docker-compose logs -f` shows any issues
4. **Check service status regularly** - `curl http://localhost:8080/api/status`
5. **Rebuild after major code changes** - `docker-compose build`
6. **Use clean start if issues persist** - `docker-compose down -v && docker-compose up -d`

---

## 📚 More Documentation

- **Redis Connection**: See `REDIS_CONNECTION_GUIDE.md`
- **GUI Tools**: See `GUI_TOOLS_SETUP.md`
- **VPS Deployment**: See `VPS_DEPLOYMENT.md`
- **Docker Details**: See `DOCKER.md`
- **General Info**: See `START_HERE.md`

---

## 🆘 Getting Help

### Check Service Status
```bash
# Via API
curl http://localhost:8080/api/status | python3 -m json.tool

# Via logs
docker logs crypto_ltp_app --tail 100
```

### Verify Data Flow
```bash
# Watch Redis operations
docker exec crypto_ltp_redis redis-cli MONITOR

# Check specific cryptocurrency
docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:ETH"
```

### Common Solutions
1. **Data not updating**: Restart containers with `docker-compose restart`
2. **Can't access dashboard**: Check port 8080 isn't in use with `lsof -i :8080`
3. **Redis connection failed**: Ensure Redis container is healthy with `docker ps`
4. **Services not starting**: Check logs with `docker logs crypto_ltp_app`

---

**Your project is now running with fresh, live cryptocurrency data!** 🎉📈

Access your dashboard at: **http://localhost:8080**
