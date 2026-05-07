# Bybit Spot kline migration — review-feedback fixes

**Date:** 2026-05-07
**Branch:** `feat/bybit-spot-kline-migration`
**Related PR:** #16
**Status:** Approved by user; implementing.

## Background

PR #16 migrated Bybit Spot (mainnet + testnet) WebSocket from `tickers.{symbol}` to
`kline.1.{symbol}`. Code review surfaced two high-confidence regressions:

1. **`timestamp` field overridden with Bybit-side candle time.** `set_price_data` writes
   `timestamp = int(time.time())`, but the kline handler passes
   `additional_data['timestamp']` derived from the candle's own ms timestamp, which then
   overrides the wall-clock value. AOE/Monitoring code documented in `.claude/CLAUDE.md`
   uses `time.time() - int(timestamp) > 5` to detect staleness; on low-trade symbols
   (MNT, HYPE) the candle timestamp can lag wall-clock by more than 5s on a healthy
   connection, producing false-positive stale-data warnings.

2. **`kline.1` does not push on idle minutes.** The old `tickers` channel pushed on any
   state change, naturally refreshing the 60s Redis TTL on every symbol. `kline.1` only
   pushes when candle data changes — i.e. when a trade prints in the bucket. A
   trade-quiet minute on illiquid symbols leads to >60s without a push and
   `bybit_spot:{symbol}` disappears from Redis. Commit `47870e6` previously fixed the
   same class of regression for orderbooks via `_refresh_orderbooks_periodically()`; the
   LTP hash is unprotected.

## Goals

- Restore the cross-service `timestamp` contract (wall-clock seconds when Redis was
  written) without losing the Bybit-side candle time.
- Ensure `bybit_spot:{symbol}` and `bybit_spot_testnet:{symbol}` stay alive in Redis on
  trade-quiet minutes, mirroring the existing orderbook keep-alive behaviour.
- Keep both services' implementations in lockstep — they have shared structure today and
  should keep it.

## Non-goals

- No change to the channel choice (kline.1 is the right primary source).
- No change to the OHLCV hash schema.
- No re-introduction of 24h fields.

## Design

### 1. Timestamp semantics

Stop writing `timestamp` via `additional_data`. Instead, expose the candle's Bybit-side
update time as a new `candle_timestamp` field (seconds, ms→s converted). The hash
`timestamp` continues to mean "wall-clock seconds when this row was written to Redis",
matching every other producer in the repo.

**Hash fields after fix:**

| Field              | Source                                  | Meaning                                |
|--------------------|-----------------------------------------|----------------------------------------|
| `timestamp`        | `int(time.time())` (in `set_price_data`)| Wall-clock seconds at write time       |
| `candle_timestamp` | `int(candle['timestamp']) // 1000`      | Bybit-side last candle update (sec)    |
| `open/high/low/close/volume` | candle dict                   | Per-1m OHLCV (unchanged)               |
| `ltp`              | candle.close (unchanged)                | Last trade price within the bucket     |

The `candle_timestamp` field is only written when the kline payload supplies a usable
timestamp. If parsing fails the field is omitted (kline timestamps from Bybit are always
present, so this is just defensive parsing).

### 2. Idle-minute keep-alive

Add an in-memory cache of the last candle per symbol and a periodic refresher that
re-writes the LTP hash from the cache:

- **Cache:** `self._last_kline: Dict[str, dict]` populated at the end of
  `_process_ticker_update` after a successful Redis write. Stores the raw candle dict
  plus the `redis_symbol`, `original_symbol`, and the price float we already validated.
- **Refresher:** Extend the existing `_refresh_orderbooks_periodically` task in the
  testnet service to also iterate `self._last_kline` and re-write the LTP hash with a
  fresh wall-clock `timestamp`. Rename the task to `_refresh_caches_periodically` to
  reflect its expanded responsibility. The mainnet service does not have this task
  today; add the same combined task there.
- **Cadence:** 45s (matches the existing testnet orderbook refresh cadence). 45s + 60s
  TTL gives a 15s safety margin even if a single refresh tick is delayed.
- **Lifecycle:** Task is started where the existing testnet refresher is started (or
  alongside the connect path on mainnet) and cancelled in the existing disconnect/cleanup
  flow. The cache is cleared on disconnect so a reconnect doesn't refresh stale
  candles.

### 3. Failure-mode behaviour

| Scenario | Before fix | After fix |
|----------|------------|-----------|
| Idle minute, healthy WS | Key TTL expires → AOE "missing" alert | Refresher re-writes within 45s, key alive |
| WS disconnect | Key TTL expires (acceptable) | Cache cleared on disconnect; refresher stops re-writing; AOE staleness check trips correctly via wall-clock `timestamp` |
| First message after start | Same as today | Same as today (refresher has nothing to re-write until first kline lands) |
| Low-volume symbol on healthy WS | False "stale price" warnings (`time.time() - candle_timestamp > 5`) | `timestamp` advances on every refresh tick; AOE warning is gated on real connection health |

### 4. Files modified

- `services/bybit_s/spot_service.py` — drop `timestamp` from `additional_data`, add
  `candle_timestamp`, populate `self._last_kline`, add
  `_refresh_caches_periodically` task with LTP + orderbook + trades refresh, start/stop
  it in connect/disconnect.
- `services/bybit_spot_testnet/spot_testnet_service.py` — same changes; rename existing
  `_refresh_orderbooks_periodically` → `_refresh_caches_periodically` and fold LTP
  refresh into it.
- `.claude/CLAUDE.md` — document the `candle_timestamp` field in the LTP/OHLCV hash
  block; document the LTP keep-alive mechanism in the Bybit Spot Note.
- `docs/superpowers/specs/2026-05-07-bybit-spot-kline-fixes-design.md` — this spec.

## Testing

- Manual smoke test: start mainnet service, observe `bybit_spot:BTCUSDT` `timestamp`
  advancing every kline message AND every 45s refresh tick even on quiet symbols.
- Manual smoke test: stop the WS connection, confirm cache is cleared and refresher
  stops re-writing.
- `redis-cli HGETALL bybit_spot:BTCUSDT` shows both `timestamp` (wall-clock) and
  `candle_timestamp` (Bybit-side).
