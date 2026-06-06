"""PolymarketMarketFeed: last_trade_price → polymarket_trades:<TICKER> hash,
and the watch-set registry scan (polymarket:watch:*)."""
import json
import logging

import pytest

from services.polymarket.market_feed import PolymarketMarketFeed


class _FakeAsyncRedis:
    def __init__(self):
        self.hashes = {}   # key -> mapping
        self.ttls = {}     # key -> ttl
        self.kv = {}       # key -> string value (for scan_iter/get)

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


def _feed(trades_enabled=True):
    return PolymarketMarketFeed(
        redis=_FakeAsyncRedis(), logger=logging.getLogger("t"), redis_ttl=60,
        trades_limit=3, trades_enabled=trades_enabled,
    )


@pytest.mark.asyncio
async def test_last_trade_price_writes_trades_hash():
    feed = _feed()
    feed._token_ticker = {"0xtok": "PMABC:UP"}

    ev = {
        "event_type": "last_trade_price",
        "asset_id": "0xtok",
        "price": "0.456",
        "side": "BUY",
        "size": "219.21",
        "timestamp": "1750428146322",
    }
    await feed._handle_raw(json.dumps(ev))

    hashed = feed._redis.hashes["polymarket_trades:PMABC:UP"]
    trades = json.loads(hashed["trades"])
    # Side is normalised to Title-Case "Buy"/"Sell" — the repo-wide trades schema
    # (CLAUDE.md + every other producer: bybit/binance/delta/hyperliquid).
    assert trades[0] == {"p": 0.456, "q": 219.21, "s": "Buy", "t": 1750428146322,
                         "id": "1750428146322-0xtok"}
    assert hashed["count"] == "1"
    assert hashed["original_symbol"] == "PMABC:UP"
    assert feed._redis.ttls["polymarket_trades:PMABC:UP"] == 60


@pytest.mark.asyncio
async def test_trades_capped_and_newest_first():
    feed = _feed()  # trades_limit=3
    feed._token_ticker = {"0xtok": "PMABC:UP"}
    for i in range(4):
        await feed._handle_raw(json.dumps({
            "event_type": "last_trade_price", "asset_id": "0xtok",
            # size = i + 1 so all four are valid (>0); the deque cap, not the
            # size guard, is what drops the oldest.
            "price": "0.5", "side": "SELL", "size": str(i + 1), "timestamp": str(1000 + i),
        }))
    trades = json.loads(feed._redis.hashes["polymarket_trades:PMABC:UP"]["trades"])
    assert [t["t"] for t in trades] == [1003, 1002, 1001]  # newest first, oldest dropped


@pytest.mark.asyncio
async def test_zero_size_or_bad_side_trade_dropped():
    feed = _feed()
    feed._token_ticker = {"0xtok": "PMABC:UP"}
    # size 0 → not a real trade print
    await feed._handle_raw(json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xtok",
        "price": "0.5", "side": "BUY", "size": "0", "timestamp": "1000",
    }))
    # missing/invalid side
    await feed._handle_raw(json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xtok",
        "price": "0.5", "size": "5", "timestamp": "1001",
    }))
    assert "polymarket_trades:PMABC:UP" not in feed._redis.hashes


@pytest.mark.asyncio
async def test_unregistered_token_ignored():
    feed = _feed()
    feed._token_ticker = {}  # nothing registered
    await feed._handle_raw(json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xtok",
        "price": "0.5", "side": "BUY", "size": "1", "timestamp": "1000",
    }))
    assert "polymarket_trades:PMABC:UP" not in feed._redis.hashes


@pytest.mark.asyncio
async def test_refresh_registry_scans_watch_keys():
    feed = _feed()
    feed._redis.kv["polymarket:watch:PMABC:UP"] = json.dumps(
        {"token_id": "0xup", "condition_id": "0xc", "ticker": "PMABC:UP"}
    )
    feed._redis.kv["polymarket:watch:PMABC:DOWN"] = json.dumps(
        {"token_id": "0xdown", "condition_id": "0xc", "ticker": "PMABC:DOWN"}
    )
    # a durable registry key must NOT be picked up by the feed anymore
    feed._redis.kv["polymarket:market:PMOLD:UP"] = json.dumps(
        {"token_id": "0xold", "condition_id": "0xo"}
    )

    await feed._refresh_registry()

    assert feed._token_ticker == {"0xup": "PMABC:UP", "0xdown": "PMABC:DOWN"}
    assert "0xold" not in feed._token_ticker


@pytest.mark.asyncio
async def test_sell_side_normalised_to_title_case():
    feed = _feed()
    feed._token_ticker = {"0xtok": "PMABC:DOWN"}
    await feed._handle_raw(json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xtok",
        "price": "0.5", "side": "sell", "size": "10", "timestamp": "1000",
    }))
    trades = json.loads(feed._redis.hashes["polymarket_trades:PMABC:DOWN"]["trades"])
    assert trades[0]["s"] == "Sell"


@pytest.mark.asyncio
async def test_trades_disabled_skips_write():
    """trades_enabled=False must suppress all polymarket_trades:* writes — the
    config knob is honoured, matching every other service in the repo."""
    feed = _feed(trades_enabled=False)
    feed._token_ticker = {"0xtok": "PMABC:UP"}
    await feed._handle_raw(json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xtok",
        "price": "0.5", "side": "BUY", "size": "5", "timestamp": "1000",
    }))
    assert "polymarket_trades:PMABC:UP" not in feed._redis.hashes
    assert not feed._trades["0xtok"]  # nothing buffered either


@pytest.mark.asyncio
async def test_empty_watch_set_warns_for_standalone_deploy_visibility(caplog):
    """An empty watch-set (e.g. standalone deploy with no scalper backend writing
    polymarket:watch:*) must surface a WARNING, not a silent DEBUG line."""
    feed = _feed()
    feed._scan_interval = 0  # don't actually sleep
    with caplog.at_level(logging.WARNING, logger="t"):
        await feed.run_once()
    assert any(
        "No registered markets" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )
