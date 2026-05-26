"""Discovery loop writes the active catalog to Redis."""
import json
import pytest
from unittest.mock import MagicMock, patch


class _FakeAsyncRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}
    async def set(self, key, value):
        self.store[key] = value
        return True
    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True


@pytest.mark.asyncio
async def test_write_catalog_serializes_legs_and_sets_ttl():
    # Patch RedisClient so BaseService.__init__ does not try to connect to Redis
    with patch("core.base_service.RedisClient", return_value=MagicMock()):
        from services.polymarket.polymarket_service import PolymarketService
        svc = PolymarketService({"enabled": True})
    fake = _FakeAsyncRedis()
    svc._redis = fake  # inject

    legs = [
        {"ticker": "PMABC:UP", "condition_id": "0x1", "token_id": "t1",
         "outcome": "UP", "outcome_label": "Up", "title": "BTC UD",
         "slug": "btc-updown-5m-1", "tick_size": 0.01, "neg_risk": False,
         "active": True, "closed": False},
    ]
    await svc._write_catalog(legs)

    raw = fake.store["polymarket:discovery:active"]
    decoded = json.loads(raw)
    assert decoded[0]["ticker"] == "PMABC:UP"
    assert fake.ttls["polymarket:discovery:active"] == svc.CATALOG_TTL_SEC
