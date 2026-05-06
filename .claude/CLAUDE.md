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
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ │
│  │   Bybit    │ │  Binance   │ │  CoinDCX   │ │   Delta    │ │ HyperLiquid│ │
│  │  WebSocket │ │  WebSocket │ │ Socket.IO  │ │  WebSocket │ │  WebSocket │ │
│  ├────────────┤ ├────────────┤ ├────────────┤ ├────────────┤ ├────────────┤ │
│  │Spot+Testnet│ │    Spot    │ │Spot+Futures│ │Spt+Fut+Opt│ │ Spot+Perp  │ │
│  │LTP/OB/Trd │ │LTP/OB/Trd │ │LTP/OB/Trd │ │LTP/OB/Trd │ │LTP/OB/Trd │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ │
│        │              │              │              │              │         │
│        └──────────────┴──────────────┴──────────────┴──────────────┘         │
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
| **Bybit** | `bybit_spot` | Spot | BTC, ETH, SOL, BNB, DOGE, MNT, HYPE | LTP (kline.1) + OHLCV + Orderbook + Trades |
| **Bybit** | `bybit_spot_testnet` | Spot (Testnet) | BTC, ETH, SOL, BNB, DOGE, MNT, HYPE | LTP (kline.1) + OHLCV + Orderbook + Trades |
| **Bybit** | `bybit_futures_orderbook` | Futures | BTC, ETH, SOL, BNB, DOGE | Orderbook only |
| **Bybit** | `bybit_options` | Options | All available (dynamic) | LTP + Greeks + IV + Orderbook + Trades |
| **Binance** | `binance_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **Binance** | `binance_futures` | Futures | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades + Funding Rate |
| **Binance** | `binance_options` | Options | BTC, ETH (all strikes) | LTP + Greeks + IV |
| **CoinDCX** | `coindcx_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | Orderbook + Trades (LTP from mid_price) |
| **CoinDCX** | `coindcx_futures_rest` | Futures | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades + Funding Rate |
| **Delta** | `delta_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | Orderbook + Trades (LTP from mid_price) |
| **Delta** | `delta_futures` | Futures | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades + Funding Rate |
| **Delta** | `delta_options` | Options | BTC, ETH (all strikes) | LTP + Greeks + Orderbook + Trades |
| **HyperLiquid** | `hyperliquid_spot` | Spot | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |
| **HyperLiquid** | `hyperliquid_futures` | Perpetual | BTC, ETH, SOL, BNB, DOGE | LTP + Orderbook + Trades |

**Total Active Services**: 14

**Notes**:
- CoinDCX Spot and Delta Spot do not have dedicated LTP ticker channels. Use the `mid_price` field from the orderbook hash for current price.
- CoinDCX Futures uses REST API polling (not WebSocket) for better stability.
- Bybit Futures provides orderbook data only (no LTP/trades service).
- Binance Spot uses combined WebSocket streams (miniTicker + depth20 + trade) on a single connection. Orderbook is 20 levels (Binance WS max).
- Binance Futures uses combined WebSocket streams on `fstream.binance.com` (miniTicker + depth20@100ms + aggTrade + markPrice@1s). Funding rate is cached from markPrice and merged into the LTP hash on each miniTicker update.

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
# LTP Keys (full exchange symbol as key suffix)
bybit_spot:BTCUSDT
binance_spot:BTCUSDT
binance_futures:BTCUSDT
coindcx_spot:BTC_USDT
coindcx_futures:BTC_USDT
delta_spot:BTCUSD
delta_futures:BTCUSD
hyperliquid_spot:BTC
hyperliquid_futures:ETH

# Options LTP Keys (full symbol as key)
bybit_options:BTC-25DEC26-70000-P-USDT
bybit_options:ETH-28FEB26-4000-C-USDT
delta_options:C-BTC-106000-241220

# Orderbook Keys
bybit_spot_ob:BTCUSDT
binance_spot_ob:BTCUSDT
binance_futures_ob:BTCUSDT
coindcx_spot_ob:BTC_USDT
coindcx_futures_ob:BTC_USDT
delta_spot_ob:BTCUSD
delta_futures_ob:BTCUSD
delta_options_ob:C-BTC-106000-241220
hyperliquid_spot_ob:BTC
hyperliquid_futures_ob:BTC

# Trades Keys
bybit_spot_trades:BTCUSDT
binance_spot_trades:BTCUSDT
binance_futures_trades:BTCUSDT
coindcx_spot_trades:BTC_USDT
coindcx_futures_trades:BTC_USDT
delta_spot_trades:BTCUSD
delta_futures_trades:BTCUSD
delta_options_trades:C-BTC-106000-241220
hyperliquid_spot_trades:BTC
hyperliquid_futures_trades:BTC
```

### Hash Fields

**LTP/Ticker Data (generic — Binance / CoinDCX / Delta / HyperLiquid):**
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

