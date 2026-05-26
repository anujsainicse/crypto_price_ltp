"""Unit tests for the LTP Polymarket Gamma discovery client."""
import hashlib

import pytest

from services.polymarket.gamma_discovery import (
    make_ticker,
    normalize_outcome,
    classify_slug,
    parse_market_to_legs,
)


def test_make_ticker_is_deterministic_and_matches_backend_format():
    cond = "0xabc123"
    t_up = make_ticker(cond, "Up", 0)
    t_down = make_ticker(cond, "Down", 1)
    assert t_up.startswith("PM") and ":UP" in t_up
    assert t_down.startswith("PM") and ":DOWN" in t_down
    assert t_up.split(":")[0] == t_down.split(":")[0]
    assert make_ticker(cond, "Up", 0) == t_up
    assert len(t_up) <= 20
    # Pin the exact vector: must byte-match the scalper backend make_ticker.
    assert t_up == "PM" + hashlib.sha1(b"0xabc123").hexdigest()[:8].upper() + ":UP"


def test_normalize_outcome():
    assert normalize_outcome("Up", 0) == "UP"
    assert normalize_outcome("Down", 1) == "DOWN"
    assert normalize_outcome("Yes", 0) == "YES"
    assert normalize_outcome("weird", 1) == "B"


def test_classify_slug_matches_crypto_updown_only():
    assert classify_slug("btc-updown-5m-1778567100") == "BTC_UD_5M"
    assert classify_slug("bitcoin-up-or-down-may-13-2026-5am-et") == "BTC_UD_1H"
    assert classify_slug("will-the-fed-cut-rates") is None


def test_classify_slug_hourly_unknown_asset_returns_none():
    assert classify_slug("avax-up-or-down-may-13-2026-5am-et") is None


def test_parse_market_to_legs_builds_two_legs():
    raw = {
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
    legs = parse_market_to_legs(raw)
    assert legs is not None
    assert len(legs) == 2
    assert {l["outcome"] for l in legs} == {"UP", "DOWN"}
    up = next(l for l in legs if l["outcome"] == "UP")
    assert up["token_id"] == "tok_up"
    assert up["condition_id"] == "0xdeadbeef"
    assert up["tick_size"] == 0.01
    assert up["slug"] == "btc-updown-5m-1778567100"
    assert parse_market_to_legs({**raw, "slug": "will-fed-cut"}) is None
