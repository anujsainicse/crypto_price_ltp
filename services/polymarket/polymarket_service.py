"""Polymarket market-data service for Crypto Price LTP.

Two cooperating async loops sharing one async-Redis connection:
  - discovery loop: poll Gamma list_active_legs() -> write polymarket:discovery:active
  - price-feed loop: public CLOB market WS for registered tokens (added in Task 3)

Uses redis.asyncio (NOT the sync RedisClient) because the price feed is WS-driven
and async by nature, and the writes must preserve the exact hash shapes the
scalper backend already reads (ISO-8601 timestamps, no field changes).
"""
from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis

from config.settings import settings
from core.base_service import BaseService
from services.polymarket.gamma_discovery import list_active_legs


class PolymarketService(BaseService):
    CATALOG_KEY = "polymarket:discovery:active"
    CATALOG_TTL_SEC = 60
    DISCOVERY_INTERVAL_SEC = 5

    def __init__(self, config: dict):
        super().__init__(service_name="Polymarket", config=config)
        self._redis: aioredis.Redis | None = None
        self._tasks: list[asyncio.Task] = []

    def _redis_url(self) -> str:
        pwd = f":{settings.REDIS_PASSWORD}@" if getattr(settings, "REDIS_PASSWORD", None) else ""
        return f"redis://{pwd}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"

    async def start(self):
        self.running = True
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url(), decode_responses=True)
        self.logger.info("Polymarket service starting (discovery + feed)")
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="pm_discovery"),
            # feed loop task added in Task 3
        ]
        await self._shutdown_event.wait()

    async def stop(self):
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

    async def _discovery_loop(self):
        while not self._shutdown_event.is_set():
            try:
                legs = await list_active_legs()
                await self._write_catalog(legs)
                self.logger.info("pm_discovery: wrote %d legs to catalog", len(legs))
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
        await self._redis.set(self.CATALOG_KEY, json.dumps(legs))
        await self._redis.expire(self.CATALOG_KEY, self.CATALOG_TTL_SEC)
