"""Unit tests for the LTP Polymarket Gamma discovery client."""
import hashlib

import pytest

from services.polymarket.gamma_discovery import (
    make_ticker,
    normalize_outcome,
    classify_slug,
    parse_market_to_legs,
    parse_kind,
    derive_windows,
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


def test_parse_kind_short_horizon():
    assert parse_kind("btc-updown-5m-1779881400") == ("BTC", "5m")
    assert parse_kind("doge-updown-15m-1779881400") == ("DOGE", "15m")


def test_parse_kind_hourly_shape():
    assert parse_kind("ethereum-up-or-down-may-29-2026-7am-et") == ("ETH", "1h")


def test_parse_kind_non_crypto_returns_none():
    assert parse_kind("will-it-rain-in-nyc-tomorrow") is None
    assert parse_kind("") is None


def test_derive_windows_start_is_end_minus_interval():
    ws, we = derive_windows("btc-updown-5m-1779881400", "2026-05-27T07:35:00Z", "5m")
    assert we == "2026-05-27T07:35:00Z"
    assert ws == "2026-05-27T07:30:00Z"


def test_derive_windows_fallback_end_from_slug_epoch():
    ws, we = derive_windows("btc-updown-5m-1779881400", None, "5m")
    assert we == "2026-05-27T11:30:00Z"
    assert ws == "2026-05-27T11:25:00Z"


def test_derive_windows_naive_date_treated_as_utc():
    ws, we = derive_windows("btc-updown-5m-1779881400", "2026-05-27T07:35:00", "5m")
    assert we == "2026-05-27T07:35:00Z"
    assert ws == "2026-05-27T07:30:00Z"


def test_derive_windows_unknown_interval_skips_start():
    ws, we = derive_windows("btc-updown-5m-1779881400", "2026-05-27T07:35:00Z", "99x")
    assert we == "2026-05-27T07:35:00Z"
    assert ws is None


def _crypto_raw():
    return {
        "slug": "btc-updown-5m-1779881400",
        "conditionId": "0xcond",
        "clobTokenIds": '["111","222"]',
        "outcomes": '["Up","Down"]',
        "tickSize": "0.01",
        "negRisk": False,
        "active": True,
        "closed": False,
        "question": "Bitcoin Up or Down - May 27, 7:30AM-7:35AM ET",
        "endDate": "2026-05-27T07:35:00Z",
        # startDate is the market CREATION time — here a full DAY before endDate,
        # mirroring the real Gamma behaviour. It must NOT drive window_start, so
        # it is deliberately distinct from (endDate - interval) to catch a
        # regression that reintroduces startDate-based derivation.
        "startDate": "2026-05-26T07:30:00Z",
    }


def test_parse_market_to_legs_includes_taxonomy_fields():
    legs = parse_market_to_legs(_crypto_raw())
    assert legs is not None and len(legs) == 2
    for leg in legs:
        assert leg["asset"] == "BTC"
        assert leg["interval"] == "5m"
        # window_start is endDate - 5m (07:30 on the 27th), NOT the day-earlier
        # startDate (07:30 on the 26th) — proving Gamma startDate is ignored.
        assert leg["window_start"] == "2026-05-27T07:30:00Z"
        assert leg["window_end"] == "2026-05-27T07:35:00Z"
