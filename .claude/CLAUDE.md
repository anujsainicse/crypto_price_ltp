# Crypto Price LTP - AI Development Guide

## Overview

**Port**: 8080
**Purpose**: Real-time cryptocurrency price and funding rate monitoring from all exchanges.
**Location**: `~/claude/crypto_price_ltp/`

The Crypto Price LTP service provides real-time price data via WebSocket streaming and stores prices in Redis for high-performance retrieval by other services.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        CRYPTO PRICE LTP (Port 8080)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │     Bybit     │  │    CoinDCX    │  │     Delta     │  │  HyperLiquid │  │
│  │   WebSocket   │  │   Socket.IO   │  │   WebSocket   │  │   WebSocket  │  │
│  ├───────────────┤  ├───────────────┤  ├───────────────┤  ├──────────────┤  │
│  │ Spot+Testnet  │  │ Spot+Futures  │  │Spot+Fut+Opts  │  │  Spot+Perp   │  │
│  │ LTP/OB/Trades │  │ LTP/OB/Trades │  │ LTP/OB/Trades │  │ LTP/OB/Trades│  │
│  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  └──────┬───────┘  │
│          │                  │                  │                  │          │
│          └──────────────────┴──────────────────┴──────────────────┘          │
│                                      │                                       │
│                                      ▼                                       │
│                          ┌─────────────────────┐                             │
│                          │   Price Processor   │                             │
│                          │ LTP / Orderbook /   │                             │
│                          │ Trades / Funding    │                             │
│                          └──────────┬──────────┘                             │
│                                     │                                        │
│                                     ▼                                        │
│                          ┌─────────────────────┐                             │
│                          │       REDIS         │                             │
│                          │   Hash Storage      │                             │
│                          │  (TTL: 60 seconds)  │                             │
│                          └─────────────────────┘                             │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                       Web Dashboard (:8080)                            │  │
│  │     Start/Stop Services | View Prices | Monitor Status | Service Logs │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ Redis HGET/HGETALL
                                      ▼
                       ┌────────────────────────────┐
                       │   AOE / Scalper Backend    │
                       │    (Price Monitoring)      │
                       └────────────────────────────┘
```

---

## Exchanges Supported

| Exchange | Service | Market Type | Symbols | Data Provided |
|----------|---------|-------------|---------|---------------|
| **Bybit** | `bybit_spot` | Spot | BTC, ETH, SOL, BNB, DOGE, MNT, HYPE | LTP + Orderbook + Trades |
| **Bybit** | `bybit_spot_testnet` | Spot (Testnet) | BTC, ETH, SOL, BNB, DOGE, MNT, HYPE | LTP |
| **CoinDCX** | `coindcx_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **CoinDCX** | `coindcx_futures` | Futures | BTC, ETH, SOL, BNB, DOGE | LTP + Funding Rate |
| **Delta** | `delta_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **Delta** | `delta_futures` | Futures | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **Delta** | `delta_options` | Options | BTC, ETH (all strikes) | LTP + Greeks |
| **HyperLiquid** | `hyperliquid_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **HyperLiquid** | `hyperliquid_futures` | Perpetual | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |

**Total Services**: 10

---

## Redis Key Structure

### Key Patterns
```
{exchange_prefix}:{symbol}           # LTP/Ticker data
{exchange_prefix}_ob:{symbol}        # Orderbook data
{exchange_prefix}_trades:{symbol}    # Recent trades
```

### Examples
```
# LTP Keys
bybit_spot:BTC
coindcx_spot:ETH
coindcx_futures:BTC
delta_spot:SOL
delta_futures:BTC
hyperliquid_spot:BTC
hyperliquid_futures:ETH

# Orderbook Keys
bybit_spot_ob:BTC
coindcx_spot_ob:ETH
delta_spot_ob:SOL
delta_futures_ob:BTC
hyperliquid_spot_ob:BTC
hyperliquid_futures_ob:BTC

# Trades Keys
bybit_spot_trades:BTC
coindcx_spot_trades:ETH
delta_spot_trades:SOL
delta_futures_trades:BTC
hyperliquid_spot_trades:BTC
hyperliquid_futures_trades:BTC
```

