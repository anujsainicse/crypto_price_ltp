# Implementation Plan: Add Order Book & Trades to Crypto Price LTP

**Date:** 2025-01-21
**Author:** Anuj Saini
**Status:** Ready for Implementation

---

## Objective

Extend the existing `BybitSpotService` to subscribe to order book and trades channels on the same WebSocket connection, storing data in separate Redis keys.

**Scope:** Bybit Spot only (prove pattern first, then expand to other exchanges)

---

## Background Context

### Current Architecture

The `crypto_price_ltp` service streams real-time price data from multiple exchanges and stores it in Redis. The existing `BybitSpotService`:

- Connects to `wss://stream.bybit.com/v5/public/spot`
- Subscribes to `tickers.{symbol}` channels for LTP (Last Traded Price)
- Stores data in Redis with prefix `bybit_spot:{symbol}`
- Uses the `BaseService` pattern with auto-reconnection

### Key Files

| File | Purpose |
|------|---------|
| `services/bybit_s/spot_service.py` | Bybit Spot WebSocket service |
| `config/exchanges.yaml` | Exchange and service configuration |
| `core/base_service.py` | Abstract base class for all services |
| `core/redis_client.py` | Redis connection and storage methods |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              BybitSpotService (Extended)                        │
│              wss://stream.bybit.com/v5/public/spot              │
├─────────────────────────────────────────────────────────────────┤
│   Subscriptions per symbol (e.g., BTCUSDT):                     │
│   ├── tickers.BTCUSDT        (existing - LTP)                   │
│   ├── orderbook.50.BTCUSDT   (new - 50 levels)                  │
│   └── publicTrade.BTCUSDT    (new - live trades)                │
│                                                                 │
│   _handle_message() routes by topic prefix:                     │
│   ├── tickers.*      → _process_ticker_update()   [existing]    │
│   ├── orderbook.*    → _process_orderbook_update() [new]        │
│   └── publicTrade.*  → _process_trade_update()     [new]        │
└─────────────────────────────────────────────────────────────────┘
             │                  │                 │
             ▼                  ▼                 ▼
      bybit_spot:BTC    bybit_spot_ob:BTC   bybit_spot_trades:BTC
      {ltp, vol, ...}   {bids, asks, ...}   {trades: [...]}
```

**Key Design Decisions:**
1. **Single WebSocket** - All 3 data types on one connection (efficient)
2. **Separate Redis keys** - Independent TTLs, cleaner querying
3. **Config-driven** - Enable/disable via YAML without code changes
4. **50 levels** - Bybit supports 1, 50, 200, 1000 depths
5. **50 recent trades** - Rolling buffer with automatic eviction

---

## Redis Data Structures

### Order Book (`bybit_spot_ob:{symbol}`)

```json
{
  "bids": "[[45000.50, 1.5], [45000.00, 2.3], ...]",
  "asks": "[[45001.00, 1.2], [45001.50, 0.9], ...]",
  "spread": "0.50",
  "mid_price": "45000.75",
  "update_id": "1234567890",
  "timestamp": "2025-01-21T10:30:45Z"
}
```

- 50 levels each side, stored as JSON arrays `[[price, qty], ...]`
- `spread` and `mid_price` pre-calculated for AOE fast access
- Bids sorted descending, asks sorted ascending

### Trades (`bybit_spot_trades:{symbol}`)

```json
{
  "trades": "[{\"p\":45000.5,\"q\":0.5,\"s\":\"Buy\",\"t\":1705834245000,\"id\":\"abc123\"}, ...]",
  "count": "50",
  "timestamp": "2025-01-21T10:30:45Z"
}
```

- Last 50 trades as JSON array (FIFO buffer)
- Compact keys: `p`=price, `q`=qty, `s`=side, `t`=time, `id`=trade_id

---

## Implementation Steps

### Step 1: Update BybitSpotService Constructor

**File:** `services/bybit_s/spot_service.py`

Add new imports and config parsing:

```python
# Add to imports
from collections import deque
from typing import Dict

# Add to __init__ method after existing config parsing:

