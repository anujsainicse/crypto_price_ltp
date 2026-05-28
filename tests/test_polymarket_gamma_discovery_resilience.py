"""Resilience tests for list_active_legs against Gamma's date-filter 500 bug.

Gamma (gamma-api.polymarket.com) intermittently returns HTTP 500 for /markets
queries that include end_date_min/start_date_min. The same query WITHOUT the
date filter returns 200, so discovery must fall back instead of aborting the
whole cycle (which previously left polymarket:discovery:active unwritten).
"""
import asyncio
import logging

import aiohttp

from services.polymarket import gamma_discovery as gd

# A crypto Up/Down market that parse_market_to_legs accepts (slug matches _KIND_RE).
_CRYPTO_MARKET = {
    "conditionId": "0xdeadbeef",
    "slug": "btc-updown-5m-1778567100",
    "outcomes": '["Up","Down"]',
    "clobTokenIds": '["tok_up","tok_down"]',
    "tickSize": "0.01",
    "active": True,
    "closed": False,
    "negRisk": False,
    "question": "Bitcoin Up or Down 5m",
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
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status, message="mock"
            )

    async def json(self):
        return self._payload


def _session_factory(handler):
    class _FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, params=None):
            return handler(params or {})

    return _FakeSession


def test_falls_back_to_undated_query_when_gamma_500s_on_date_filter(monkeypatch, caplog):
    """A 500 on the date-filtered query must trigger a retry without the date
    filter (which returns 200), not abort the whole discovery cycle — and must
    emit a warning so the degraded query is visible to operators."""
    calls = {"dated": 0, "undated": 0}

    def handler(params):
        if "end_date_min" in params or "start_date_min" in params:
            calls["dated"] += 1
            return _FakeResp(500, None)
        calls["undated"] += 1
        return _FakeResp(200, [_CRYPTO_MARKET])

    monkeypatch.setattr(gd.aiohttp, "ClientSession", _session_factory(handler))

    with caplog.at_level(logging.WARNING, logger="services.polymarket.gamma_discovery"):
        legs = asyncio.run(gd.list_active_legs())

    assert legs, "expected fallback (undated query) to yield legs despite the 500"
    assert {leg["outcome"] for leg in legs} == {"UP", "DOWN"}
    assert calls["dated"] >= 1 and calls["undated"] >= 1
    assert any(
        "degraded scope" in r.getMessage() for r in caplog.records
    ), "fallback to the undated query must log a warning"


def test_uses_dated_query_result_when_gamma_is_healthy(monkeypatch):
    """When Gamma returns 200 for the date-filtered query, no fallback occurs."""
    calls = {"dated": 0, "undated": 0}

    def handler(params):
        if "end_date_min" in params or "start_date_min" in params:
            calls["dated"] += 1
            return _FakeResp(200, [_CRYPTO_MARKET])
        calls["undated"] += 1
        return _FakeResp(200, [])

    monkeypatch.setattr(gd.aiohttp, "ClientSession", _session_factory(handler))

    legs = asyncio.run(gd.list_active_legs())

    assert {leg["outcome"] for leg in legs} == {"UP", "DOWN"}
    assert calls["undated"] == 0, "no fallback should happen when the dated query is 200"