### Hash Fields

**LTP/Ticker Data:**
```json
{
  "ltp": "45000.50",
  "timestamp": "1704628800",
  "original_symbol": "BTCUSDT",
  "volume_24h": "1234.56",
  "high_24h": "46000.00",
  "low_24h": "44000.00",
  "price_change_percent": "2.5",
  "current_funding_rate": "0.0001"
}
```

**Orderbook Data:**
```json
{
  "bids": "[[45000.50, 1.5], [45000.00, 2.3], ...]",
  "asks": "[[45001.00, 1.2], [45001.50, 0.9], ...]",
  "spread": "0.50",
  "mid_price": "45000.75",
  "update_id": "1234567890",
  "timestamp": "2026-01-24T10:30:45Z",
  "original_symbol": "BTCUSDT"
}
```

**Trades Data:**
```json
{
  "trades": "[{\"p\":45000.5,\"q\":0.5,\"s\":\"Buy\",\"t\":1705834245000,\"id\":\"abc123\"}, ...]",
  "count": "50",
  "timestamp": "2026-01-24T10:30:45Z",
  "original_symbol": "BTCUSDT"
}
```

**Notes:**
- Orderbook: 50 levels each side (bids descending, asks ascending)
- Trades: Last 50 trades in FIFO buffer
- All keys have 60-second TTL (configurable via `redis_ttl`)

---

## Reading Data (Client Code)

### Reading LTP/Ticker

```python
import redis
import json
from datetime import datetime

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# Get CoinDCX BTC futures price
data = redis_client.hgetall("coindcx_futures:BTC")
ltp = float(data[b'ltp'])
funding_rate = float(data.get(b'current_funding_rate', b'0'))
timestamp = datetime.fromtimestamp(int(data[b'timestamp']))

print(f"BTC LTP: ${ltp:,.2f}")
print(f"Funding Rate: {funding_rate:.4%}")
print(f"Last Update: {timestamp}")
```

### Reading Orderbook

```python
# Get Bybit BTC orderbook
ob_data = redis_client.hgetall("bybit_spot_ob:BTC")
bids = json.loads(ob_data[b'bids'])  # [[price, qty], ...]
asks = json.loads(ob_data[b'asks'])  # [[price, qty], ...]
spread = float(ob_data[b'spread'])
mid_price = float(ob_data[b'mid_price'])

print(f"Best Bid: ${bids[0][0]} ({bids[0][1]} qty)")
print(f"Best Ask: ${asks[0][0]} ({asks[0][1]} qty)")
print(f"Spread: ${spread:.2f}")
print(f"Mid Price: ${mid_price:.2f}")
```

### Reading Recent Trades

```python
# Get Bybit BTC trades
trades_data = redis_client.hgetall("bybit_spot_trades:BTC")
trades = json.loads(trades_data[b'trades'])  # List of trade dicts
count = int(trades_data[b'count'])

for trade in trades[-5:]:  # Last 5 trades
    print(f"{trade['s']}: {trade['q']} @ ${trade['p']}")
```

---

## Web Dashboard

**URL**: http://localhost:8080

### Features
- Start/stop individual exchange services
- View real-time prices
- Monitor connection status
- View service logs

### Dashboard Controls
| Action | Description |
|--------|-------------|
| **Start All** | Start all exchange WebSocket connections |
| **Stop All** | Stop all connections |
| **Start Bybit** | Start Bybit spot price stream |
| **Stop CoinDCX** | Stop CoinDCX futures stream |

---

## Auto-Reconnection

Each WebSocket connection implements automatic reconnection:

1. **Connection Lost**: Detected within 30 seconds (ping/pong)
2. **Backoff**: 5s → 10s → 20s → 40s → 60s (max)
3. **Reconnect**: Attempts indefinitely
4. **Recovery**: Resumes price streaming automatically

