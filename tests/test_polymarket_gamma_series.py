"""Targeted Gamma /events series queries surface the sparse 1h/4h up/down markets.

The high-churn 5m/15m markets saturate the first 100 rows of /markets (Gamma's
per-response cap), so the sparse 1h/4h markets are fetched via /events?series_slug=
instead — the /events endpoint honours series_slug while /markets ignores it
(verified live 2026-06-05). Ported from polybot's market_discovery.
"""
import asyncio
import json
import unittest.mock as mock

import aiohttp
from yarl import URL

from services.polymarket import gamma_discovery as gd


def _binary_market(cond: str, slug: str) -> dict:
    return {
        "conditionId": cond,
        "slug": slug,
        "outcomes": '["Up","Down"]',
        "clobTokenIds": f'["{cond}_u","{cond}_d"]',
        "tickSize": "0.01",
        "active": True,
        "closed": False,
        "negRisk": False,
        "question": slug,
    }


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            url = URL(f"{gd.GAMMA_HOST}/events")
            raise aiohttp.ClientResponseError(
                request_info=aiohttp.RequestInfo(url, "GET", (), url),
                history=(), status=self.status, message="mock",
            )

    async def json(self):
        return self._payload


def _session_factory(handler):
    """Fake aiohttp.ClientSession whose .get(url, params) defers to handler(url, params)."""
    class _FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, params=None):
            return handler(url, params or {})

    return _FakeSession


def test_series_slug_maps_cover_every_active_asset():
    assets = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE"}
    assert set(gd.HOURLY_SERIES_SLUGS) == assets
    assert set(gd.FOUR_HOUR_SERIES_SLUGS) == assets
    # Polymarket naming inconsistency: SOL hourly is spelled out, 4h is the short code.
    assert gd.HOURLY_SERIES_SLUGS["SOL"] == "solana-up-or-down-hourly"
    assert gd.FOUR_HOUR_SERIES_SLUGS["SOL"] == "sol-up-or-down-4h"


def test_list_series_legs_flattens_events_into_1h_and_4h_legs():
    """/events returns events that each wrap one inner market; the hourly slug maps
    to interval 1h and the aligned-ts 4h slug to interval 4h."""
    eth_1h = _binary_market("0xeth1h", "ethereum-up-or-down-june-5-2026-7am-et")
    eth_4h = _binary_market("0xeth4h", "eth-updown-4h-1780646400")

    def handler(url, params):
        assert url.endswith("/events"), "series query must hit /events, not /markets"
        slug = params.get("series_slug")
        if slug == "eth-up-or-down-hourly":
            return _FakeResp(200, [{"markets": [eth_1h]}])
        if slug == "eth-up-or-down-4h":
            return _FakeResp(200, [{"markets": [eth_4h]}])
        return _FakeResp(200, [])

    with mock.patch.object(gd, "HOURLY_SERIES_SLUGS", {"ETH": "eth-up-or-down-hourly"}), \
         mock.patch.object(gd, "FOUR_HOUR_SERIES_SLUGS", {"ETH": "eth-up-or-down-4h"}), \
         mock.patch.object(gd.aiohttp, "ClientSession", _session_factory(handler)):
        legs = asyncio.run(gd.list_series_legs())

    by_interval: dict[str, list[dict]] = {}
    for leg in legs:
        by_interval.setdefault(leg["interval"], []).append(leg)
    assert set(by_interval) == {"1h", "4h"}
    assert all(leg["asset"] == "ETH" for leg in legs)
    assert {leg["outcome"] for leg in by_interval["1h"]} == {"UP", "DOWN"}
    assert {leg["outcome"] for leg in by_interval["4h"]} == {"UP", "DOWN"}


def test_list_series_legs_isolates_per_series_failure():
    """A 5xx on one series must not abort the whole long-interval refresh."""
    good = _binary_market("0xbtc1h", "bitcoin-up-or-down-june-5-2026-7am-et")

    def handler(url, params):
        if params.get("series_slug") == "btc-up-or-down-hourly":
            return _FakeResp(200, [{"markets": [good]}])
        return _FakeResp(500, None)  # every other series errors

    with mock.patch.object(
        gd, "HOURLY_SERIES_SLUGS",
        {"BTC": "btc-up-or-down-hourly", "ETH": "eth-up-or-down-hourly"},
    ), mock.patch.object(
        gd, "FOUR_HOUR_SERIES_SLUGS", {"BTC": "btc-up-or-down-4h"},
    ), mock.patch.object(gd.aiohttp, "ClientSession", _session_factory(handler)):
        legs = asyncio.run(gd.list_series_legs())

    assert {leg["slug"] for leg in legs} == {"bitcoin-up-or-down-june-5-2026-7am-et"}


