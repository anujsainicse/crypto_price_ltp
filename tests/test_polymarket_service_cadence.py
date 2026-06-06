"""Decoupled discovery cadence: short-interval legs every tick, long-interval
(1h/4h) series legs on a slower timer, merged into one catalog write.

Short-interval enumeration (5m/15m) must run every tick, but the targeted /events
series queries for 1h/4h are slow-moving and refresh only every
LONG_REFRESH_INTERVAL_SEC — yet the cached long set is merged into EVERY catalog
write, and survives a transient series-query failure.
"""
import json
import logging

import pytest
from unittest.mock import MagicMock, patch

import services.polymarket.polymarket_service as pm
from services.polymarket.polymarket_service import PolymarketService


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


def _leg(cond, slug, asset, interval, outcome):
    return {
        "ticker": f"PM{cond}:{outcome}", "condition_id": cond, "token_id": f"{cond}_{outcome}",
        "outcome": outcome, "outcome_label": outcome.title(), "title": slug, "slug": slug,
        "tick_size": 0.01, "neg_risk": False, "active": True, "closed": False,
        "asset": asset, "interval": interval, "window_start": None, "window_end": None,
    }


def _short_legs():
    return [_leg("0x5m", "btc-updown-5m-1", "BTC", "5m", "UP"),
            _leg("0x5m", "btc-updown-5m-1", "BTC", "5m", "DOWN")]


def _long_legs():
    return [_leg("0xeth1h", "ethereum-up-or-down-june-5-2026-7am-et", "ETH", "1h", "UP"),
            _leg("0xeth1h", "ethereum-up-or-down-june-5-2026-7am-et", "ETH", "1h", "DOWN")]


def _make_service():
    svc = PolymarketService({"enabled": True, "long_refresh_interval_sec": 60})
    svc._redis = _FakeAsyncRedis()
    return svc


def _catalog(svc):
    return json.loads(svc._redis.store["polymarket:discovery:active"])


@patch("core.base_service.RedisClient", return_value=MagicMock())
@pytest.mark.asyncio
async def test_long_legs_refresh_on_decoupled_cadence(_mock_redis_client, monkeypatch):
    short_calls = {"n": 0}
    long_calls = {"n": 0}

    async def fake_short():
        short_calls["n"] += 1
        return _short_legs()

    async def fake_long():
        long_calls["n"] += 1
        return _long_legs()

    monkeypatch.setattr(pm, "list_active_legs", fake_short)
    monkeypatch.setattr(pm, "list_series_legs", fake_long)

    svc = _make_service()
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc, "_monotonic", lambda: clock["t"])

    await svc._discovery_tick()                      # first tick → long refreshes
    assert (short_calls["n"], long_calls["n"]) == (1, 1)

    clock["t"] += 5                                  # +5s → short only
    await svc._discovery_tick()
    assert (short_calls["n"], long_calls["n"]) == (2, 1)

    clock["t"] += 50                                 # +55s total since refresh → still short only
    await svc._discovery_tick()
    assert (short_calls["n"], long_calls["n"]) == (3, 1)

    clock["t"] += 10                                 # +65s since refresh → long refreshes again
    await svc._discovery_tick()
    assert (short_calls["n"], long_calls["n"]) == (4, 2)

    # every catalog write merges short + long
    intervals = {leg["interval"] for leg in _catalog(svc)}
    assert intervals == {"5m", "1h"}


