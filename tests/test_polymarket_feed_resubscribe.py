"""Regression: the WS feed must reconnect/resubscribe when the watch-set changes
EVEN IF the currently-subscribed market is silent (sends no frames).

Bug (observed 2026-06-07): a freshly-selected Polymarket market showed an empty
Order Book and "No trades yet". Root cause — run_once()'s consumer loop only
re-checked the `refresh_needed` flag when a new WS frame arrived. Quiet /
near-resolved / rolled-over market windows emit no frames, so the consumer
blocked on `async for raw in ws` forever; the background _refresh_loop set
`refresh_needed` and exited, but nothing observed it. The new (live) market was
therefore never subscribed until an UNRELATED ~hourly `1011 keepalive ping
timeout` happened to tear the socket down. The price-history REST loop kept
working throughout, which is why the probability chart rendered while the
order book / trades stayed empty.

Fix: when the watch-set changes, _refresh_loop must close the socket so the
consumer unblocks immediately and run_forever reconnects with the new token set.
"""
import asyncio
import json
import logging

import pytest

import services.polymarket.market_feed as mf
from services.polymarket.market_feed import PolymarketMarketFeed


class _FakeAsyncRedis:
    def __init__(self):
        self.hashes = {}
        self.ttls = {}

    async def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return True

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True


class _ScriptedWS:
    """Fake CLOB market WebSocket.

    Delivers any `frames` in order, then models an OPEN-BUT-SILENT socket: a
    further read blocks until close() is called (mirroring a real connection to a
    quiet market that emits nothing yet stays open). Set end_when_silent=True to
    instead end the stream cleanly once frames are exhausted (models a server that
    closes after its messages).
    """

    def __init__(self, frames=None, end_when_silent=False):
        self.sent = []
        self.closed = False
        self._frames = list(frames or [])
        self._end_when_silent = end_when_silent
        self._wake = asyncio.Event()  # set by close() to unblock a silent read

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self, *args, **kwargs):
        self.closed = True
        self._wake.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._end_when_silent:
            raise StopAsyncIteration
        await self._wake.wait()  # open but silent: unblocks only on close()
        raise StopAsyncIteration


def _feed(redis):
    # scan_interval=0 → the background refresh loop reacts immediately (no real
    # wall-clock wait) so the test is fast and deterministic.
    return PolymarketMarketFeed(
        redis=redis, logger=logging.getLogger("t"), redis_ttl=60, scan_interval=0,
    )


@pytest.mark.asyncio
async def test_run_once_returns_on_token_change_when_socket_is_silent(monkeypatch):
    """The wedge reproduction: silent socket + a watch-set change mid-connection.

    run_once() MUST return (so run_forever can reconnect and resubscribe to the
    newly-selected market). With the bug it blocks forever and wait_for times out.
    """
    ws = _ScriptedWS(frames=[])  # never delivers a frame
    monkeypatch.setattr(mf.websockets, "connect", lambda *a, **k: ws)

    feed = _feed(_FakeAsyncRedis())
    calls = {"n": 0}

    async def fake_refresh():
        calls["n"] += 1
        # 1st call (pre-connect): subscribe set A. 2nd call (refresh loop): the
        # user selected a different market → set B → token set changed.
        feed._token_ticker = {"0xA": "PMA:UP"} if calls["n"] == 1 else {"0xB": "PMB:UP"}

    monkeypatch.setattr(feed, "_refresh_registry", fake_refresh)

    await asyncio.wait_for(feed.run_once(), timeout=2.0)

    assert ws.sent, "feed never sent a subscribe frame"
    assert json.loads(ws.sent[0]) == {"type": "MARKET", "assets_ids": ["0xA"]}
    assert ws.closed, "feed must close the socket on a token-set change to force resubscribe"


@pytest.mark.asyncio
async def test_run_once_still_handles_delivered_frames(monkeypatch):
    """Regression guard: the consumer must still process frames it does receive."""
    trade = json.dumps({
        "event_type": "last_trade_price", "asset_id": "0xA",
        "price": "0.42", "side": "BUY", "size": "7", "timestamp": "1700000000000",
    })
    ws = _ScriptedWS(frames=[trade], end_when_silent=True)
    monkeypatch.setattr(mf.websockets, "connect", lambda *a, **k: ws)

    redis = _FakeAsyncRedis()
    feed = _feed(redis)

    async def fake_refresh():
        feed._token_ticker = {"0xA": "PMA:UP"}

    monkeypatch.setattr(feed, "_refresh_registry", fake_refresh)

    await asyncio.wait_for(feed.run_once(), timeout=2.0)

    assert "polymarket_trades:PMA:UP" in redis.hashes