def test_list_series_legs_dedups_across_series():
    """If the same condition_id appears in two series (e.g. a slug landing in both
    maps), it is emitted once (two legs), not twice."""
    shared = _binary_market("0xdup", "ethereum-up-or-down-june-5-2026-7am-et")

    def handler(url, params):
        return _FakeResp(200, [{"markets": [shared]}])

    with mock.patch.object(gd, "HOURLY_SERIES_SLUGS", {"ETH": "eth-up-or-down-hourly"}), \
         mock.patch.object(gd, "FOUR_HOUR_SERIES_SLUGS", {"ETH": "eth-up-or-down-4h"}), \
         mock.patch.object(gd.aiohttp, "ClientSession", _session_factory(handler)):
        legs = asyncio.run(gd.list_series_legs())

    assert len(legs) == 2  # one market = two legs, despite appearing in both series


def test_merge_legs_dedup_prefers_short_and_drops_duplicate_markets():
    def _legs(cond):
        return [
            {"condition_id": cond, "outcome": "UP"},
            {"condition_id": cond, "outcome": "DOWN"},
        ]

    short = _legs("0xA") + _legs("0xB")
    long = _legs("0xB") + _legs("0xC")  # B duplicates short; C is new
    merged = gd.merge_legs_dedup(short, long)

    cids = [leg["condition_id"] for leg in merged]
    assert len(merged) == 6
    assert cids.count("0xA") == 2
    assert cids.count("0xB") == 2  # long's B pair dropped, not doubled
    assert cids.count("0xC") == 2  # long's C pair added
    # short legs come first (priority)
    assert cids[:4] == ["0xA", "0xA", "0xB", "0xB"]


class _RaisingCtx:
    """Async context manager whose __aenter__ raises — simulates a request that
    fails before a response object exists (e.g. aiohttp's total ClientTimeout,
    which raises a plain asyncio.TimeoutError, NOT an aiohttp.ClientError)."""
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


class _BadJsonResp(_FakeResp):
    """HTTP 200 whose body is not valid JSON — resp.json() raises
    json.JSONDecodeError (a ValueError subclass, NOT an aiohttp.ClientError)."""
    async def json(self):
        raise json.JSONDecodeError("Expecting value", "", 0)


def test_list_series_legs_isolates_per_series_timeout():
    """A total-timeout (asyncio.TimeoutError) on one series must not abort the
    whole long-interval refresh — the other series' legs must still come back."""
    good = _binary_market("0xbtc1h", "bitcoin-up-or-down-june-5-2026-7am-et")

    def handler(url, params):
        if params.get("series_slug") == "btc-up-or-down-hourly":
            return _FakeResp(200, [{"markets": [good]}])
        return _RaisingCtx(asyncio.TimeoutError())  # every other series times out

    with mock.patch.object(
        gd, "HOURLY_SERIES_SLUGS",
        {"BTC": "btc-up-or-down-hourly", "ETH": "eth-up-or-down-hourly"},
    ), mock.patch.object(
        gd, "FOUR_HOUR_SERIES_SLUGS", {"BTC": "btc-up-or-down-4h"},
    ), mock.patch.object(gd.aiohttp, "ClientSession", _session_factory(handler)):
        legs = asyncio.run(gd.list_series_legs())

    assert {leg["slug"] for leg in legs} == {"bitcoin-up-or-down-june-5-2026-7am-et"}


def test_list_series_legs_isolates_per_series_bad_json():
    """A malformed-JSON HTTP 200 (json.JSONDecodeError) on one series must not
    abort the whole long-interval refresh."""
    good = _binary_market("0xbtc1h", "bitcoin-up-or-down-june-5-2026-7am-et")

    def handler(url, params):
        if params.get("series_slug") == "btc-up-or-down-hourly":
            return _FakeResp(200, [{"markets": [good]}])
        return _BadJsonResp(200, None)  # every other series returns junk

    with mock.patch.object(
        gd, "HOURLY_SERIES_SLUGS",
        {"BTC": "btc-up-or-down-hourly", "ETH": "eth-up-or-down-hourly"},
    ), mock.patch.object(
        gd, "FOUR_HOUR_SERIES_SLUGS", {"BTC": "btc-up-or-down-4h"},
    ), mock.patch.object(gd.aiohttp, "ClientSession", _session_factory(handler)):
        legs = asyncio.run(gd.list_series_legs())

    assert {leg["slug"] for leg in legs} == {"bitcoin-up-or-down-june-5-2026-7am-et"}