**LTP/OHLCV Data (Bybit Spot + Bybit Spot Testnet — sourced from `kline.1.{symbol}`):**
```json
{
  "ltp": "81895.6",
  "timestamp": "1778058652",
  "original_symbol": "BTCUSDT",
  "open": "81854.9",
  "high": "81896.6",
  "low": "81854.9",
  "close": "81895.6",
  "volume": "6.379417"
}
```
- `ltp` mirrors `close` (last trade price within the in-progress 1-minute candle).
- `timestamp` is the candle's own update time (Bybit-side, ms→s converted).
- `volume` is **per-minute** volume within the bucket, not 24h.
- 24h fields (`volume_24h`, `high_24h`, `low_24h`, `price_change_percent`) are **not provided** — kline channel does not expose them. If 24h stats are needed in the future, add a REST poller for `/v5/market/tickers`.

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
- Orderbook: 50 levels each side (bids descending, asks ascending). Binance Spot: 20 levels (WS max).
- Trades: Last 50 trades in FIFO buffer
- All keys have 60-second TTL (configurable via `redis_ttl`)

**Options Data (Bybit/Delta):**
```json
{
  "ltp": "1250.50",
  "mark_price": "1248.20",
  "bid": "1249.00",
  "ask": "1251.00",
  "bid_size": "10.5",
  "ask_size": "8.2",
  "delta": "-0.35",
  "gamma": "0.00012",
  "vega": "8.50",
  "theta": "-2.10",
  "iv": "0.65",
  "bid_iv": "0.62",
  "ask_iv": "0.68",
  "open_interest": "5420",
  "volume_24h": "1234.56",
  "turnover_24h": "6789012.34",
  "high_24h": "1280.00",
  "low_24h": "1200.00",
  "price_change_percent": "2.5",
  "underlying_price": "68000.00",
  "option_type": "PUT",
  "underlying": "BTC",
  "strike_price": "70000",
  "expiry_date": "25DEC26",
  "timestamp": "1704628800",
  "original_symbol": "BTC-25DEC26-70000-P-USDT"
}
```

---

## Reading Data (Client Code)

### Reading LTP/Ticker

```python
import redis
import json
from datetime import datetime

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# Get CoinDCX BTC futures price
data = redis_client.hgetall("coindcx_futures:BTC_USDT")
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
ob_data = redis_client.hgetall("bybit_spot_ob:BTCUSDT")
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
trades_data = redis_client.hgetall("bybit_spot_trades:BTCUSDT")
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
| `manager.py` | Service lifecycle management (registers all 14 services) |
| `core/redis_client.py` | Redis connection + orderbook/trades storage methods |
| `core/base_service.py` | Abstract base class for all services |
| `config/settings.py` | Global settings (Redis, logging) |
| `config/exchanges.yaml` | Exchange and service configuration |

### Service Files

| Service | File | Features |
|---------|------|----------|
| Bybit Spot | `services/bybit_s/spot_service.py` | LTP (kline.1.close) + OHLCV + Orderbook + Trades |
| Bybit Testnet | `services/bybit_spot_testnet/spot_testnet_service.py` | LTP (kline.1.close) + OHLCV + Orderbook + Trades |
| Bybit Futures OB | `services/bybit_f/futures_orderbook_service.py` | Orderbook only |
| Bybit Options | `services/bybit_o/options_service.py` | LTP + Greeks + IV + Orderbook + Trades (dynamic discovery) |
| Binance Spot | `services/binance_s/spot_service.py` | LTP + Orderbook (20 levels) + Trades |
| Binance Futures | `services/binance_f/futures_service.py` | LTP + Orderbook (20 levels) + Trades + Funding Rate |
| CoinDCX Spot | `services/coindcx_s/spot_service.py` | Orderbook + Trades (Socket.IO) |
| CoinDCX Futures | `services/coindcx_f/futures_rest_service.py` | LTP + Orderbook + Trades + Funding (REST) |
| Delta Spot | `services/delta_s/spot_service.py` | Orderbook + Trades |
| Delta Futures | `services/delta_f/futures_ltp_service.py` | LTP + Orderbook + Trades + Funding |
| Delta Options | `services/delta_o/options_service.py` | LTP + Greeks + Orderbook + Trades |
| HyperLiquid Spot | `services/hyperliquid_s/spot_service.py` | LTP + Orderbook + Trades |
| HyperLiquid Futures | `services/hyperliquid_p/perpetual_service.py` | LTP + Orderbook + Trades |

---

## Integration with AOE

The Advanced Order Engine (AOE) uses Crypto Price LTP for:

1. **Price Monitoring**: SL/TP trigger price checks (100ms polling)
2. **Trailing Stops**: Current price tracking for trail calculation
3. **Market Order Validation**: Price sanity checks

### AOE Price Check Code
```python
# AOE reads price from Redis (symbol = full exchange symbol, e.g. "BTC_USDT")
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
for key in ["coindcx_futures:BTC_USDT", "bybit_spot:ETHUSDT"]:
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
| Feature | Bybit Spot | Binance Spot | CoinDCX Spot | Delta Spot | HyperLiquid Spot |
|---------|------------|--------------|--------------|------------|------------------|
| LTP | ✅ | ✅ | ⚠️ (from mid_price) | ⚠️ (from mid_price) | ✅ |
| Orderbook | ✅ (50 levels) | ✅ (20 levels) | ✅ (20 levels) | ✅ (50 levels) | ✅ (50 levels) |
| Trades | ✅ (50 trades) | ✅ (50 trades) | ✅ (50 trades) | ✅ (50 trades) | ✅ (50 trades) |
| Spread/Mid | ✅ | ✅ | ✅ | ✅ | ✅ |
| TTL | 60s | 60s | 60s | 60s | 60s |
| Connection | WebSocket | WebSocket | Socket.IO | WebSocket | WebSocket |
| Auto-Reconnect | ✅ | ✅ | ✅ | ✅ | ✅ |

