"""Regression tests for PR #17 extended code-review fixes.

Covers the three findings that passed the 60+ confidence threshold:
  1. Price hash `timestamp` must be epoch seconds (AOE/Monitoring do int(timestamp)).
     The orderbook hash `timestamp` must stay ISO-8601 (the scalper backend's
     market_data endpoint parses it with datetime.fromisoformat, and CLAUDE.md
     documents the orderbook schema as ISO-8601).
  2. polymarket: / polymarket_ob: keys must be written with a TTL (repo-wide 60s
     contract — every other producer expires its keys).
  3. The new polymarket_market service must be wired into the dashboard
     (web_dashboard._get_services_info) and the data-count prefix lists
     (core/control_interface).
"""
import logging
import os
import time
from datetime import datetime

import pytest

from services.polymarket.market_feed import PolymarketMarketFeed

PROJECT_ROOT = os.getcwd()
_LOG = logging.getLogger("test_pm_review")


class _FakeAsyncRedis:
    """Records hset mappings and expire TTLs for assertion."""

    def __init__(self):
        self.hashes = {}
        self.ttls = {}

    async def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return True

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True


def _make_feed(redis, **kwargs):
    return PolymarketMarketFeed(redis=redis, logger=_LOG, **kwargs)


# --- Issue: timestamp format -------------------------------------------------

@pytest.mark.asyncio
async def test_price_hash_timestamp_is_epoch_seconds():
    """polymarket:<ticker> timestamp must be int-parseable epoch seconds.

    AOE/Monitoring consumers do `int(price_data['timestamp'])`; an ISO-8601
    string raises ValueError.
    """
    fake = _FakeAsyncRedis()
    feed = _make_feed(fake)
    await feed._write_redis("PMABC:UP", [(0.4, 10.0)], [(0.6, 12.0)], 0.4, 0.6)

    price = fake.hashes["polymarket:PMABC:UP"]
    ts = int(price["timestamp"])  # must not raise
    assert abs(time.time() - ts) < 5


@pytest.mark.asyncio
async def test_orderbook_hash_timestamp_stays_iso8601():
    """polymarket_ob:<ticker> timestamp must remain ISO-8601.

    The scalper backend's market_data endpoint parses it with
    datetime.fromisoformat(...); CLAUDE.md documents the orderbook schema as
    ISO-8601. Guards against accidentally converting it to epoch too.
    """
    fake = _FakeAsyncRedis()
    feed = _make_feed(fake)
    await feed._write_redis("PMABC:UP", [(0.4, 10.0)], [(0.6, 12.0)], 0.4, 0.6)

    ob = fake.hashes["polymarket_ob:PMABC:UP"]
    parsed = datetime.fromisoformat(ob["timestamp"].replace("Z", "+00:00"))
    assert parsed.year >= 2024


# --- Issue: missing TTL ------------------------------------------------------

@pytest.mark.asyncio
async def test_price_and_orderbook_keys_get_default_ttl():
    """Both keys expire with the repo-wide 60s default."""
    fake = _FakeAsyncRedis()
    feed = _make_feed(fake)
    await feed._write_redis("PMABC:UP", [(0.4, 10.0)], [(0.6, 12.0)], 0.4, 0.6)

    assert fake.ttls["polymarket:PMABC:UP"] == 60
    assert fake.ttls["polymarket_ob:PMABC:UP"] == 60


@pytest.mark.asyncio
async def test_ttl_is_configurable():
    """redis_ttl from config flows through to expire()."""
    fake = _FakeAsyncRedis()
    feed = _make_feed(fake, redis_ttl=30)
    await feed._write_redis("PMABC:UP", [(0.4, 10.0)], [(0.6, 12.0)], 0.4, 0.6)

    assert fake.ttls["polymarket:PMABC:UP"] == 30
    assert fake.ttls["polymarket_ob:PMABC:UP"] == 30


# --- Issue: dashboard / control_interface wiring -----------------------------

def test_polymarket_registered_in_dashboard_source():
    """web_dashboard._get_services_info must expose polymarket_market."""
    src = open(os.path.join(PROJECT_ROOT, "web_dashboard.py")).read()
    assert "'polymarket_market'" in src
    assert "'redis_prefix': 'polymarket'" in src


def test_polymarket_prefix_in_control_interface_counts():
    """Both get_all_data_counts and *_breakdown must include the polymarket prefix."""
    src = open(os.path.join(PROJECT_ROOT, "core/control_interface.py")).read()
    # One occurrence per prefix list (get_all_data_counts + get_all_data_counts_breakdown).
    assert src.count("'polymarket'") >= 2
