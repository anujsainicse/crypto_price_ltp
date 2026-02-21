# Redis Key Migration Guide

## What Changed

Redis keys now use the **full exchange symbol** as the key suffix instead of just the base coin.

**Before**: `bybit_spot:BTC` (stripped quote currency)
**After**: `bybit_spot:BTCUSDT` (full exchange symbol)

This fixes key collisions when multiple quote pairs exist for the same base coin (e.g., `ETHUSDT` and `ETHUSDC` on Bybit Spot Testnet were both writing to `bybit_spot_testnet:ETH`).

---

## Key Format Changes

| Service | Redis Prefix | Before | After |
|---------|-------------|--------|-------|
| Bybit Spot | `bybit_spot` | `bybit_spot:BTC` | `bybit_spot:BTCUSDT` |
| Bybit Spot Testnet | `bybit_spot_testnet` | `bybit_spot_testnet:ETH` | `bybit_spot_testnet:ETHUSDT` |
| Bybit Futures OB | `bybit_futures_ob` | `bybit_futures_ob:BTC` | `bybit_futures_ob:BTCUSDT` |
| Bybit Futures Testnet | `bybit_futures_testnet` | `bybit_futures_testnet:BTC` | `bybit_futures_testnet:BTCUSDT` |
| Binance Spot | `binance_spot` | `binance_spot:BTC` | `binance_spot:BTCUSDT` |
| CoinDCX Spot | `coindcx_spot` | `coindcx_spot:BTC` | `coindcx_spot:BTC_USDT` |
| CoinDCX Futures | `coindcx_futures` | `coindcx_futures:BTC` | `coindcx_futures:BTC_USDT` |
| Delta Spot | `delta_spot` | `delta_spot:BTC` | `delta_spot:BTCUSD` |
| Delta Futures | `delta_futures` | `delta_futures:BTC` | `delta_futures:BTCUSD` |

### Services NOT Changed

These services already used full symbols or bare coin names:

| Service | Key Format | Reason |
|---------|-----------|--------|
| HyperLiquid Spot | `hyperliquid_spot:BTC` | Already uses bare symbols (no quote currency in exchange format) |
| HyperLiquid Futures | `hyperliquid_futures:BTC` | Already uses bare symbols |
| Bybit Options | `bybit_options:BTC-25DEC26-70000-P-USDT` | Already uses full option symbols |
| Delta Options | `delta_options:C-BTC-106000-241220` | Already uses full option symbols |

---

## Downstream Consumer Migration

### AOE / Scalper Price Monitor

Update key construction to use full exchange symbols instead of base coins.

**Before:**
```python
price_key = f"coindcx_futures:{symbol}"  # symbol was "BTC"
```

**After:**
```python
price_key = f"coindcx_futures:{symbol}"  # symbol is now "BTC_USDT"
```

The `original_symbol` field in each Redis hash still contains the full exchange-native symbol (e.g., `B-BTC_USDT` for CoinDCX Futures) for reference.

### Pattern for Reading Keys

Use `SCAN` with prefix patterns — this approach is migration-safe:

```python
# This still works (prefix-based scanning is format-agnostic)
keys = redis_client.scan_iter(match="bybit_spot:*")
```

### Key Discovery

If you need to discover available symbols for a service:

```python
import redis
r = redis.Redis()

# List all Bybit spot keys
for key in r.scan_iter(match="bybit_spot:*"):
    print(key)
# Output: bybit_spot:BTCUSDT, bybit_spot:ETHUSDT, ...
```

---

## Rollback

If rollback is needed, revert the `_get_redis_symbol` methods back to the old `_extract_base_coin` implementations. The Redis keys are ephemeral (60s TTL), so old keys will expire naturally once the services restart with reverted code.
