"""Cboe 公开指数、VIX 和十年收益率；引用时间来自实际行情记录。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Callable

from common.http import fetch_text

from ..calendar import is_trading_day, previous_trading_day, session_close
from ..models import MarketData, Quote, finite_number, new_york_time

SYMBOLS = {"^GSPC": "SPX", "^RUT": "RUT", "^VIX": "VIX", "^TNX": "TNX"}
REFERER = "https://www.cboe.com/"
BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes"


def _data(payload: object, symbol: str) -> object:
    if not isinstance(payload, dict) or payload.get("symbol") != f"_{SYMBOLS[symbol]}":
        raise ValueError("Cboe 标的与请求不一致")
    return payload.get("data")


def parse_history(payload: object, symbol: str, name: str, now: datetime) -> dict[date, Quote]:
    now = new_york_time(now)
    rows = _data(payload, symbol)
    if not isinstance(rows, list):
        raise ValueError("Cboe 日线格式错误")
    divisor = 10 if symbol == "^TNX" else 1
    prices = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            day = date.fromisoformat(row.get("date", ""))
            if not is_trading_day(day) or session_close(day).timestamp() > now.timestamp():
                continue
        except (TypeError, ValueError):
            continue
        value = finite_number(row.get("close"), positive=True)
        if value is not None:
            prices[day] = value / divisor
    quotes = {}
    for day, price in prices.items():
        try:
            prior_day = previous_trading_day(day)
        except ValueError:
            prior_day = None
        prior = prices.get(prior_day)
        quotes[day] = Quote(
            symbol, name, price,
            pct=finite_number((price / prior - 1) * 100) if prior is not None else None,
            previous_close=prior, previous_date=prior_day if prior is not None else None,
            asof=session_close(day), source="Cboe日线", unit="%" if symbol == "^TNX" else "点",
            observed_at=now, delay_minutes=15,
        )
    return quotes


def parse_quote(
    payload: object, symbol: str, name: str, history: dict[date, Quote], now: datetime,
) -> Quote | None:
    now = new_york_time(now)
    data = _data(payload, symbol)
    if not isinstance(data, dict) or data.get("symbol") != f"^{SYMBOLS[symbol]}":
        raise ValueError("Cboe 行情标的与请求不一致")
    price = finite_number(data.get("current_price"), positive=True)
    try:
        stamp = new_york_time(datetime.fromisoformat(data.get("last_trade_time", "")))
        if stamp.timestamp() > now.timestamp() or not is_trading_day(stamp.date()):
            return None
        prior_day = previous_trading_day(stamp.date())
    except (TypeError, ValueError):
        return None
    if price is None:
        return None
    if symbol == "^TNX":
        price /= 10
    prior = history.get(prior_day)
    return Quote(
        symbol, name, price,
        pct=finite_number((price / prior.last - 1) * 100) if prior else None,
        previous_close=prior.last if prior else None,
        previous_date=prior_day if prior else None,
        asof=stamp, session="reference" if symbol in {"^VIX", "^TNX"} else "regular",
        source="Cboe", unit="%" if symbol == "^TNX" else "点", observed_at=now, delay_minutes=15,
    )


def load(
    symbol: str, name: str, now: datetime, *, clock: Callable[[], datetime] | None = None,
) -> MarketData:
    result = MarketData()
    code = SYMBOLS.get(symbol)
    if code is None:
        return result
    now = new_york_time(now)
    history = {}
    try:
        payload = json.loads(fetch_text(
            f"{BASE_URL}/charts/historical/_{code}.json", REFERER, timeout=10, retries=0,
        ))
        history = parse_history(payload, symbol, name, clock() if clock else now)
        if history:
            result.completed[symbol] = result.quotes[symbol] = history[max(history)]
    except (OSError, ValueError) as exc:
        result.notes.append(f"Cboe {code} 日线暂不可用（{type(exc).__name__}）")
    try:
        payload = json.loads(fetch_text(f"{BASE_URL}/quotes/_{code}.json", REFERER, timeout=10, retries=0))
        quote = parse_quote(payload, symbol, name, history, clock() if clock else now)
        if quote:
            result.quotes[symbol] = quote
    except (OSError, ValueError) as exc:
        result.notes.append(f"Cboe {code} 行情暂不可用（{type(exc).__name__}）")
    return result