**Note**: CoinDCX Spot and Delta Spot do not have dedicated LTP ticker channels. Read `mid_price` from the orderbook hash.

### Futures/Perpetual Services
| Feature | Bybit Futures OB | Binance Futures | Delta Futures | CoinDCX Futures REST | HyperLiquid Futures |
|---------|------------------|-----------------|---------------|----------------------|---------------------|
| LTP | ❌ | ✅ | ✅ | ✅ (1s polling) | ✅ |
| Orderbook | ✅ (50 levels) | ✅ (20 levels) | ✅ (50 levels) | ✅ (50 levels, 1s polling) | ✅ (50 levels) |
| Trades | ❌ | ✅ (50 trades, aggTrade) | ✅ (50 trades) | ✅ (50 trades, 2s polling) | ✅ (50 trades) |
| Funding Rate | ❌ | ✅ (markPrice stream) | ✅ | ✅ (30min polling) | ❌ |
| TTL | 60s | 60s | 60s | 60s | 60s |
| Connection Type | WebSocket | WebSocket | WebSocket | REST API | WebSocket |
| Auto-Reconnect | ✅ | ✅ | ✅ | ✅ (exponential backoff) | ✅ |

### Options Services
| Feature | Bybit Options | Delta Options |
|---------|---------------|---------------|
| LTP | ✅ | ✅ |
| Greeks (delta/gamma/vega/theta) | ✅ | ✅ |
| IV (Implied Volatility) | ✅ | ✅ |
| Orderbook | ✅ (25 levels) | ✅ (50 levels) |
| Trades | ✅ (50 trades) | ✅ (50 trades) |
| Dynamic Symbol Discovery | ✅ | ✅ |
| TTL | 60s | 60s |
| Connection Type | WebSocket | WebSocket |
| Auto-Reconnect | ✅ | ✅ |

---

**Last Updated**: May 2026
**Version**: 2.9.0 (Bybit Spot + Testnet migrated to `kline.1.{symbol}` for LTP; OHLCV exposed in Redis hash)
**Part of**: Scalper Bot Ecosystem

**Bybit Spot Note**: As of v2.9.0, Bybit Spot and Bybit Spot Testnet subscribe to `kline.1.{symbol}` instead of `tickers.{symbol}`. The Redis hash `bybit_spot:{symbol}` now exposes per-candle OHLCV (`open`, `high`, `low`, `close`, `volume`) and `ltp` is sourced from `close`. The 24h fields (`volume_24h`, `high_24h`, `low_24h`, `price_change_percent`) are no longer written — kline does not provide them. Symbol is parsed from the topic string since the kline payload does not include it.

**CoinDCX Futures Note**: The REST-based service (`futures_rest_service.py`) provides LTP, orderbook, trades, and funding rate data via REST API polling for better stability than WebSocket.

**HyperLiquid Note**: The Perpetual service writes to both new (`hyperliquid_futures*`) and legacy (`hyperliquid_perp*`) Redis keys for backwards compatibility. Legacy key writes can be disabled via `write_legacy_keys: false` in config once downstream consumers have migrated.

**Spot LTP Note**: CoinDCX Spot and Delta Spot do not have dedicated LTP channels. Use the `mid_price` field from the orderbook hash (`coindcx_spot_ob:BTC_USDT`, `delta_spot_ob:BTCUSD`) for current price data.

**Binance Spot Note**: Uses combined WebSocket streams (`wss://stream.binance.com:9443/stream?streams=...`) for miniTicker (LTP), partial book depth (20-level orderbook snapshots at 100ms), and trades on a single connection. Binance auto-disconnects after 24 hours; the reconnection loop handles this transparently.

**Binance Futures Note**: Uses combined WebSocket streams on `wss://fstream.binance.com` (`@miniTicker` + `@depth20@100ms` + `@aggTrade` + `@markPrice@1s`) on a single connection for 5 symbols (20 streams total). Funding rate is received via `@markPrice@1s`, cached in memory, and merged into the LTP Redis hash on each `@miniTicker` update. Uses `@aggTrade` (not `@trade`) — futures depth payload includes the `s` (symbol) field directly, unlike spot depth which omits it.
