# Redis GUI Tools Setup Guide

## Quick Start

Your Redis database **IS working** with live cryptocurrency price data. This guide helps you connect GUI tools properly.

### Connection Settings (Copy-Paste Ready):
```
Host: localhost
Port: 6379
Password: (leave empty)
Database: 0
```

---

## Recommended Tools

### 1. RedisInsight (Best Choice)

**Why:** Official Redis GUI, best features, cross-platform

#### Download:
- Website: https://redis.com/redis-enterprise/redis-insight/
- Or: `brew install --cask redis-insight` (macOS)

#### Setup:
1. Open RedisInsight
2. Click **"Add Redis Database"**
3. Select **"Connect to a Redis Database"**
4. Fill in:
   - **Host**: `localhost`
   - **Port**: `6379`
   - **Database Alias**: `Crypto Price LTP` (any name you want)
   - **Username**: Leave empty
   - **Password**: Leave empty (or delete any pre-filled text)
   - **Use TLS**: Toggle OFF
   - **Logical Database**: Leave as default or set to `0`
5. Click **"Add Redis Database"**
6. Click on your new connection to connect

#### Features You'll See:
- 📊 **Browser**: View all 10 keys (bybit_spot:*, coindcx_futures:*)
- 🔍 **Search**: Filter keys by pattern
- 📈 **Analysis**: Memory usage, key patterns
- 💻 **CLI**: Built-in Redis CLI
- 📱 **Workbench**: Query builder

#### Troubleshooting:
- **Can't see keys?** → Make sure you're viewing Database 0
- **Connection failed?** → Run `docker ps | grep redis` to verify container is running
- **"Auth failed"?** → Delete any text in password field and leave it completely empty

---

### 2. Medis (macOS Only)

**Why:** Native Mac app, beautiful UI, lightweight

#### Download:
- Mac App Store: Search "Medis"
- Or: `brew install --cask medis`
- GitHub: https://github.com/luin/medis

#### Setup:
1. Open Medis
2. Click **"New Connection"** or **"+"** button
3. Fill in:
   - **Name**: `Crypto LTP`
   - **Host**: `127.0.0.1`
   - **Port**: `6379`
   - **Password**: Leave empty
   - **Select DB**: `0` (after connecting)
4. Click **"Connect"** or **"Test & Save"**

#### Important:
After connecting, use the **database selector dropdown** (top of window) and select **"DB 0"**.

#### Features:
- 🎨 Beautiful native macOS UI
- ⚡ Fast key browsing
- 🔑 Key viewer/editor
- 📋 Clipboard integration
- 🌙 Dark mode

---

### 3. Another Redis Desktop Manager (ARDM)

**Why:** Free, open-source, works on Windows/Mac/Linux

#### Download:
- GitHub: https://github.com/qishibo/AnotherRedisDesktopManager/releases
- Or: `brew install --cask another-redis-desktop-manager` (macOS)

#### Setup:
1. Open ARDM
2. Click **"New Connection"** (top left)
3. Fill in:
   - **Name**: `Crypto Price LTP`
   - **Host**: `localhost` or `127.0.0.1`
   - **Port**: `6379`
   - **Auth**: Leave **unchecked**
   - **Separator**: Leave as default (`:`)
   - **Database**: Can leave empty (will show all DBs)
4. Click **"Test Connection"** (should show "Connected successfully")
5. Click **"OK"**

#### Usage:
- Expand connection → Expand `db0` → See your keys grouped by prefix
- Keys shown as tree: `bybit_spot` folder → `BTC`, `ETH`, etc.

#### Features:
- 📁 Tree view of keys (grouped by separators)
- 🔄 Auto-refresh
- 📊 Memory analysis
- 🔎 Pattern search
- 💾 Import/export

---

### 4. Redis Commander (Web-Based)

**Why:** No installation needed, runs in browser

#### Run with Docker:
```bash
docker run --rm --name redis-commander \
  -d \
  -p 8081:8081 \
  --network crypto_network \
  -e REDIS_HOSTS=local:redis:6379 \
  rediscommander/redis-commander
```

#### Access:
Open browser to: **http://localhost:8081**

#### Stop when done:
```bash
docker stop redis-commander
```

#### Features:
- 🌐 Browser-based (no install)
- 🔑 View/edit keys
- 📊 Server stats
- 💻 CLI access
- 🔄 Real-time updates

---

