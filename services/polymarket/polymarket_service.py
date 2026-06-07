"""Polymarket market-data service for Crypto Price LTP.

Two cooperating async loops sharing one async-Redis connection:
  - discovery loop: poll Gamma list_active_legs() -> write polymarket:discovery:active
  - price-feed loop: public CLOB market WS for registered tokens (added in Task 3)

Uses redis.asyncio (NOT the sync RedisClient) because the price feed is WS-driven
and async by nature, and the writes preserve the hash shapes the scalper backend
already reads. Timestamps: the orderbook hash stays ISO-8601 (scalper market_data
parses it with fromisoformat) while the price/LTP hash uses epoch seconds (the
repo-wide int(timestamp) staleness contract). Both keys carry a redis_ttl.
"""
from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis

from config.settings import settings
from core.base_service import BaseService
from services.polymarket.gamma_discovery import (
    list_active_legs,
    list_series_legs,
    merge_legs_dedup,
)
from services.polymarket.market_feed import PolymarketMarketFeed
from services.polymarket.price_history import PolymarketPriceHistory


class PolymarketService(BaseService):
    CATALOG_KEY = "polymarket:discovery:active"

    def __init__(self, config: dict):
        super().__init__(service_name="Polymarket", config=config)
        self.DISCOVERY_INTERVAL_SEC = config.get('discovery_interval_sec', 5)
        # 1h/4h markets change hourly, so their targeted /events series queries
        # refresh on a slower cadence than the 5s short-interval enumeration.
        self.LONG_REFRESH_INTERVAL_SEC = config.get('long_refresh_interval_sec', 60)
        self.CATALOG_TTL_SEC = config.get('catalog_ttl_sec', 60)
        self.REDIS_TTL = config.get('redis_ttl', 60)
        self.TRADES_ENABLED = config.get('trades_enabled', True)
        self.TRADES_LIMIT = config.get('trades_limit', 50)
        self.WATCH_SCAN_INTERVAL_SEC = config.get('watch_scan_interval_sec', 5)
        self.PRICEHISTORY_ENABLED = config.get('pricehistory_enabled', True)
        self.PRICEHISTORY_INTERVAL_SEC = config.get('pricehistory_interval_sec', 20)
        # `1d` not `max`: `interval=max` makes the CLOB ignore `fidelity`,
        # yielding ~10-min points over the token's ~24h life (≈1 point inside a
        # 15m window). A bounded interval honors `fidelity=1` → dense 1-min data.
        self.PRICEHISTORY_CLOB_INTERVAL = config.get('pricehistory_clob_interval', '1d')
        self.PRICEHISTORY_FIDELITY = config.get('pricehistory_fidelity', 1)
        self._redis: aioredis.Redis | None = None
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        # Cached long-interval (1h/4h) legs, refreshed every LONG_REFRESH_INTERVAL_SEC
        # and merged into every 5s catalog write. Survives a transient series-query
        # failure (last good set is reused).
        self._long_legs: list[dict] = []
        self._last_long_refresh: float = 0.0

    def _redis_url(self) -> str:
        pwd = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
        return f"redis://{pwd}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"

    async def start(self):
        self.running = True
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url(), decode_responses=True)
        self.logger.info("Polymarket service starting (discovery + feed)")
        feed = PolymarketMarketFeed(
            redis=self._redis,
            logger=self.logger,
            redis_ttl=self.REDIS_TTL,
            trades_enabled=self.TRADES_ENABLED,
            trades_limit=self.TRADES_LIMIT,
            scan_interval=self.WATCH_SCAN_INTERVAL_SEC,
        )
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="pm_discovery"),
            asyncio.create_task(feed.run_forever(), name="pm_feed"),
        ]
        if self.PRICEHISTORY_ENABLED:
            price_history = PolymarketPriceHistory(
                redis=self._redis,
                logger=self.logger,
                interval_sec=self.PRICEHISTORY_INTERVAL_SEC,
                clob_interval=self.PRICEHISTORY_CLOB_INTERVAL,
                fidelity=self.PRICEHISTORY_FIDELITY,
                ttl=self.REDIS_TTL,
            )
            self._tasks.append(
                asyncio.create_task(price_history.run_forever(), name="pm_pricehistory")
            )
        await self._shutdown_event.wait()

    async def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.running = False
        self._shutdown_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self.logger.info("Polymarket service stopped")

    def _monotonic(self) -> float:
        """Monotonic clock for the long-refresh gate (patchable in tests)."""
        return asyncio.get_running_loop().time()

    async def _refresh_long_legs_if_due(self) -> None:
        """Refresh the cached 1h/4h series legs when LONG_REFRESH_INTERVAL_SEC has
        elapsed (and on the very first tick). The due-time advances even on failure
        so a persistent series outage can't turn into a 5s retry storm — the last
        good set keeps serving until the next window."""
        now = self._monotonic()
        due = (
            self._last_long_refresh == 0.0
            or (now - self._last_long_refresh) >= self.LONG_REFRESH_INTERVAL_SEC
        )
        if not due:
            return
        self._last_long_refresh = now
        try:
            self._long_legs = await list_series_legs()
        except Exception as exc:  # noqa: BLE001 — keep serving the cached set
            self.logger.warning(
                "pm_discovery: long-interval series refresh failed "
                "(serving cached %d legs): %s",
                len(self._long_legs),
                exc,
            )

    async def _discovery_tick(self) -> None:
        """One discovery cycle: fetch short-interval legs fresh, refresh long-interval
        legs if due, merge (dedup by condition_id), write the catalog."""
        short_legs = await list_active_legs()
        await self._refresh_long_legs_if_due()
        legs = merge_legs_dedup(short_legs, self._long_legs)
        await self._write_catalog(legs)
        # merge_legs_dedup keeps every short leg and appends only non-duplicate
        # long legs, so the long count actually written is total - short (NOT the
        # raw len(self._long_legs), which double-counts markets present in both).
        self.logger.info(
            "pm_discovery: wrote %d legs to catalog (%d short + %d long)",
            len(legs),
            len(short_legs),
            len(legs) - len(short_legs),
        )

    async def _discovery_loop(self):
        while not self._shutdown_event.is_set():
            try:
                await self._discovery_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must survive
                self.logger.warning("pm_discovery failed: %s", exc)
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), self.DISCOVERY_INTERVAL_SEC
                )
            except asyncio.TimeoutError:
                pass

    async def _write_catalog(self, legs: list[dict]):
        if self._redis is None:
            return
        await self._redis.set(self.CATALOG_KEY, json.dumps(legs))
        await self._redis.expire(self.CATALOG_KEY, self.CATALOG_TTL_SEC)
