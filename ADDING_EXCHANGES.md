# Adding New Exchanges to price_ltp

This guide shows you how to add a new exchange to the price_ltp system.

## Overview

The system is modular - each exchange is a separate, independent service. Adding a new exchange involves:

1. Create service directory
2. Write service code
3. Add configuration
4. Register in manager

**Time required**: ~15 minutes

---

## Step-by-Step Guide

### 1. Create Service Directory

```bash
cd /Users/anujsainicse/claude/price_ltp
mkdir -p services/EXCHANGE_f  # Replace EXCHANGE with exchange name
```

**Naming Convention:**
- `exchange_s` - For Spot markets (e.g., `bybit_s`)
- `exchange_f` - For Futures markets (e.g., `delta_f`, `coindcx_f`)

### 2. Create `__init__.py`

File: `services/EXCHANGE_f/__init__.py`

```python
"""EXCHANGE services."""

from .futures_ltp_service import EXCHANGEFuturesLTPService

__all__ = ['EXCHANGEFuturesLTPService']
```

### 3. Create Service File

File: `services/EXCHANGE_f/futures_ltp_service.py`

Use one of the existing services as a template:
- **WebSocket**: Copy from `services/delta_f/futures_ltp_service.py`
- **REST API**: Copy from `services/coindcx_f/futures_rest_service.py`

**Key things to customize:**

```python
class EXCHANGEFuturesLTPService(BaseService):
    def __init__(self, config: dict):
        super().__init__("EXCHANGE-Futures-LTP", config)
        self.ws_url = config.get('websocket_url', 'wss://api.exchange.com/ws')
        self.symbols = config.get('symbols', [])
        self.redis_prefix = config.get('redis_prefix', 'exchange_futures')

    async def _subscribe_to_symbols(self):
        # Customize subscription message format for your exchange
        subscribe_msg = {
            "type": "subscribe",
            "channel": "ticker",
            "symbols": self.symbols
        }
        await self.websocket.send(json.dumps(subscribe_msg))

    async def _process_ticker_update(self, data: dict):
        # Customize data parsing for your exchange
        price = data.get('price')  # Adjust based on exchange format
        symbol = data.get('symbol')

        # Store in Redis
        base_coin = self._extract_base_coin(symbol)
        redis_key = f"{self.redis_prefix}:{base_coin}"

        self.redis_client.set_price_data(
            key=redis_key,
            price=float(price),
            symbol=symbol,
            additional_data={'your_custom_fields': 'here'}
        )
```

### 4. Add Exchange Configuration

File: `config/exchanges.yaml`

```yaml
exchange_name:
  name: "Exchange Name"
  enabled: true
  services:
    futures_ltp:
      enabled: true
      websocket_url: "wss://api.exchange.com/ws"
      symbols:
        - "BTCUSD"
        - "ETHUSDT"
        - "SOLUSDT"
      reconnect_interval: 5
      max_reconnect_attempts: 10
      redis_prefix: "exchange_futures"
```

### 5. Register in Manager

File: `manager.py`

**Step 5a**: Add import at the top:

```python
from services.exchange_f import EXCHANGEFuturesLTPService
```

**Step 5b**: Add to `_load_exchange_services()` method:

```python
elif exchange == 'exchange_name':  # Must match YAML key
    # EXCHANGE Futures LTP Service
    ltp_config = services_config.get('futures_ltp', {})
    if ltp_config.get('enabled', False):
        self.services.append(EXCHANGEFuturesLTPService(ltp_config))
        self.logger.info("✓ EXCHANGE Futures LTP Service loaded")
```

### 6. Test the Service

```bash
# Test standalone first
cd /Users/anujsainicse/claude/price_ltp
python -m services.exchange_f.futures_ltp_service

# If standalone works, test with manager
python manager.py
```

### 7. Verify Data

```bash
# Check if data is being collected
redis-cli KEYS "exchange_futures:*"

# Check specific coin
redis-cli HGETALL exchange_futures:BTC
```

---

## Real Example: Delta Exchange

Here's how Delta Exchange was added to the system:

### 1. Created Directory
```bash
mkdir -p services/delta_f
```

### 2. Created Files
```
services/delta_f/
├── __init__.py
└── futures_rest_service.py
```

### 3. Added Config
```yaml
delta:
  name: "Delta Exchange"
  enabled: true
  services:
    futures_ltp:
      enabled: true
      websocket_url: "wss://socket.delta.exchange"
      symbols:
        - "BTCUSD"
        - "ETHUSD"
        - "SOLUSD"
      redis_prefix: "delta_futures"
```

### 4. Registered in Manager
```python
# Import
from services.delta_f import DeltaFuturesLTPService

# Register
elif exchange == 'delta':
    ltp_config = services_config.get('futures_ltp', {})
    if ltp_config.get('enabled', False):
        self.services.append(DeltaFuturesLTPService(ltp_config))
        self.logger.info("✓ Delta Futures LTP Service loaded")
```

### 5. Result
```
✅ Delta-Futures-LTP Service loaded
✅ WebSocket connected successfully
✅ Data flowing to Redis (delta_futures:BTC, etc.)
```

---

## Common WebSocket Patterns

### Pattern 1: Standard WebSocket (Delta, Bybit)

```python
import websockets

async with websockets.connect(self.ws_url) as websocket:
    subscribe_msg = {"type": "subscribe", "channel": "ticker"}
    await websocket.send(json.dumps(subscribe_msg))

    async for message in websocket:
        data = json.loads(message)
        # Process data
```

