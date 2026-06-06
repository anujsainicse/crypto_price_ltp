"""Polymarket outcome-token price-history feed (Phase 2).

For every token in the polymarket:watch:* feed-set (the SAME set the order
book + trades feed uses — UI-selected ∪ active-bot), periodically fetch the
public CLOB /prices-history series and write polymarket_pricehistory:<TICKER>.
The backend reads that key on demand; the frontend plots it as the outcome-
token probability (0->1) line. Nobody-watching markets are never fetched.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, List, Optional, Tuple

import aiohttp

_CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
_WATCH_PREFIX = "polymarket:watch:"
_HTTP_TIMEOUT = 10.0


class PolymarketPriceHistory:
    """Lifecycle: ph = PolymarketPriceHistory(redis, logger); await ph.run_forever()."""

    def __init__(
        self,
        redis: Any,
        logger,
        interval_sec: int = 20,
        clob_interval: str = "max",
        fidelity: int = 1,
        ttl: int = 60,
        max_concurrency: int = 4,
    ) -> None:
        self._redis = redis
        self._log = logger
        self._interval_sec = interval_sec
        self._clob_interval = clob_interval
        self._fidelity = fidelity
        self._ttl = ttl
        self._sem = asyncio.Semaphore(max_concurrency)
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must survive
                self._log.warning("[PolymarketPriceHistory] cycle error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), self._interval_sec)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> None:
        pairs = await self._scan_watch()
        if not pairs:
            return
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(
                *[self._fetch_and_write(session, tid, tk) for tid, tk in pairs],
                return_exceptions=True,
            )

    async def _scan_watch(self) -> List[Tuple[str, str]]:
        """Return [(token_id, ticker), ...] from polymarket:watch:* keys."""
        out: List[Tuple[str, str]] = []
        try:
            async for key in self._redis.scan_iter("polymarket:watch:*"):
                if not key.startswith(_WATCH_PREFIX):
                    continue
                raw = await self._redis.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                token_id = data.get("token_id")
                if not token_id:
                    continue
                out.append((token_id, data.get("ticker") or key[len(_WATCH_PREFIX):]))
        except Exception as exc:  # noqa: BLE001
            self._log.error("[PolymarketPriceHistory] watch scan failed: %s", exc)
        return out

    async def _fetch_and_write(self, session, token_id: str, ticker: str) -> None:
        async with self._sem:
            history = await self._fetch_history(session, token_id)
        if not history:
            return  # fetch failed or empty — keep last good (TTL) rather than blanking
        await self._write(ticker, history)

    async def _fetch_history(self, session, token_id: str) -> Optional[List[dict]]:
        params = {
            "market": token_id,
            "interval": self._clob_interval,
            "fidelity": str(self._fidelity),
        }
        try:
            async with session.get(
                _CLOB_HISTORY_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()
            return payload.get("history") or []
        except Exception as exc:  # noqa: BLE001 — per-token isolation
            self._log.warning(
                "[PolymarketPriceHistory] fetch failed for %s: %s", token_id, exc
            )
            return None

    async def _write(self, ticker: str, history: List[dict]) -> None:
        mapping = {
            "history": json.dumps(history),
            "interval": self._clob_interval,
            "fidelity": str(self._fidelity),
            "timestamp": str(int(time.time())),
        }
        key = f"polymarket_pricehistory:{ticker}"
        try:
            await self._redis.hset(key, mapping=mapping)
            await self._redis.expire(key, self._ttl)
        except Exception as exc:  # noqa: BLE001
            self._log.error("[PolymarketPriceHistory] write error for %s: %s", ticker, exc)

    def stop(self) -> None:
        self._stop.set()
