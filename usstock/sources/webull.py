"""Public overnight snapshots; Webull does not disclose each trade's time."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import isfinite

from common.http import fetch_text

from ..calendar import edition_date, overnight_window, previous_trading_day
from ..models import MEGA_NAMES, SECTOR_NAMES, Quote, new_york_time

NAMES = {**MEGA_NAMES, **SECTOR_NAMES, "SPY": "标普ETF"}
URL = (
    "https://quotes-gw.webullfintech.com/api/wlas/ranking/overnight"
    "?regionId=6&brokerId=8&order=overnightVolume&direction=-1&pageIndex=1&pageSize=1000"
)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _window(now: datetime) -> tuple[float, float] | None:
    now = new_york_time(now)
    try:
        start, end = overnight_window(edition_date("premarket", now))
    except ValueError:
        return None
    limits = start.timestamp(), end.timestamp()
    return limits if limits[0] <= now.timestamp() < limits[1] else None


def parse_snapshots(payload: object, now: datetime, observed_at: datetime) -> dict[str, Quote]:
    window = _window(now)
    if window is None or observed_at.tzinfo is None:
        return {}
    observed_at = observed_at.astimezone(timezone.utc)
    if not window[0] <= observed_at.timestamp() < window[1]:
        return {}
    if not isinstance(payload, dict) or payload.get("rankType") != "overnight":
        return {}
    rows = payload.get("data")
    ranking_time = _number(payload.get("latestUpdateTime"))
    # Ranking refresh time prevents prior-night reuse; it is not a trade timestamp.
    if (
        not isinstance(rows, list) or ranking_time is None
        or not window[0] <= ranking_time / 1000 <= observed_at.timestamp()
    ):
        return {}
    try:
        basis_date = previous_trading_day(edition_date("premarket", now))
    except ValueError:
        basis_date = None
    quotes: dict[str, Quote] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker, values = row.get("ticker"), row.get("values")
        if not isinstance(ticker, dict) or not isinstance(values, dict):
            continue
        symbol, ticker_id = ticker.get("symbol"), ticker.get("tickerId")
        if (
            not isinstance(symbol, str) or symbol not in NAMES
            or ticker.get("regionId") != 6 or ticker.get("currencyCode") != "USD"
            or ticker.get("type") not in (2, 3)
            or not isinstance(ticker_id, int) or isinstance(ticker_id, bool) or ticker_id <= 0
            or values.get("tickerId") != ticker_id or values.get("dt") != "overnight"
        ):
            continue
        price = _number(values.get("overnightPrice"))
        if price is None or price <= 0:
            continue
        basis = _number(values.get("close"))
        ratio = _number(values.get("overnightChangeRatio"))
        pct = (price / basis - 1) * 100 if basis is not None and basis > 0 else None
        if (
            pct is None or not isfinite(pct) or ratio is None or basis_date is None
            or abs(pct - ratio * 100) > 0.05
        ):
            basis, pct = None, None
        quotes[symbol] = Quote(
            symbol=symbol, name=NAMES[symbol], last=price, pct=pct,
            previous_close=basis, session="overnight", source="Webull",
            previous_date=basis_date if basis is not None else None, observed_at=observed_at,
        )
    return quotes


def night_snapshots(now: datetime) -> dict[str, Quote]:
    if _window(now) is None:
        return {}
    raw = fetch_text(URL, referer="https://app.webull.com/", timeout=10, retries=0)
    observed_at = datetime.now(timezone.utc)
    return parse_snapshots(json.loads(raw), now, observed_at)