### 5. RedisCommander GUI (Desktop App)

**Why:** Simple, cross-platform, good for quick checks

#### Download:
- GitHub: https://github.com/joeferner/redis-commander
- Via npm: `npm install -g redis-commander`

#### Run:
```bash
redis-commander --redis-host localhost --redis-port 6379
```

Then open: http://localhost:8081

---

### 6. TablePlus (Paid, but Beautiful)

**Why:** Premium experience, supports many databases

#### Download:
- Website: https://tableplus.com
- Or: `brew install --cask tableplus` (macOS)

#### Setup:
1. Open TablePlus
2. Click **"Create a new connection"**
3. Select **"Redis"**
4. Fill in:
   - **Name**: `Crypto Price LTP`
   - **Host**: `localhost`
   - **Port**: `6379`
   - **Password**: Leave empty
   - **Database**: `0`
5. Click **"Test"** → Should show "Connection successful"
6. Click **"Connect"**

#### Features:
- 🎨 Beautiful UI
- ⚡ Super fast
- 🔍 Advanced filtering
- 📊 Data visualization
- 💰 Requires license ($89, has free trial)

---

## Common Issues & Solutions

### Issue 1: "Connection Refused" or "Cannot Connect"

**Causes:**
- Redis container not running
- Docker not running
- Wrong port

**Solutions:**
```bash
# 1. Check Docker is running
docker ps

# 2. Check Redis container
docker ps | grep redis

# 3. Start Redis if not running
docker-compose up -d redis

# 4. Check Redis is listening
docker exec crypto_ltp_redis redis-cli PING
# Should return: PONG

# 5. Run the test script
./test_redis_connection.sh
```

---

### Issue 2: Connected but No Keys Visible

**Causes:**
- Viewing wrong database (DB 1 instead of DB 0)
- GUI cached empty state
- Keys expired (TTL is 1 hour)

**Solutions:**

1. **Check database number**:
   - Look for database selector in GUI
   - Ensure it shows "DB 0" or "Database 0"
   - Some tools show databases as dropdown, others as tabs

2. **Refresh the view**:
   - Click refresh button
   - Close and reopen connection
   - Restart GUI tool

3. **Verify keys exist** (via CLI):
   ```bash
   docker exec crypto_ltp_redis redis-cli KEYS "*"
   # Should show 10 keys
   ```

4. **Check if services are running**:
   ```bash
   docker ps | grep crypto_ltp_app
   docker logs crypto_ltp_app --tail 20
   ```

---

### Issue 3: "Authentication Failed" or "NOAUTH"

**Cause:** GUI tool trying to use password authentication

**Solutions:**

1. **Completely clear password field**:
   - Don't type "empty"
   - Don't type ""
   - Just leave it blank or delete all text

2. **Try these variations**:
   - No password field filled
   - Uncheck "Use authentication"
   - Uncheck "Require password"
   - Set auth method to "None"

3. **Verify Redis has no password**:
   ```bash
   docker exec crypto_ltp_redis redis-cli CONFIG GET requirepass
   # Should return: requirepass (empty)

   docker exec crypto_ltp_redis redis-cli PING
   # Should return: PONG (without needing -a flag)
   ```

---

### Issue 4: "Connection Timeout"

**Cause:** Redis bound to 127.0.0.1, not 0.0.0.0

**Solution:**
Use `localhost` or `127.0.0.1` as host, NOT:
- ❌ `0.0.0.0`
- ❌ Your machine's IP (192.168.x.x)
- ❌ `host.docker.internal`

✅ Use: `localhost` or `127.0.0.1`

---

### Issue 5: Data Seems Old or Not Updating

**Causes:**
- GUI not auto-refreshing
- Services stopped updating

**Solutions:**

1. **Enable auto-refresh** (if available in GUI)
2. **Manually refresh** every few seconds
3. **Check live updates**:
   ```bash
   # Monitor Redis in real-time
   docker exec crypto_ltp_redis redis-cli MONITOR
   # Press Ctrl+C to stop

   # You should see HSET and EXPIRE commands
   ```

4. **Verify app is running**:
   ```bash
   docker logs crypto_ltp_app --tail 30 --follow
   ```

---

## What You Should See