def __init__(self, config: dict):
    # ... existing code ...

    # Order book config
    self.orderbook_enabled = config.get('orderbook_enabled', False)
    self.orderbook_depth = config.get('orderbook_depth', 50)
    self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'bybit_spot_ob')

    # Trades config
    self.trades_enabled = config.get('trades_enabled', False)
    self.trades_limit = config.get('trades_limit', 50)
    self.trades_redis_prefix = config.get('trades_redis_prefix', 'bybit_spot_trades')

    # In-memory state for order book deltas
    self._orderbooks: Dict[str, dict] = {}  # symbol -> {bids: {price: qty}, asks: {price: qty}}

    # In-memory buffer for recent trades
    self._trades: Dict[str, deque] = {}  # symbol -> deque(maxlen=trades_limit)
```

---

### Step 2: Update Subscription Method

**File:** `services/bybit_s/spot_service.py`

Rename `_subscribe_to_tickers()` to `_subscribe_to_channels()` and extend:

```python
async def _subscribe_to_channels(self):
    """Subscribe to ticker, order book, and trade channels for configured symbols."""
    if not self.websocket:
        return

    for symbol in self.symbols:
        # Build list of channels to subscribe
        channels = [f"tickers.{symbol}"]

        if self.orderbook_enabled:
            channels.append(f"orderbook.{self.orderbook_depth}.{symbol}")

        if self.trades_enabled:
            channels.append(f"publicTrade.{symbol}")

        # Bybit allows multiple args in one subscribe message
        subscribe_msg = {
            "op": "subscribe",
            "args": channels
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.logger.info(f"Subscribed to {len(channels)} channels for {symbol}: {channels}")
```

**Also update the call site:**
- In `_connect_and_stream()`, change `await self._subscribe_to_tickers()` to `await self._subscribe_to_channels()`

---

### Step 3: Update Message Handler

**File:** `services/bybit_s/spot_service.py`

Extend `_handle_message()` to route new topics:

```python
async def _handle_message(self, message: str):
    """Handle incoming WebSocket message."""
    try:
        data = json.loads(message)

        # Handle subscription confirmation
        if data.get('op') == 'subscribe':
            self.logger.debug(f"Subscription confirmed: {data}")
            return

        # Route by topic prefix
        topic = data.get('topic', '')

        if topic.startswith('tickers.'):
            await self._process_ticker_update(data)
        elif topic.startswith('orderbook.'):
            await self._process_orderbook_update(data)
        elif topic.startswith('publicTrade.'):
            await self._process_trade_update(data)

    except json.JSONDecodeError as e:
        self.logger.error(f"Failed to parse message: {e}")
    except Exception as e:
        self.logger.error(f"Error processing message: {e}")
```

---

### Step 4: Implement Order Book Handler

**File:** `services/bybit_s/spot_service.py`

Add new method to handle order book updates:

```python
async def _process_orderbook_update(self, data: dict):
    """Process order book snapshot or delta update.

    Bybit sends:
    - type: 'snapshot' with full order book on first connect
    - type: 'delta' with incremental updates after

    Args:
        data: WebSocket message data
    """
    try:
        topic = data.get('topic', '')
        symbol = topic.split('.')[-1]  # orderbook.50.BTCUSDT -> BTCUSDT
        base_coin = symbol.replace('USDT', '')

        msg_type = data.get('type')  # 'snapshot' or 'delta'
        ob_data = data.get('data', {})

        if msg_type == 'snapshot':
            # Full snapshot - replace local state
            self._orderbooks[symbol] = {
                'bids': {float(b[0]): float(b[1]) for b in ob_data.get('b', [])},
                'asks': {float(a[0]): float(a[1]) for a in ob_data.get('a', [])}
            }
            self.logger.debug(f"Received order book snapshot for {symbol}")

        elif msg_type == 'delta':
            # Delta update - apply changes to local state
            if symbol not in self._orderbooks:
                self.logger.warning(f"Received delta before snapshot for {symbol}, ignoring")
                return

            # Apply bid updates
            for bid in ob_data.get('b', []):
                price, qty = float(bid[0]), float(bid[1])
                if qty == 0:
                    self._orderbooks[symbol]['bids'].pop(price, None)
                else:
                    self._orderbooks[symbol]['bids'][price] = qty

            # Apply ask updates
            for ask in ob_data.get('a', []):
                price, qty = float(ask[0]), float(ask[1])
                if qty == 0:
                    self._orderbooks[symbol]['asks'].pop(price, None)
                else:
                    self._orderbooks[symbol]['asks'][price] = qty

        # Store in Redis
        await self._store_orderbook(symbol, base_coin, ob_data.get('u'))

    except Exception as e:
        self.logger.error(f"Error processing order book update: {e}")


async def _store_orderbook(self, symbol: str, base_coin: str, update_id):
    """Build sorted order book and store in Redis.

    Args:
        symbol: Full symbol (e.g., BTCUSDT)
        base_coin: Base coin (e.g., BTC)
        update_id: Sequence number from exchange
    """
    ob = self._orderbooks.get(symbol)
    if not ob:
        return

    # Sort: bids descending (highest first), asks ascending (lowest first)
    bids = sorted(ob['bids'].items(), key=lambda x: x[0], reverse=True)[:50]
    asks = sorted(ob['asks'].items(), key=lambda x: x[0])[:50]

    # Calculate spread and mid price for quick AOE access
    best_bid = bids[0][0] if bids else 0
    best_ask = asks[0][0] if asks else 0
    spread = best_ask - best_bid if best_bid and best_ask else 0
    mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0

    # Store in Redis hash
    redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
    self.redis_client.client.hset(redis_key, mapping={
        'bids': json.dumps([[p, q] for p, q in bids]),
        'asks': json.dumps([[p, q] for p, q in asks]),
        'spread': str(round(spread, 8)),
        'mid_price': str(round(mid_price, 8)),
        'update_id': str(update_id) if update_id else '',
        'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    })

    self.logger.debug(
        f"Updated {base_coin} order book: spread=${spread:.2f}, "
        f"mid=${mid_price:.2f}, {len(bids)} bids, {len(asks)} asks"
    )
```

---

### Step 5: Implement Trades Handler

**File:** `services/bybit_s/spot_service.py`

Add new method to handle trade updates:

```python
async def _process_trade_update(self, data: dict):
    """Process public trade updates.

    Bybit sends trade data with fields:
    - p: price
    - v: volume/quantity
    - S: side (Buy/Sell)
    - T: timestamp (ms)
    - i: trade id

    Args:
        data: WebSocket message data
    """
    try:
        topic = data.get('topic', '')
        symbol = topic.split('.')[-1]  # publicTrade.BTCUSDT -> BTCUSDT
        base_coin = symbol.replace('USDT', '')

        trades_data = data.get('data', [])

        # Initialize deque if needed
        if symbol not in self._trades:
            self._trades[symbol] = deque(maxlen=self.trades_limit)

        # Append new trades (Bybit sends array of trades)
        for trade in trades_data:
            self._trades[symbol].append({
                'p': float(trade.get('p', 0)),       # price
                'q': float(trade.get('v', 0)),       # quantity (v = volume in Bybit)
                's': trade.get('S', ''),             # side: Buy/Sell
                't': trade.get('T', 0),              # timestamp (ms)
                'id': trade.get('i', '')             # trade id
            })

        # Store in Redis
        redis_key = f"{self.trades_redis_prefix}:{base_coin}"
        self.redis_client.client.hset(redis_key, mapping={
            'trades': json.dumps(list(self._trades[symbol])),
            'count': str(len(self._trades[symbol])),
            'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        })

        self.logger.debug(
            f"Updated {base_coin} trades: {len(self._trades[symbol])} trades in buffer"
        )

    except Exception as e:
        self.logger.error(f"Error processing trade update: {e}")
```

---

### Step 6: Update Configuration

**File:** `config/exchanges.yaml`

Update the Bybit spot configuration:

```yaml
bybit:
  name: "Bybit"
  enabled: true
  services:
    spot:
      enabled: true
      auto_start: true
      websocket_url: "wss://stream.bybit.com/v5/public/spot"
      symbols:
        - "BTCUSDT"
        - "ETHUSDT"
        - "SOLUSDT"
        - "BNBUSDT"
        - "DOGEUSDT"
        - "MNTUSDT"
        - "HYPEUSDT"
      reconnect_interval: 5
      max_reconnect_attempts: 10
      redis_prefix: "bybit_spot"

      # Order Book Settings
      orderbook_enabled: true
      orderbook_depth: 50
      orderbook_redis_prefix: "bybit_spot_ob"

      # Trades Settings
      trades_enabled: true
      trades_limit: 50
      trades_redis_prefix: "bybit_spot_trades"
```

---

### Step 7: Add Redis Helper Methods (Optional)

**File:** `core/redis_client.py`

Add convenience methods for reading order book and trades:

```python
def get_orderbook(self, key: str) -> Optional[dict]:
    """Get order book data from Redis.

    Args:
        key: Redis key (e.g., 'bybit_spot_ob:BTC')

    Returns:
        Order book dict with bids, asks, spread, mid_price, etc.
    """
    data = self.client.hgetall(key)
    if not data:
        return None

    return {
        'bids': json.loads(data.get(b'bids', b'[]')),
        'asks': json.loads(data.get(b'asks', b'[]')),
        'spread': float(data.get(b'spread', b'0')),
        'mid_price': float(data.get(b'mid_price', b'0')),
        'update_id': data.get(b'update_id', b'').decode(),
        'timestamp': data.get(b'timestamp', b'').decode()
    }


def get_trades(self, key: str) -> Optional[list]:
    """Get recent trades from Redis.

    Args:
        key: Redis key (e.g., 'bybit_spot_trades:BTC')

    Returns:
        List of trade dicts with p, q, s, t, id fields
    """
    data = self.client.hgetall(key)
    if not data:
        return None

    return json.loads(data.get(b'trades', b'[]'))
```

---

## Files to Modify Summary

| File | Action | Changes |
|------|--------|---------|
| `services/bybit_s/spot_service.py` | Modify | Add imports, config, orderbook/trades handlers |
| `config/exchanges.yaml` | Modify | Add orderbook/trades config options |
| `core/redis_client.py` | Modify (optional) | Add helper methods |

---

## Verification Steps

### 1. Start the Service

```bash
cd ~/claude/crypto_price_ltp
source venv/bin/activate
python main.py
```

### 2. Check Redis Keys Exist

```bash
redis-cli keys "bybit_spot*"
```

Expected output:
```
1) "bybit_spot:BTC"
2) "bybit_spot:ETH"
3) "bybit_spot_ob:BTC"
4) "bybit_spot_ob:ETH"
5) "bybit_spot_trades:BTC"
6) "bybit_spot_trades:ETH"
... (for all configured symbols)
```

### 3. Verify Order Book Data

```bash
redis-cli hgetall bybit_spot_ob:BTC
```

Should show: `bids`, `asks`, `spread`, `mid_price`, `update_id`, `timestamp`

### 4. Verify Trades Data

```bash
redis-cli hget bybit_spot_trades:BTC trades
```

Should show JSON array of up to 50 trades.

### 5. Check Service Logs

```bash
tail -f logs/bybit-spot.log
```

Look for:
- "Subscribed to 3 channels for BTCUSDT"
- "Received order book snapshot"
- "Updated BTC order book"
- "Updated BTC trades"

### 6. Web Dashboard

Navigate to http://localhost:8080 and verify:
- Bybit Spot service shows as running
- No error indicators

---

## Bybit WebSocket API Reference

**Order Book Channel:** `orderbook.{depth}.{symbol}`
- Depths: 1, 50, 200, 1000
- First message: `type: 'snapshot'`
- Subsequent: `type: 'delta'`
- Fields: `b` (bids), `a` (asks), `u` (update_id)

**Trades Channel:** `publicTrade.{symbol}`
- Fields: `p` (price), `v` (volume), `S` (side), `T` (timestamp), `i` (trade_id)

**Documentation:** https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook

---

## Future Expansion

Once Bybit is working, replicate the pattern for other exchanges:

1. **CoinDCX Futures** - Socket.IO protocol, different message format
2. **Delta Futures** - WebSocket, different subscription format
3. **HyperLiquid** - WebSocket, different API structure

Each exchange will need its own `_process_orderbook_update()` and `_process_trade_update()` implementations.

---

## Notes for Implementer

- The order book uses in-memory state because Bybit sends snapshot + deltas
- Trades use a `deque(maxlen=50)` for automatic FIFO eviction
- Pre-calculate `spread` and `mid_price` for AOE performance (avoids JSON parsing on every check)
- Use compact field names (`p`, `q`, `s`, `t`) to reduce Redis memory
- The existing `_process_ticker_update()` method stays unchanged