@patch("core.base_service.RedisClient", return_value=MagicMock())
@pytest.mark.asyncio
async def test_cached_long_legs_survive_series_refresh_failure(_mock_redis_client, monkeypatch):
    state = {"fail": False}

    async def fake_short():
        return _short_legs()

    async def fake_long():
        if state["fail"]:
            raise RuntimeError("gamma /events down")
        return _long_legs()

    monkeypatch.setattr(pm, "list_active_legs", fake_short)
    monkeypatch.setattr(pm, "list_series_legs", fake_long)

    svc = _make_service()
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc, "_monotonic", lambda: clock["t"])

    await svc._discovery_tick()                      # long succeeds → cached
    assert {leg["interval"] for leg in _catalog(svc)} == {"5m", "1h"}

    # next refresh window, but the series query now fails
    state["fail"] = True
    clock["t"] += 65
    await svc._discovery_tick()

    # catalog still carries the cached 1h legs + fresh short legs (no blank-out)
    assert {leg["interval"] for leg in _catalog(svc)} == {"5m", "1h"}
    assert len(svc._long_legs) == 2


@patch("core.base_service.RedisClient", return_value=MagicMock())
@pytest.mark.asyncio
async def test_persistent_failure_does_not_retry_every_tick(_mock_redis_client, monkeypatch):
    """The due-time advances even on failure, so a series outage can't become a
    5s retry storm."""
    long_calls = {"n": 0}

    async def fake_short():
        return _short_legs()

    async def fake_long():
        long_calls["n"] += 1
        raise RuntimeError("gamma /events down")

    monkeypatch.setattr(pm, "list_active_legs", fake_short)
    monkeypatch.setattr(pm, "list_series_legs", fake_long)

    svc = _make_service()
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc, "_monotonic", lambda: clock["t"])

    await svc._discovery_tick()        # first tick attempts once
    clock["t"] += 5
    await svc._discovery_tick()        # +5s → NOT due, no second attempt
    clock["t"] += 5
    await svc._discovery_tick()        # +10s → still not due
    assert long_calls["n"] == 1

    # short-interval legs are still written despite the long outage
    assert {leg["interval"] for leg in _catalog(svc)} == {"5m"}


@patch("core.base_service.RedisClient", return_value=MagicMock())
def test_service_reads_trades_enabled(_mock_redis_client):
    """trades_enabled must be read from config (default True) so it can be wired
    into the feed — otherwise the YAML knob is a silent no-op."""
    assert PolymarketService({"enabled": True}).TRADES_ENABLED is True
    assert PolymarketService(
        {"enabled": True, "trades_enabled": False}
    ).TRADES_ENABLED is False


@patch("core.base_service.RedisClient", return_value=MagicMock())
@pytest.mark.asyncio
async def test_discovery_log_counts_are_post_dedup(_mock_redis_client, monkeypatch, caplog):
    """The 'X short + Y long' log must report the legs actually written, not the
    raw pre-merge inputs — merge_legs_dedup drops long legs that duplicate a
    short market, so raw counts can exceed the written total."""
    async def fake_short():
        return [_leg("0xDUP", "btc-updown-5m-1", "BTC", "5m", "UP"),
                _leg("0xDUP", "btc-updown-5m-1", "BTC", "5m", "DOWN")]

    async def fake_long():
        # 0xDUP duplicates the short market (its pair is dropped); 0xNEW is kept.
        return [_leg("0xDUP", "eth-1h", "ETH", "1h", "UP"),
                _leg("0xDUP", "eth-1h", "ETH", "1h", "DOWN"),
                _leg("0xNEW", "eth-1h-2", "ETH", "1h", "UP"),
                _leg("0xNEW", "eth-1h-2", "ETH", "1h", "DOWN")]

    monkeypatch.setattr(pm, "list_active_legs", fake_short)
    monkeypatch.setattr(pm, "list_series_legs", fake_long)

    svc = _make_service()
    monkeypatch.setattr(svc, "_monotonic", lambda: 1000.0)

    with caplog.at_level(logging.INFO):
        await svc._discovery_tick()

    # 2 short + 2 NEW long written = 4 total (the 0xDUP long pair was dropped).
    msg = [r.getMessage() for r in caplog.records if "wrote" in r.getMessage()][-1]
    assert "wrote 4 legs" in msg
    assert "(2 short + 2 long)" in msg  # post-dedup, not the raw "4 long"
