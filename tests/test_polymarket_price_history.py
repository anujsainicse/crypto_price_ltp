"""PolymarketPriceHistory: scan watch-set → fetch /prices-history → write
polymarket_pricehistory:<TICKER>."""
import json
import logging

import pytest

from services.polymarket.price_history import PolymarketPriceHistory


class _FakeAsyncRedis:
    def __init__(self):
        self.hashes = {}
        self.ttls = {}
        self.kv = {}

    async def hset(self, key, mapping=None):
        self.hashes[key] = dict(mapping or {})
        return True

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def scan_iter(self, match):
        prefix = match.rstrip("*")
        for k in list(self.kv):
            if k.startswith(prefix):
                yield k


def _ph(redis):
    return PolymarketPriceHistory(
        redis=redis, logger=logging.getLogger("t"),
        interval_sec=20, clob_interval="max", fidelity=1, ttl=60,
    )


@pytest.mark.asyncio
async def test_scan_watch_returns_token_ticker_pairs():
    r = _FakeAsyncRedis()
    r.kv["polymarket:watch:PMABC:UP"] = json.dumps(
        {"token_id": "0xup", "condition_id": "0xc", "ticker": "PMABC:UP"}
    )
    ph = _ph(r)
    pairs = await ph._scan_watch()
    assert pairs == [("0xup", "PMABC:UP")]


@pytest.mark.asyncio
async def test_write_history_hash():
    r = _FakeAsyncRedis()
    ph = _ph(r)
    await ph._write("PMABC:UP", [{"t": 1718236800, "p": 0.52}, {"t": 1718236860, "p": 0.55}])
    h = r.hashes["polymarket_pricehistory:PMABC:UP"]
    assert json.loads(h["history"]) == [{"t": 1718236800, "p": 0.52}, {"t": 1718236860, "p": 0.55}]
    assert h["interval"] == "max"
    assert h["fidelity"] == "1"
    assert r.ttls["polymarket_pricehistory:PMABC:UP"] == 60


@pytest.mark.asyncio
async def test_fetch_and_write_uses_fetcher(monkeypatch):
    r = _FakeAsyncRedis()
    r.kv["polymarket:watch:PMABC:UP"] = json.dumps(
        {"token_id": "0xup", "condition_id": "0xc", "ticker": "PMABC:UP"}
    )
    ph = _ph(r)

    async def fake_fetch(session, token_id):
        assert token_id == "0xup"
        return [{"t": 1, "p": 0.4}]

    monkeypatch.setattr(ph, "_fetch_history", fake_fetch)
    await ph.run_once()
    assert json.loads(r.hashes["polymarket_pricehistory:PMABC:UP"]["history"]) == [{"t": 1, "p": 0.4}]


@pytest.mark.asyncio
async def test_fetch_failure_skips_write(monkeypatch):
    r = _FakeAsyncRedis()
    r.kv["polymarket:watch:PMABC:UP"] = json.dumps(
        {"token_id": "0xup", "condition_id": "0xc", "ticker": "PMABC:UP"}
    )
    ph = _ph(r)

    async def fake_fetch(session, token_id):
        return None  # fetch failed

    monkeypatch.setattr(ph, "_fetch_history", fake_fetch)
    await ph.run_once()
    assert "polymarket_pricehistory:PMABC:UP" not in r.hashes