---

## Configuration

### config/exchanges.yaml

```yaml
bybit:
  name: "Bybit"
  enabled: true
  services:
    spot:
      enabled: true
      auto_start: true
      websocket_url: "wss://stream.bybit.com/v5/public/spot"
      symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]
      redis_prefix: "bybit_spot"
      redis_ttl: 60
      # Orderbook configuration
      orderbook_enabled: true
      orderbook_depth: 50
      orderbook_redis_prefix: "bybit_spot_ob"
      # Trades configuration
      trades_enabled: true
      trades_limit: 50
      trades_redis_prefix: "bybit_spot_trades"
      # Symbol parsing
      quote_currencies: ["USDT", "USDC", "BTC", "ETH"]

coindcx:
  name: "CoinDCX"
  enabled: true
  services:
    spot:
      enabled: true
      websocket_url: "wss://stream.coindcx.com"
      symbols: ["KC-BTC_USDT", "KC-ETH_USDT", "KC-SOL_USDT"]
      redis_prefix: "coindcx_spot"
      redis_ttl: 60
      orderbook_enabled: true
      orderbook_depth: 20
      trades_enabled: true
      trades_limit: 50

delta:
  name: "Delta Exchange India"
  enabled: true
  services:
    spot:
      enabled: true
      websocket_url: "wss://socket.india.delta.exchange"
      symbols: ["BTCUSD", "ETHUSD", "SOLUSD"]
      redis_prefix: "delta_spot"
      redis_ttl: 60
      orderbook_enabled: true
      orderbook_depth: 50
      trades_enabled: true
      trades_limit: 50
    futures_ltp:
      enabled: true
      websocket_url: "wss://socket.india.delta.exchange"
      symbols: ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "DOGEUSD"]
      redis_prefix: "delta_futures"
      redis_ttl: 60
      quote_currencies: ["USD", "USDT"]
      orderbook_enabled: true
      orderbook_depth: 50
      orderbook_redis_prefix: "delta_futures_ob"
      trades_enabled: true
      trades_limit: 50
      trades_redis_prefix: "delta_futures_trades"

hyperliquid:
  name: "HyperLiquid"
  enabled: true
  services:
    spot:
      enabled: true
      websocket_url: "wss://api.hyperliquid.xyz/ws"
      symbols: ["BTC", "ETH", "SOL", "BNB", "DOGE"]
      redis_prefix: "hyperliquid_spot"
      redis_ttl: 60
```

---

## Environment Variables

```env
# Required
REDIS_URL=redis://localhost:6379/0

# Optional
LOG_LEVEL=INFO
WEB_DASHBOARD_PORT=8080
RECONNECT_MAX_DELAY=60
```

---

## Setup & Running

```bash
cd ~/claude/crypto_price_ltp
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start with web dashboard
python main.py

# Or run specific service
python -m services.bybit_spot
```

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point, starts web dashboard |
| `web_dashboard.py` | Flask dashboard for service control |
| `manager.py` | Service lifecycle management (registers all 10 services) |
| `core/redis_client.py` | Redis connection + orderbook/trades storage methods |
| `core/base_service.py` | Abstract base class for all services |
| `config/settings.py` | Global settings (Redis, logging) |
| `config/exchanges.yaml` | Exchange and service configuration |

### Service Files

| Service | File | Features |
|---------|------|----------|
| Bybit Spot | `services/bybit_s/spot_service.py` | LTP + Orderbook + Trades |
| Bybit Testnet | `services/bybit_spot_testnet/spot_testnet_service.py` | LTP |
| CoinDCX Spot | `services/coindcx_s/spot_service.py` | LTP + Orderbook + Trades |
| CoinDCX Futures | `services/coindcx_f/futures_ltp_service.py` | LTP |
| CoinDCX Funding | `services/coindcx_f/funding_rate_service.py` | Funding Rates |
| Delta Spot | `services/delta_s/spot_service.py` | LTP + Orderbook + Trades |
| Delta Futures | `services/delta_f/futures_ltp_service.py` | LTP + Orderbook + Trades |
| Delta Options | `services/delta_o/options_service.py` | LTP + Greeks |
| HyperLiquid Spot | `services/hyperliquid_s/spot_service.py` | LTP |
| HyperLiquid Perp | `services/hyperliquid_p/perpetual_service.py` | LTP |