### Expected Keys (10 total):
```
bybit_spot:BTC       → Bitcoin price from Bybit
bybit_spot:ETH       → Ethereum price from Bybit
bybit_spot:SOL       → Solana price from Bybit
bybit_spot:BNB       → Binance Coin from Bybit
bybit_spot:DOGE      → Dogecoin from Bybit
coindcx_futures:BTC  → Bitcoin futures from CoinDCX
coindcx_futures:ETH  → Ethereum futures from CoinDCX
coindcx_futures:SOL  → Solana futures from CoinDCX
coindcx_futures:BNB  → Binance Coin from CoinDCX
coindcx_futures:DOGE → Dogecoin from CoinDCX
```

### Each Key Contains (HASH type):
```
ltp                   → "102076.4" (Last Traded Price)
timestamp             → "2025-11-08T13:40:54.067098Z"
original_symbol       → "BTCUSDT"
volume_24h            → "9028.482883"
high_24h              → "104083.8"
low_24h               → "99509.8"
price_change_percent  → "0.0176"
```

### Key Properties:
- **Type**: HASH
- **TTL**: ~3600 seconds (1 hour)
- **Size**: ~200-300 bytes each
- **Updates**: Every few seconds (live WebSocket data)

---

## Viewing Data Examples

### View BTC Price:
1. Navigate to key `bybit_spot:BTC`
2. Click to expand (it's a HASH)
3. See fields: `ltp`, `timestamp`, `volume_24h`, etc.
4. Watch `ltp` value update in real-time

### Search/Filter Keys:
- Pattern: `bybit_spot:*` → Shows only Bybit data
- Pattern: `*:BTC` → Shows BTC from all exchanges
- Pattern: `coindcx_*` → Shows only CoinDCX data

### Check Key TTL:
Most GUIs show TTL automatically. You should see:
- ~3600 seconds for fresh data
- Counting down every second
- Resets to 3600 when data updates

---

## Quick Verification Checklist

Before connecting GUI tool:

- [ ] Docker is running: `docker ps`
- [ ] Redis container is healthy: `docker ps | grep redis`
- [ ] Redis responds to PING: `docker exec crypto_ltp_redis redis-cli PING`
- [ ] Keys exist: `docker exec crypto_ltp_redis redis-cli DBSIZE` (should show 10)
- [ ] App is running: `docker ps | grep crypto_ltp_app`

GUI connection checklist:

- [ ] Host: `localhost` or `127.0.0.1`
- [ ] Port: `6379`
- [ ] Password: Empty/blank
- [ ] Database: `0` (or default, which is usually 0)
- [ ] TLS: Disabled/off
- [ ] Authentication: Disabled/none

After connecting:

- [ ] Can see 10 keys
- [ ] Keys are prefixed with `bybit_spot:` and `coindcx_futures:`
- [ ] Can open `bybit_spot:BTC` and see hash fields
- [ ] `ltp` value is a reasonable number (e.g., 102000 for BTC)
- [ ] `timestamp` is recent (within last minute)

---

## Still Not Working?

### Run Full Diagnostic:
```bash
# Run the comprehensive test script
./test_redis_connection.sh
```

This will check:
- ✅ Docker status
- ✅ Redis container status
- ✅ Connection test
- ✅ Key count
- ✅ Sample data
- ✅ Operation statistics

### Get Support:

1. **Check logs**:
   ```bash
   docker logs crypto_ltp_redis --tail 50
   docker logs crypto_ltp_app --tail 50
   ```

2. **Verify environment**:
   ```bash
   cat .env.docker | grep REDIS
   ```

3. **Check network**:
   ```bash
   docker network inspect crypto_network
   ```

---

## Summary

You have **multiple excellent options** for viewing your Redis data:

| Tool | Platform | Complexity | Best For |
|------|----------|------------|----------|
| **RedisInsight** | All | Easy | Everyone, feature-rich |
| **Medis** | macOS | Very Easy | Mac users, simple UI |
| **ARDM** | All | Easy | Tree view lovers |
| **Redis Commander** | Browser | None | Quick checks, no install |
| **TablePlus** | All | Easy | Premium experience |

**Recommended**: Start with **RedisInsight** (official, cross-platform, free)

---

## Next Steps

1. **Choose a GUI tool** from the list above
2. **Download and install** it
3. **Use the connection settings** from the top of this guide
4. **Select Database 0** after connecting
5. **Browse your 10 cryptocurrency price keys**
6. **Watch live updates** as prices change

Your Redis database is working perfectly - you just need to connect your GUI tool correctly! 🚀
