# Redis Connection Guide

## ✅ Verification: Redis is Working!

Your Redis database IS actively receiving data:
- **Total Keys**: 10 cryptocurrency price feeds
- **Operations**: 2.4+ million commands processed
- **Activity**: 200+ operations per second
- **Data TTL**: 3600 seconds (1 hour auto-refresh)

### Current Keys in Database:
```
bybit_spot:BTC
bybit_spot:ETH
bybit_spot:SOL
bybit_spot:BNB
bybit_spot:DOGE
coindcx_futures:BTC
coindcx_futures:ETH
coindcx_futures:SOL
coindcx_futures:BNB
coindcx_futures:DOGE
```

### Sample Data (BTC):
```json
{
  "ltp": "102061.0",
  "timestamp": "2025-11-08T13:38:52.066292Z",
  "original_symbol": "BTCUSDT",
  "volume_24h": "9019.600887",
  "high_24h": "104083.8",
  "low_24h": "99509.8",
  "price_change_percent": "0.0176"
}
```

---

## GUI Tool Connection Settings

### Correct Connection Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Host** | `localhost` or `127.0.0.1` | Redis is bound to localhost only |
| **Port** | `6379` | Default Redis port |
| **Password** | *Leave empty/blank* | No password configured |
| **Database** | `0` | All price data is in DB 0 |
| **TLS/SSL** | Disabled | Local connection, no encryption |

---

## Popular GUI Tools Configuration

### RedisInsight (Recommended)
1. Click "Add Redis Database"
2. Select "Connect to a Redis Database"
3. **Host**: `localhost`
4. **Port**: `6379`
5. **Database Alias**: `Crypto Price LTP`
6. **Username**: Leave empty
7. **Password**: Leave empty
8. **Use TLS**: Off
9. Click "Add Redis Database"

### Medis (macOS)
1. Click "New Connection"
2. **Host**: `127.0.0.1`
3. **Port**: `6379`
4. **Auth**: Leave empty
5. **Name**: `Crypto LTP`
6. Click "Connect"
7. **Important**: Select "DB 0" from the database dropdown

### Another Redis Desktop Manager
1. Click "Connect to Redis Server"
2. **Name**: `Crypto Price LTP`
3. **Host**: `localhost`
4. **Port**: `6379`
5. **Auth**: Leave unchecked
6. Click "Test Connection" → "OK"

### Redis Commander (Web-based)
```bash
# Run Redis Commander in Docker
docker run --rm --name redis-commander \
  -p 8081:8081 \
  --network crypto_network \
  -e REDIS_HOSTS=local:redis:6379 \
  rediscommander/redis-commander

# Then open: http://localhost:8081
```

---

## Command Line Verification

### Quick Test Commands

```bash
# 1. List all keys
docker exec crypto_ltp_redis redis-cli KEYS "*"

# 2. Get total key count
docker exec crypto_ltp_redis redis-cli DBSIZE

# 3. Check BTC price
docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:BTC"

# 4. Monitor live updates
docker exec crypto_ltp_redis redis-cli MONITOR

# 5. Check database stats
docker exec crypto_ltp_redis redis-cli INFO stats

# 6. Check specific key TTL
docker exec crypto_ltp_redis redis-cli TTL "bybit_spot:BTC"
```

### Interactive Redis CLI

```bash
# Enter Redis CLI
docker exec -it crypto_ltp_redis redis-cli

# Then run commands:
> KEYS *
> HGETALL bybit_spot:ETH
> TTL coindcx_futures:SOL
> INFO
> exit
```

---

## Troubleshooting

### Issue: "Connection Refused"
**Cause**: Docker container not running
**Solution**:
```bash
docker-compose up -d redis
docker ps | grep redis
```

### Issue: "No keys visible in GUI"
**Causes**:
1. Connected to wrong database (should be DB 0)
2. GUI cached empty state
3. Keys expired (TTL is 3600 seconds)

**Solutions**:
1. Ensure database selector shows "DB 0" or "0"
2. Refresh/reconnect the GUI tool
3. Verify keys exist: `docker exec crypto_ltp_redis redis-cli KEYS "*"`

### Issue: "Authentication failed"
**Cause**: GUI trying to use a password
**Solution**: Ensure password field is completely empty (not "password", not "empty", just blank)

### Issue: "Data seems old or not updating"
**Cause**: Services might not be running
**Solution**:
```bash
# Check app container logs
docker logs crypto_ltp_app --tail 50

# Verify services are running
curl http://localhost:8000/status
```

---

## Data Structure Reference

All price data is stored as Redis HASHes with the following structure:

### Key Format:
```
{exchange}_{market_type}:{symbol}
```

Examples:
- `bybit_spot:BTC` - Bybit spot market Bitcoin price
- `coindcx_futures:ETH` - CoinDCX futures Ethereum price

### Hash Fields:
| Field | Description | Example |
|-------|-------------|---------|
| `ltp` | Last Traded Price | "102061.0" |
| `timestamp` | ISO 8601 timestamp | "2025-11-08T13:38:52.066292Z" |
| `original_symbol` | Exchange-specific symbol | "BTCUSDT" |
| `volume_24h` | 24-hour volume | "9019.600887" |
| `high_24h` | 24-hour high | "104083.8" |
| `low_24h` | 24-hour low | "99509.8" |
| `price_change_percent` | 24h change % | "0.0176" |

### Data Expiration:
- **TTL**: 3600 seconds (1 hour)
- **Auto-refresh**: Services update prices continuously
- **Purpose**: Prevents stale data if services crash

---

## Monitoring Data Flow

### Watch Live Updates
```bash
# See every command in real-time
docker exec crypto_ltp_redis redis-cli MONITOR

# You should see:
# HSET bybit_spot:BTC "ltp" "102100.5" ...
# EXPIRE bybit_spot:BTC 3600
# HSET coindcx_futures:ETH "ltp" "3392.1" ...
```

### Check Service Status
```bash
# Via API
curl http://localhost:8000/status

# Via Docker logs
docker logs crypto_ltp_app --follow
```

---

## Quick Connection Test

Run this to verify everything is working:

```bash
echo "=== Redis Connection Test ==="
echo "1. Container status:"
docker ps --filter name=crypto_ltp_redis --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo -e "\n2. Total keys:"
docker exec crypto_ltp_redis redis-cli DBSIZE

echo -e "\n3. Sample keys:"
docker exec crypto_ltp_redis redis-cli KEYS "*" | head -5

echo -e "\n4. BTC price data:"
docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:BTC"

echo -e "\n5. Operations per second:"
docker exec crypto_ltp_redis redis-cli INFO stats | grep instantaneous_ops_per_sec

echo -e "\n✅ If you see data above, Redis is working perfectly!"
```

---

## Next Steps

1. **Choose a GUI tool** from the list above (RedisInsight recommended)
2. **Use the exact connection settings** provided
3. **Select Database 0** after connecting
4. **Refresh** if you don't see keys immediately
5. **Monitor live updates** using the MONITOR command

If you still don't see data after following these steps, run the "Quick Connection Test" above and share the output.
