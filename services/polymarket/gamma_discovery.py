"""Gamma API discovery for Polymarket crypto Up/Down markets.

Ported from polybot (~/random/polybot/backend/polybot/polymarket/gamma_client.py)
and scalper backend (app/services/polymarket/gamma_client.py). Emits legs in the
EXACT shape the scalper backend registry expects, with byte-identical tickers.

Tickers MUST equal backend make_ticker(): PM<sha1(condition_id)[:8].upper()>:<OUTCOME>.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
from typing import Any

import aiohttp

GAMMA_HOST = "https://gamma-api.polymarket.com"

# Gamma's /markets endpoint hard-caps every response at 100 rows regardless of the
# `limit` query param, and the high-churn 5m/15m crypto Up/Down markets (created
# every few minutes across every asset) saturate those first 100 rows. The sparse
# 1h/4h markets sort hundreds of rows back and never appear, so /markets is used
# only for the short-interval set; the long-interval markets are fetched by
# targeted /events?series_slug= queries (see list_series_legs).
#
# Asset → Polymarket series slug for the 1-hour up/down markets. Their slugs are
# wall-clock date strings (`ethereum-up-or-down-june-5-2026-7am-et`) that can't be
# derived from `now`, so they must be enumerated via the series. Ported from
# polybot (backend/polybot/polymarket/market_discovery.py). NOTE Polymarket's
# naming is inconsistent: SOL hourly uses the spelled-out `solana-` prefix while
# 4h uses the short `sol-` code. The /events endpoint honours `series_slug`;
# /markets ignores it (verified 2026-05-15, re-verified live 2026-06-05).
HOURLY_SERIES_SLUGS: dict[str, str] = {
    "BTC": "btc-up-or-down-hourly",
    "ETH": "eth-up-or-down-hourly",
    "SOL": "solana-up-or-down-hourly",
    "DOGE": "doge-up-or-down-hourly",
    "BNB": "bnb-up-or-down-hourly",
    "XRP": "xrp-up-or-down-hourly",
    "HYPE": "hype-up-or-down-hourly",
}

# Asset → Polymarket series slug for the 4-hour up/down markets. 4H slugs are
# aligned-timestamp (`eth-updown-4h-1780646400`) but the generic /markets
# enumeration still buries them behind the 5m/15m churn, so the series query is
# the reliable source for the next/prev 4H windows. Verified live 2026-06-05.
FOUR_HOUR_SERIES_SLUGS: dict[str, str] = {
    "BTC": "btc-up-or-down-4h",
    "ETH": "eth-up-or-down-4h",
    "SOL": "sol-up-or-down-4h",
    "DOGE": "doge-up-or-down-4h",
    "BNB": "bnb-up-or-down-4h",
    "XRP": "xrp-up-or-down-4h",
    "HYPE": "hype-up-or-down-4h",
}

logger = logging.getLogger(__name__)

_KIND_RE = re.compile(r"^([a-z]+)-updown-(5m|15m|1h|4h|1d|1w|1mo|1y)-\d+$")
_HOURLY_RE = re.compile(
    r"^([a-z]+)-up-or-down-"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"-\d{1,2}-\d{4}-\d{1,2}(?:am|pm)-et$"
)
_ASSET_CODE: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL", "doge": "DOGE", "dogecoin": "DOGE",
    "bnb": "BNB", "xrp": "XRP", "hype": "HYPE", "hyperliquid": "HYPE",
}

_INTERVAL_SEC: dict[str, int] = {
    "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
    "1d": 86400, "1w": 604800, "1mo": 2592000, "1y": 31536000,
}


def parse_kind(slug: str) -> tuple[str, str] | None:
    """Return (asset_code, interval) e.g. ('BTC', '5m') for a crypto Up/Down
    slug, else None. Canonicalises the asset through _ASSET_CODE for BOTH slug
    shapes (used only to populate the new asset/interval fields — classify_slug
    keeps its own raw-prefix behaviour and is left untouched)."""
    s = (slug or "").lower()
    m = _KIND_RE.match(s)
    if m:
        # .upper() fallback is intentional: a not-yet-mapped short-horizon asset
        # still categorises off its raw prefix, unlike the hourly path below
        # which requires an explicit _ASSET_CODE entry.
        return _ASSET_CODE.get(m.group(1), m.group(1).upper()), m.group(2)
    m = _HOURLY_RE.match(s)
    if m:
        code = _ASSET_CODE.get(m.group(1))
        return (code, "1h") if code is not None else None
    return None


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d if d.tzinfo is not None else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _iso_z(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_windows(
    slug: str, end_date: str | None, interval: str | None
) -> tuple[str | None, str | None]:
    """(window_start, window_end) as UTC 'Z' ISO strings. window_end from
    Gamma endDate (fallback: slug trailing epoch). window_start is always
    window_end - interval (the kind defines the window length); Gamma
    startDate is the market CREATION time and is deliberately NOT used."""
    we = _parse_iso(end_date)
    if we is None:
        m = re.search(r"-(\d{10,})$", slug or "")
        if m:
            we = dt.datetime.fromtimestamp(int(m.group(1)), dt.timezone.utc)
    ws = we - dt.timedelta(seconds=_INTERVAL_SEC[interval]) if (we is not None and interval in _INTERVAL_SEC) else None
    return (_iso_z(ws) if ws else None, _iso_z(we) if we else None)


def classify_slug(slug: str) -> str | None:
    """Return market_kind (e.g. 'BTC_UD_5M') or None for non-target markets."""
    s = (slug or "").lower()
    m = _KIND_RE.match(s)
    if m:
        # Intentional asymmetry (faithful to polybot): the short-horizon
        # "-updown-" path uppercases the RAW asset prefix directly, while the
        # hourly path below is gated through _ASSET_CODE.
        return f"{m.group(1).upper()}_UD_{m.group(2).upper()}"
    m = _HOURLY_RE.match(s)
    if m:
        code = _ASSET_CODE.get(m.group(1))
        return f"{code}_UD_1H" if code is not None else None
    return None


def normalize_outcome(label: str, index: int = 0) -> str:
    """Map a human outcome label to a canonical code (UP/DOWN/YES/NO/A/B).

    Falls back to positional A/B for unknown labels (index=0 -> A, index=1+ -> B).
    """
    n = (label or "").strip().lower()
    if n.startswith("up"):
        return "UP"
    if n.startswith("down"):
        return "DOWN"
    if n in ("yes", "y"):
        return "YES"
    if n in ("no", "n"):
        return "NO"
    return "A" if index == 0 else "B"


def make_ticker(condition_id: str, outcome_label: str, index: int = 0) -> str:
    code = hashlib.sha1(condition_id.encode()).hexdigest()[:8].upper()
    return f"PM{code}:{normalize_outcome(outcome_label, index)}"


def _to_list(v: Any) -> list[Any]:
    """Normalise a Gamma field that may be a proper list or a JSON-encoded string.

    Polymarket's Gamma API returns `outcomes` and `clobTokenIds` as either a
    native JSON array or a JSON-encoded string (e.g. '["Up","Down"]'). Both
    forms must be handled.
    """
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def parse_market_to_legs(raw: dict[str, Any]) -> list[dict] | None:
    """Convert a Gamma /markets row into 2 scalper legs, or None if not a
    binary crypto Up/Down market."""
    slug = raw.get("slug", "")
    if not slug or classify_slug(slug) is None:
        return None
    token_ids = _to_list(raw.get("clobTokenIds"))
    outcomes = _to_list(raw.get("outcomes"))
    if len(token_ids) != 2 or len(outcomes) != 2:
        return None
    cond = raw.get("conditionId") or ""
    if not cond:
        return None
    tick = float(raw.get("tickSize") or "0.01")
    if tick <= 0:
        tick = 0.01
    neg_risk = bool(raw.get("negRisk", False))
    active = bool(raw.get("active", True))
    closed = bool(raw.get("closed", False))
    title = raw.get("question") or raw.get("title") or slug
    kind = parse_kind(slug)
    asset = kind[0] if kind else None
    interval = kind[1] if kind else None
    window_start, window_end = derive_windows(slug, raw.get("endDate"), interval)

    legs: list[dict] = []
    for i, (tid, label) in enumerate(zip(token_ids, outcomes)):
        label_str = str(label)
        legs.append({
            "ticker": make_ticker(cond, label_str, i),
            "condition_id": cond,
            "token_id": str(tid),
            "outcome": normalize_outcome(label_str, i),
            "outcome_label": label_str,
            "title": title,
            "slug": slug,
            "tick_size": tick,
            "neg_risk": neg_risk,
            "active": active,
            "closed": closed,
            "asset": asset,
            "interval": interval,
            "window_start": window_start,
            "window_end": window_end,
        })
    return legs


async def _get_markets(session: aiohttp.ClientSession, params: dict, date_key: str) -> list[dict]:
    """GET /markets, tolerating Gamma's date-filter 500 bug.

    gamma-api.polymarket.com intermittently returns HTTP 500 for /markets
    queries carrying end_date_min/start_date_min. The identical query WITHOUT
    the date filter (still ordered by endDate/startDate) returns 200 and still
    surfaces the imminent/recent markets we need, so on a 5xx we retry once
    without the date filter rather than aborting the whole discovery cycle.
    """
    async with session.get(f"{GAMMA_HOST}/markets", params=params) as resp:
        if resp.status < 500 or date_key not in params:
            resp.raise_for_status()
            return await resp.json()
        status = resp.status
    # First response was a 5xx on a date-filtered query (released above); retry
    # the same query without the date filter. Warn so the degraded query is
    # visible — Gamma's date-filter 500 is intermittent and would otherwise be
    # silent behind the caller's normal "wrote N legs to catalog" success line.
    logger.warning(
        "pm_discovery: Gamma /markets returned %s on the %s-filtered query; "
        "retrying without the date filter (degraded scope)",
        status,
        date_key,
    )
    fallback = {k: v for k, v in params.items() if k != date_key}
    async with session.get(f"{GAMMA_HOST}/markets", params=fallback) as resp2:
        resp2.raise_for_status()
        return await resp2.json()


def _rows_to_legs(rows: list[dict]) -> list[dict]:
    """Flatten Gamma /markets (or /events inner-market) rows to legs, deduped by
    conditionId and filtered to crypto Up/Down via parse_market_to_legs."""
    seen: set[str] = set()
    legs: list[dict] = []
    for raw in rows:
        cid = raw.get("conditionId", "")
        if cid and cid in seen:
            continue
        # Rows with no conditionId aren't deduped here (empty cid can't seed the
        # set); they're filtered out by parse_market_to_legs's `if not cond` guard.
        parsed = parse_market_to_legs(raw)
        if parsed is not None:
            if cid:
                seen.add(cid)
            legs.extend(parsed)
    return legs


def merge_legs_dedup(short_legs: list[dict], long_legs: list[dict]) -> list[dict]:
    """Merge short-interval and long-interval legs into one catalog list, deduped
    by condition_id (first occurrence wins, so short-interval rows take priority).

    Both legs of a market share a condition_id, so a market present in short_legs
    suppresses BOTH of its long_legs copies (and vice versa) — never half a pair."""
    seen = {leg.get("condition_id") for leg in short_legs if leg.get("condition_id")}
    merged = list(short_legs)
    for leg in long_legs:
        if leg.get("condition_id") in seen:
            continue
        merged.append(leg)
    return merged


async def list_active_legs(*, limit: int = 1000, timeout_sec: float = 8.0) -> list[dict]:
    """Short-interval source: two-query Gamma /markets merge (imminent endDate asc
    + recent startDate desc), deduped by conditionId, filtered to crypto Up/Down,
    flattened to legs.

    This captures the high-churn 5m/15m markets that dominate the first 100 rows
    (Gamma's per-response cap). The sparse 1h/4h markets are fetched separately by
    list_series_legs — paginating /markets to dredge them out is wasteful and was
    rejected in favour of targeted /events series queries."""
    now = dt.datetime.now(dt.timezone.utc)
    end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_min = (now - dt.timedelta(minutes=1500)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params_imminent = {
        "closed": "false", "active": "true", "limit": limit,
        "order": "endDate", "ascending": "true", "end_date_min": end_min,
    }
    params_recent = {
        "closed": "false", "active": "true", "limit": limit,
        "order": "startDate", "ascending": "false", "start_date_min": start_min,
    }

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        rows: list[dict] = []
        rows.extend(await _get_markets(session, params_imminent, "end_date_min"))
        rows.extend(await _get_markets(session, params_recent, "start_date_min"))

    return _rows_to_legs(rows)


async def _get_events_series(
    session: aiohttp.ClientSession, series_slug: str, end_min: str
) -> list[dict]:
    """GET /events for one series, returning its flattened inner-market rows.

    The /events endpoint honours `series_slug` (unlike /markets), so this returns
    exactly the upcoming markets of `series_slug`, soonest-first. Each event wraps
    one inner market. Returns [] on ANY per-series failure so one bad series can't
    abort the whole long-interval refresh — including the total ClientTimeout
    (which raises a plain asyncio.TimeoutError, NOT an aiohttp.ClientError) and a
    malformed-JSON 200 body (json.JSONDecodeError, a ValueError subclass)."""
    params = {
        "closed": "false", "active": "true", "limit": 10,
        "order": "endDate", "ascending": "true",
        "end_date_min": end_min, "series_slug": series_slug,
    }
    try:
        async with session.get(f"{GAMMA_HOST}/events", params=params) as resp:
            resp.raise_for_status()
            events = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning(
            "pm_discovery: Gamma /events series=%s failed: %s", series_slug, exc
        )
        return []
    rows: list[dict] = []
    for ev in events or []:
        rows.extend(ev.get("markets") or [])
    return rows


async def list_series_legs(*, timeout_sec: float = 8.0, concurrency: int = 4) -> list[dict]:
    """Long-interval source: targeted Gamma /events?series_slug= queries for every
    asset's 1h and 4h up/down series, flattened + deduped to legs.

    One small (limit=10) query per series returns exactly that series' upcoming
    markets, soonest-first — no /markets pagination, no row-budget luck. Queries
    run with bounded concurrency; a per-series failure is skipped, not fatal.
    gather(return_exceptions=True) is belt-and-suspenders: even an exception type
    _get_events_series does not catch can't abort the other series' results."""
    now = dt.datetime.now(dt.timezone.utc)
    end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    slugs = list(HOURLY_SERIES_SLUGS.values()) + list(FOUR_HOUR_SERIES_SLUGS.values())

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def _one(slug: str) -> list[dict]:
            async with sem:
                return await _get_events_series(session, slug, end_min)

        results = await asyncio.gather(
            *(_one(s) for s in slugs), return_exceptions=True
        )

    rows: list[dict] = []
    for series_rows in results:
        if isinstance(series_rows, BaseException):
            logger.warning("pm_discovery: series query raised: %s", series_rows)
            continue
        rows.extend(series_rows)
    return _rows_to_legs(rows)