---

## Integration with AOE

The Advanced Order Engine (AOE) uses Crypto Price LTP for:

1. **Price Monitoring**: SL/TP trigger price checks (100ms polling)
2. **Trailing Stops**: Current price tracking for trail calculation
3. **Market Order Validation**: Price sanity checks

### AOE Price Check Code
```python
# AOE reads price from Redis
price_key = f"coindcx_futures:{symbol}"
price_data = redis_client.hgetall(price_key)
current_price = float(price_data[b'ltp'])

# Check if stale (>5 seconds old)
timestamp = int(price_data[b'timestamp'])
if time.time() - timestamp > 5:
    logger.warning(f"Stale price data for {symbol}")
```

---

## Monitoring Integration

The Monitoring Service (Port 8002) checks price data freshness:

```python
# Monitoring checks timestamp freshness
for key in ["coindcx_futures:BTC", "bybit_spot:ETH"]:
    timestamp = redis_client.hget(key, "timestamp")
    age_seconds = time.time() - int(timestamp)

    if age_seconds > 300:  # 5 minutes
        alert_manager.send_p2_alert(f"Stale price data: {key}")
```

---

## Troubleshooting

### "No price data in Redis"
1. Check if service is running: `curl http://localhost:8080`
2. Verify WebSocket connection in dashboard
3. Check logs: `tail -f logs/bybit_spot.log`
4. Verify Redis is running: `redis-cli ping`

### "Stale prices"
1. Check WebSocket connection status in dashboard
2. Look for reconnection messages in logs
3. Verify exchange API is not rate-limited

### "Connection keeps dropping"
1. Check network connectivity
2. Verify exchange WebSocket URL is correct
3. Check for exchange maintenance announcements

---

## Service Features Matrix

### Spot Services
| Feature | Bybit Spot | CoinDCX Spot | Delta Spot | HyperLiquid Spot |
|---------|------------|--------------|------------|------------------|
| LTP | ✅ | ✅ | ✅ | ✅ |
| Orderbook | ✅ (50 levels) | ✅ (20 levels) | ✅ (50 levels) | ✅ (50 levels) |
| Trades | ✅ (50 trades) | ✅ (50 trades) | ✅ (50 trades) | ✅ (50 trades) |
| Spread/Mid | ✅ | ✅ | ✅ | ✅ |
| TTL | 60s | 60s | 60s | 60s |
| Auto-Reconnect | ✅ | ✅ | ✅ | ✅ |

### Futures/Perpetual Services
| Feature | Delta Futures | CoinDCX Futures | HyperLiquid Futures |
|---------|---------------|-----------------|---------------------|
| LTP | ✅ | ✅ | ✅ |
| Orderbook | ✅ (50 levels) | ❌ | ✅ (50 levels) |
| Trades | ✅ (50 trades) | ❌ | ✅ (50 trades) |
| Funding Rate | ✅ | ✅ | ❌ |
| TTL | 60s | 60s | 60s |
| Auto-Reconnect | ✅ | ✅ | ✅ |

---

**Last Updated**: January 2026
**Version**: 2.2.1 (Backwards compatibility for HyperLiquid Futures Redis keys)
**Part of**: Scalper Bot Ecosystem

**Migration Note**: HyperLiquid Perpetual service now writes to both new (`hyperliquid_futures*`) and legacy (`hyperliquid_perp*`) Redis keys for backwards compatibility. Legacy key writes can be disabled via `write_legacy_keys: false` in config once downstream consumers have migrated.
