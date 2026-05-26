"""
Polymarket public market-data feed (Task E1 — ported to Crypto Price LTP in Task 3).

Opens ONE credential-independent WebSocket to the Polymarket CLOB market
channel (wss://ws-subscriptions-clob.polymarket.com/ws/market), subscribes
to all registered token_ids, maintains an in-memory orderbook per token, and
writes price + orderbook data to Redis in the exact shape the backend's
/api/v1/price/* and /api/v1/orderbook endpoints already read.

Redis write contracts
─────────────────────
Orderbook  key: polymarket_ob:<TICKER>  (HASH)
    bids       = JSON [[price, qty], ...]  sorted descending by price
    asks       = JSON [[price, qty], ...]  sorted ascending by price
    spread     = str(best_ask - best_bid)
    mid_price  = str((best_bid + best_ask) / 2)
    timestamp  = ISO-8601 UTC string

Price      key: polymarket:<TICKER>  (HASH)
    ltp        = str(mid_price)   — the outcome token's mid-price (0–1)
    timestamp  = epoch seconds (str(int(time.time()))) — matches this repo's
                 LTP contract so AOE/Monitoring `int(timestamp)` staleness
                 checks work. The scalper price endpoint reads it as an opaque
                 string, so epoch is compatible there too.

Note the asymmetry: the orderbook timestamp is ISO-8601 (the scalper's
market_data endpoint parses it via datetime.fromisoformat and CLAUDE.md
documents the orderbook schema that way), while the price timestamp is epoch
seconds (the repo-wide LTP staleness contract). Both consumer contracts are
satisfied.

Both keys come from backend/app/core/exchange_metadata.py:
    redis_prefix = "polymarket"
    ticker_to_redis_symbol for Polymarket → ticker.upper() (the raw ticker)

The price HASH format is confirmed against:
    backend/app/core/redis.py RedisClient.get_price_data → hgetall
    backend/app/api/v1/endpoints/price.py → reads data["ltp"]

The orderbook HASH format is confirmed against:
    backend/app/api/v1/endpoints/market_data.py:58-76 → hgetall, reads
    data["bids"], data["asks"], data["spread"], data["mid_price"],
    data["timestamp"]

This module is credential-independent — no ClobClient, no per-user auth.
It must NOT be imported by or modify the Task D adapter.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import websockets

# Public CLOB market WebSocket — no auth required
_WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# How often to re-scan Redis for newly-registered markets (seconds)
_REGISTRY_REFRESH_INTERVAL = 30

# Top-N book levels to keep in memory and write to Redis
_MAX_LEVELS = 20

# Backoff: starts at 1s, doubles, caps at 30s
_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 30.0

# How long to pause after a CLEAN close (mirrors Task D's run_forever pattern)
_CLEAN_CLOSE_PAUSE = 1.0

# Type alias: (price, size)
Level = Tuple[float, float]


def _apply_level(
    levels: List[Level],
    price: float,
    size: float,
    *,
    descending: bool,
) -> List[Level]:
    """Upsert one ladder level (size=0 removes), keep sort order."""
    out = [(p, s) for (p, s) in levels if p != price]
    if size > 0:
        out.append((price, size))
        out.sort(key=lambda x: x[0], reverse=descending)
    return out[:_MAX_LEVELS]


class PolymarketMarketFeed:
    """
    Credential-independent Polymarket public market feed.

    Lifecycle:
        feed = PolymarketMarketFeed(redis, logger)
        await feed.run_forever()   # never returns unless cancelled

    State:
        _token_ticker  : {token_id: ticker}  — refreshed every 30s
        _books         : {token_id: {"bids": [...], "asks": [...]}}
    """

    def __init__(self, redis: Any, logger, redis_ttl: int = 60) -> None:
        self._redis = redis
        self._log = logger
        # Per-key TTL (seconds) — matches the repo-wide 60s contract so stale
        # data for resolved/delisted markets ages out instead of lingering.
        self._ttl = redis_ttl
        # token_id → ticker (e.g. "BTC-UD:UP")
        self._token_ticker: Dict[str, str] = {}
        # In-memory book per token_id
        self._books: Dict[str, Dict[str, List[Level]]] = defaultdict(
            lambda: {"bids": [], "asks": []}
        )
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """
        Reconnect loop with exponential backoff.

        Mirrors Task D's PolymarketWsClient.run_forever:
        - On error: sleep backoff (doubles, caps at 30s), then reconnect.
        - On CLEAN close: pause 1s before reconnecting (prevents hot loop).
        - On asyncio.CancelledError: re-raise cleanly.
        """
        backoff = _BACKOFF_INITIAL
        while not self._stop.is_set():
            try:
                await self.run_once()
                # Clean close from server — pause before reconnecting
                backoff = _BACKOFF_INITIAL
                if not self._stop.is_set():
                    await asyncio.sleep(_CLEAN_CLOSE_PAUSE)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.warning(
                    "[PolymarketFeed] WS error: %s; reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(_BACKOFF_MAX, backoff * 2)

    async def run_once(self) -> None:
        """
        Connect once, subscribe, and read until close or _stop.

        Also spawns a background registry-refresh task that re-scans Redis
        every _REGISTRY_REFRESH_INTERVAL seconds and reconnects if new tokens
        are discovered (new tokens require a fresh WS — Polymarket's market
        channel rejects re-subscribes that add assets).
        """
        # Ensure we have an up-to-date token→ticker map before connecting
        await self._refresh_registry()

        token_ids = list(self._token_ticker.keys())

        # Nothing to subscribe to yet — subscribing with an empty assets_ids
        # list is pointless. Sleep one refresh interval and let run_forever
        # retry once markets get registered.
        if not token_ids:
            self._log.debug(
                "[PolymarketFeed] No registered markets yet; sleeping %ds before retry",
                _REGISTRY_REFRESH_INTERVAL,
            )
            await asyncio.sleep(_REGISTRY_REFRESH_INTERVAL)
            return

        sub_msg = json.dumps({"type": "MARKET", "assets_ids": token_ids})

        # ping_interval/ping_timeout enable WS keepalive so a dead connection
        # is detected promptly (matches the Task D user-WS client).
        async with websockets.connect(
            _WS_MARKET_URL,
            ping_interval=10,
            ping_timeout=10,
        ) as ws:
            await ws.send(sub_msg)

            # Background task: periodically refresh registry; if set changes,
            # break out so run_forever reconnects with updated token list.
            refresh_needed = asyncio.Event()

            async def _refresh_loop():
                while not self._stop.is_set():
                    await asyncio.sleep(_REGISTRY_REFRESH_INTERVAL)
                    old_tokens = set(self._token_ticker.keys())
                    await self._refresh_registry()
                    if set(self._token_ticker.keys()) != old_tokens:
                        self._log.info(
                            "[PolymarketFeed] Token set changed, will reconnect to re-subscribe"
                        )
                        refresh_needed.set()
                        break

            refresh_task = asyncio.create_task(_refresh_loop())
            try:
                async for raw in ws:
                    if self._stop.is_set() or refresh_needed.is_set():
                        break
                    try:
                        await self._handle_raw(raw)
                    except Exception as exc:
                        self._log.warning("[PolymarketFeed] Message handling error: %s", exc)
            finally:
                refresh_task.cancel()
                try:
                    await refresh_task
                except (asyncio.CancelledError, Exception):
                    pass

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    async def _refresh_registry(self) -> None:
        """
        Scan polymarket:market:* keys in Redis and rebuild token_id→ticker map.

        Each key is polymarket:market:<TICKER>.
        Each value is JSON: {"token_id": ..., "condition_id": ..., ...}.
        """
        new_map: Dict[str, str] = {}
        try:
            async for key in self._redis.scan_iter("polymarket:market:*"):
                # Extract ticker from key suffix after last 'market:'
                # Key format: polymarket:market:<TICKER>
                # TICKER may itself contain colons (e.g. BTC-UD:UP)
                prefix = "polymarket:market:"
                if not key.startswith(prefix):
                    continue
                ticker = key[len(prefix):]
                try:
                    raw_val = await self._redis.get(key)
                    if not raw_val:
                        continue
                    data = json.loads(raw_val)
                    token_id = data.get("token_id")
                    if not token_id:
                        self._log.debug("[PolymarketFeed] Key %s has no token_id, skipping", key)
                        continue
                    new_map[token_id] = ticker
                except json.JSONDecodeError as e:
                    self._log.warning("[PolymarketFeed] JSON error for key %s: %s", key, e)
                except Exception as e:
                    self._log.warning("[PolymarketFeed] Error processing key %s: %s", key, e)
        except Exception as e:
            self._log.error("[PolymarketFeed] Registry scan failed: %s", e)
            return

        self._token_ticker = new_map
        self._log.debug("[PolymarketFeed] Registry refreshed: %d tokens", len(new_map))

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _handle_raw(self, raw: str | bytes) -> None:
        """
        Dispatch a raw WS message (may be a single event or a list).

        Handlers are awaited sequentially in arrival order — NOT fire-and-forget
        via create_task. This guarantees:
          (a) ORDERING: a full-book snapshot and a following price_change delta
              for the same token cannot interleave/reorder, so the in-memory
              _books[token_id] and the data written to Redis stay consistent.
          (b) NO GC FOOTGUN: create_task-ed coroutines with no retained strong
              reference can be garbage-collected mid-execution (CPython keeps
              only a weak ref), silently dropping an update.
        polybot's upstream.py processes these synchronously in order; awaiting
        sequentially is the async-correct equivalent.
        """
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as e:
            self._log.warning("[PolymarketFeed] JSON decode error: %s", e)
            return

        events = payload if isinstance(payload, list) else [payload]
        for ev in events:
            et = ev.get("event_type")
            if et == "price_change":
                for ch in ev.get("price_changes") or []:
                    await self._handle_price_change(ch)
            else:
                await self._handle_event(ev)

    async def _handle_event(self, ev: Dict[str, Any]) -> None:
        """
        Handle a full-book event.

        Shape: {"asset_id": <token_id>, "bids": [{"price":..,"size":..},...], "asks":[...]}

        Bids are pre-sorted descending by price, asks ascending — Polymarket
        delivers them that way. We re-sort to guarantee correctness regardless.
        """
        token_id = ev.get("asset_id")
        if not token_id:
            return

        ticker = self._token_ticker.get(token_id)
        if not ticker:
            return  # Not a registered market — ignore silently

        raw_bids = ev.get("bids") or []
        raw_asks = ev.get("asks") or []

        bids: List[Level] = []
        for lv in raw_bids:
            try:
                bids.append((float(lv["price"]), float(lv["size"])))
            except (KeyError, TypeError, ValueError):
                continue
        bids.sort(key=lambda x: x[0], reverse=True)
        bids = bids[:_MAX_LEVELS]

        asks: List[Level] = []
        for lv in raw_asks:
            try:
                asks.append((float(lv["price"]), float(lv["size"])))
            except (KeyError, TypeError, ValueError):
                continue
        asks.sort(key=lambda x: x[0], reverse=False)
        asks = asks[:_MAX_LEVELS]

        if not bids or not asks:
            return  # Cannot compute mid/spread — skip write

        # Update in-memory book
        self._books[token_id]["bids"] = bids
        self._books[token_id]["asks"] = asks

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        await self._write_redis(ticker, bids, asks, best_bid, best_ask)

    async def _handle_price_change(self, ch: Dict[str, Any]) -> None:
        """
        Handle a price_change delta.

        Shape: {"asset_id": <tid>, "side": "BUY"|"SELL", "price": <p>,
                "size": <s>, "best_bid": <bb>, "best_ask": <ba>}

        size=0 removes the level. best_bid/best_ask give mid directly
        (accurate even when the ladder is stale).
        """
        token_id = ch.get("asset_id")
        if not token_id:
            return

        ticker = self._token_ticker.get(token_id)
        if not ticker:
            return

        try:
            price = float(ch["price"])
            size = float(ch["size"])
        except (KeyError, TypeError, ValueError):
            return

        side = (ch.get("side") or "").upper()
        book = self._books[token_id]

        if side == "BUY":
            book["bids"] = _apply_level(book["bids"], price, size, descending=True)
        elif side == "SELL":
            book["asks"] = _apply_level(book["asks"], price, size, descending=False)

        bids = book["bids"]
        asks = book["asks"]

        # Prefer best_bid/best_ask from the message for mid accuracy
        try:
            best_bid = float(ch["best_bid"])
            best_ask = float(ch["best_ask"])
        except (KeyError, TypeError, ValueError):
            if not bids or not asks:
                return  # Cannot compute mid — skip
            best_bid = bids[0][0]
            best_ask = asks[0][0]

        await self._write_redis(ticker, bids, asks, best_bid, best_ask)

    # ------------------------------------------------------------------
    # Redis writes
    # ------------------------------------------------------------------

    async def _write_redis(
        self,
        ticker: str,
        bids: List[Level],
        asks: List[Level],
        best_bid: float,
        best_ask: float,
    ) -> None:
        """
        Write orderbook HASH and price HASH to Redis.

        Orderbook: polymarket_ob:<TICKER>
            bids, asks, spread, mid_price, timestamp

        Price: polymarket:<TICKER>
            ltp, timestamp

        Both verified against backend/app/api/v1/endpoints/market_data.py:58-76
        and backend/app/core/redis.py RedisClient.get_price_data (hgetall).
        """
        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        # Orderbook: ISO-8601 — the scalper market_data endpoint parses this
        # with datetime.fromisoformat() and CLAUDE.md documents it that way.
        ob_ts = datetime.now(timezone.utc).isoformat()
        # Price/LTP: epoch seconds — the repo-wide staleness contract is
        # int(price_data["timestamp"]) (AOE/Monitoring); an ISO string breaks it.
        price_ts = str(int(time.time()))

        ob_mapping = {
            "bids": json.dumps([[p, s] for p, s in bids]),
            "asks": json.dumps([[p, s] for p, s in asks]),
            "spread": str(spread),
            "mid_price": str(mid_price),
            "timestamp": ob_ts,
        }
        price_mapping = {
            "ltp": str(mid_price),
            "timestamp": price_ts,
        }

        ob_key = f"polymarket_ob:{ticker}"
        price_key = f"polymarket:{ticker}"
        try:
            await self._redis.hset(ob_key, mapping=ob_mapping)
            await self._redis.expire(ob_key, self._ttl)
            await self._redis.hset(price_key, mapping=price_mapping)
            await self._redis.expire(price_key, self._ttl)
        except Exception as e:
            self._log.error("[PolymarketFeed] Redis write error for %s: %s", ticker, e)