### Pattern 2: Socket.IO (CoinDCX)

```python
import socketio

self.sio = socketio.AsyncClient()
await self.sio.connect(self.ws_url)

@self.sio.on('new-trade')
async def handle_trade(data):
    # Process data
```

### Pattern 3: REST API Polling

```python
import aiohttp

async with aiohttp.ClientSession() as session:
    async with session.get(self.api_url) as response:
        data = await response.json()
        # Process data
```

---

## Data Storage Format

All exchanges should store data in Redis with this format:

**Key Patterns**:
- `{redis_prefix}:{BASE_COIN}` - LTP (Last Traded Price)
- `{redis_prefix}_ob:{BASE_COIN}` - Orderbook data (optional)
- `{redis_prefix}_trades:{BASE_COIN}` - Recent trades (optional)

Examples:
- `bybit_spot:BTC` - LTP data
- `bybit_spot_ob:BTC` - Orderbook (50-level bids/asks, spread, mid_price)
- `bybit_spot_trades:BTC` - Recent trades (rolling 50)
- `delta_futures:ETH` - LTP data
- `coindcx_futures:SOL` - LTP data

**LTP Data Structure** (Hash):
```
ltp: "106960.50"              # Required
timestamp: "2025-10-19T..."    # Required
original_symbol: "BTCUSD"      # Required
volume_24h: "98768"            # Optional
high_24h: "107466.5"           # Optional
low_24h: "106486.5"            # Optional
mark_price: "106960.678"       # Optional (Futures)
funding_rate: "-0.00110166"    # Optional (Futures)
open_interest: "220869"        # Optional (Futures)
```

**Orderbook Data Structure** (Hash):
```
bids: "[["106500.0","1.5"],...]"  # JSON array of [price, qty]
asks: "[["106501.0","0.8"],...]"  # JSON array of [price, qty]
spread: "1.0"                     # Best ask - best bid
mid_price: "106500.5"             # (best_bid + best_ask) / 2
update_id: "12345"                # Sequence number
timestamp: "2025-10-19T..."       # Required
original_symbol: "BTCUSDT"        # Required
```

**Trades Data Structure** (Hash):
```
trades: "[{"p":"106500","q":"0.1","s":"Buy","t":1705837200000,"id":"123"}]"
count: "50"                       # Number of trades stored
timestamp: "2025-10-19T..."       # Required
original_symbol: "BTCUSDT"        # Required
```

---

## Tips & Best Practices

### 1. Start with a Template
Copy an existing service that uses similar technology:
- WebSocket → Copy `delta_f`
- Socket.IO → Copy `coindcx_f`
- REST API → Copy `coindcx_f/futures_rest_service.py`

### 2. Test Standalone First
Always test your service independently before adding to manager:

```bash
python -m services.your_exchange.futures_ltp_service
```

### 3. Check Exchange Documentation
Find the exchange's API documentation for:
- WebSocket URL
- Subscription message format
- Data format/structure
- Rate limits

### 4. Add Comprehensive Error Handling
```python
try:
    await self._process_data(data)
except KeyError as e:
    self.logger.error(f"Missing field in data: {e}")
except ValueError as e:
    self.logger.error(f"Invalid value: {e}")
except Exception as e:
    self.logger.error(f"Unexpected error: {e}", exc_info=True)
```

### 5. Use Debug Logging
```python
self.logger.debug(f"Received message: {message}")
self.logger.debug(f"Parsed data: {data}")
```

Enable debug logs:
```bash
# In .env file
LOG_LEVEL=DEBUG
```

### 6. Symbol Normalization
Different exchanges use different symbol formats. Normalize them:

```python
def _extract_base_coin(self, symbol: str) -> str:
    """Extract base coin from symbol."""
    # Remove common suffixes
    symbol = symbol.replace('USDT', '').replace('USD', '')
    symbol = symbol.replace('PERP', '').replace('-', '')

    # Handle exchange-specific formats
    if symbol.startswith('B-'):  # CoinDCX
        symbol = symbol[2:]

    return symbol.split('_')[0].upper()
```

---

## Troubleshooting

### Service Won't Start
```bash
# Check logs
tail -f logs/your-exchange.log

# Check configuration
cat config/exchanges.yaml | grep -A 20 "your_exchange"

# Test Python import
python -c "from services.your_exchange import YourService"
```

### No Data in Redis
```bash
# Enable debug logging
# In .env: LOG_LEVEL=DEBUG

# Watch raw messages
tail -f logs/your-exchange.log | grep "Received message"

# Check WebSocket connection
grep "WebSocket connected" logs/your-exchange.log
```

### Connection Errors
- Check WebSocket URL is correct
- Verify exchange API is accessible
- Check firewall/network settings
- Look for API rate limits

---

## Current Exchanges

| Exchange | Type | Service Directory | Redis Prefix |
|----------|------|-------------------|--------------|
| Bybit | Spot | `services/bybit_s` | `bybit_spot` |
| CoinDCX | Futures | `services/coindcx_f` | `coindcx_futures` |
| Delta | Futures | `services/delta_f` | `delta_futures` |

---

## Need Help?

1. Check existing services for examples
2. Review exchange API documentation
3. Test standalone before integrating
4. Check logs for errors
5. Verify Redis data format

---

**Adding a new exchange is easy! Just follow this guide and you'll have it running in minutes.**
