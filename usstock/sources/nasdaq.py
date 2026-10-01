"""Nasdaq 公开股票、ETF 日线与盘前、盘后成交，不提供夜盘。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from typing import Callable
from urllib.parse import urlencode

from common.http import fetch_text

from ..calendar import extended_close, is_trading_day, previous_trading_day, session_close, session_open
from ..models import MEGA_NAMES, NY, SECTOR_NAMES, MarketData, Quote, finite_number, new_york_time

ASSET_CLASSES = {
    **dict.fromkeys(MEGA_NAMES, "stocks"),
    **dict.fromkeys((*SECTOR_NAMES, "SPY", "QQQ", "DIA", "IWM"), "etf"),
}
REFERER = "https://www.nasdaq.com/"
BASE_URL = "https://api.nasdaq.com/api/quote"


def _number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    return finite_number(value, positive=positive)


def _data(payload: object, symbol: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Nasdaq 响应格式错误")
    status = payload.get("status")
    if isinstance(status, dict) and status.get("rCode") != 200:
        raise ValueError("Nasdaq 接口返回失败状态")
    data = payload.get("data")
    if data is None:
        return {}
    identity = data.get("symbol", symbol) if isinstance(data, dict) else None
    if not isinstance(identity, str) or identity.upper() != symbol:
        raise ValueError("Nasdaq 标的与请求不一致")
    return data


def parse_history(payload: object, symbol: str, name: str, now: datetime) -> dict[date, Quote]:
    """日线时间表示该交易日正式闭市，不表示最后一笔成交时钟。"""
    now = new_york_time(now)
    data = _data(payload, symbol)
    if data and data.get("symbol") != symbol:
        raise ValueError("Nasdaq 日线缺少正确标的")
    table = data.get("tradesTable")
    rows = table.get("rows") or [] if isinstance(table, dict) else []
    if not isinstance(rows, list):
        raise ValueError("Nasdaq 日线格式错误")
    prices: dict[date, tuple[float, float | None]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(row.get("date", ""), "%m/%d/%Y").date()
            if not is_trading_day(day) or session_close(day).timestamp() > now.timestamp():
                continue
        except (TypeError, ValueError):
            continue
        price = _number(row.get("close"), positive=True)
        volume = _number(row.get("volume"))
        if price is not None:
            prices[day] = (price, volume if volume is not None and volume >= 0 else None)
    quotes = {}
    for day, (price, volume) in prices.items():
        try:
            prior_day = previous_trading_day(day)
        except ValueError:
            prior_day = None
        prior = prices.get(prior_day)
        quotes[day] = Quote(
            symbol, name, price,
            pct=finite_number((price / prior[0] - 1) * 100) if prior else None,
            previous_close=prior[0] if prior else None,
            asof=session_close(day), volume=volume,
            previous_volume=prior[1] if prior else None,
            previous_date=prior_day if prior else None,
            source="Nasdaq日线", observed_at=now,
        )
    return quotes


def parse_extended(
    payload: object, symbol: str, name: str, session: str,
    history: dict[date, Quote], now: datetime,
) -> Quote | None:
    if session not in {"premarket", "postmarket"}:
        raise ValueError("Nasdaq 仅提供盘前和盘后成交")
    now = new_york_time(now)
    data = _data(payload, symbol)
    day = None
    updates = data.get("lastUpdateInfo")
    if updates is None:
        updates = []
    if not isinstance(updates, list):
        raise ValueError("Nasdaq 成交日期格式错误")
    for line in updates:
        if not isinstance(line, str):
            continue
        match = re.search(r"^Data last updated ([A-Za-z]{3} \d{1,2}, \d{4})\b", line)
        if match:
            try:
                day = datetime.strptime(match.group(1), "%b %d, %Y").date()
            except ValueError:
                pass
            break
    try:
        if day is None or not is_trading_day(day):
            return None
        start = datetime.combine(day, time(4), NY) if session == "premarket" else session_close(day)
        end = session_open(day) if session == "premarket" else extended_close(day)
        basis_day = previous_trading_day(day) if session == "premarket" else day
    except ValueError:
        return None
    candidates = []
    table = data.get("tradeDetailTable")
    rows = table.get("rows") or [] if isinstance(table, dict) else []
    if not isinstance(rows, list):
        raise ValueError("Nasdaq 场外成交格式错误")
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _number(row.get("price"), positive=True)
        try:
            stamp = datetime.combine(day, datetime.strptime(row.get("time", ""), "%H:%M:%S").time(), NY)
        except (TypeError, ValueError):
            continue
        if price is not None and start <= stamp < end and stamp.timestamp() <= now.timestamp():
            candidates.append((stamp, price))
    if not candidates:
        return None
    stamp, price = max(candidates)
    basis = history.get(basis_day)
    return Quote(
        symbol, name, price,
        pct=finite_number((price / basis.last - 1) * 100) if basis else None,
        previous_close=basis.last if basis else None,
        previous_date=basis_day if basis else None,
        asof=stamp, session=session, source="Nasdaq", observed_at=now,
    )


def load(
    symbol: str, name: str, now: datetime, *, clock: Callable[[], datetime] | None = None,
) -> MarketData:
    result = MarketData()
    asset = ASSET_CLASSES.get(symbol)
    if asset is None:
        return result
    now = new_york_time(now)
    history = {}
    parameters = {"assetclass": asset, "fromdate": (now.date() - timedelta(days=14)).isoformat(),
                  "todate": now.date().isoformat(), "limit": 20}
    url = f"{BASE_URL}/{symbol}/historical?{urlencode(parameters)}"
    try:
        payload = json.loads(fetch_text(url, REFERER, timeout=10, retries=0))
        history = parse_history(payload, symbol, name, clock() if clock else now)
        if history:
            result.completed[symbol] = result.quotes[symbol] = history[max(history)]
    except (OSError, ValueError) as exc:
        result.notes.append(f"Nasdaq {symbol} 日线暂不可用（{type(exc).__name__}）")
    for session, market in (("premarket", "pre"), ("postmarket", "post")):
        parameters = urlencode({"assetclass": asset, "markettype": market})
        url = f"{BASE_URL}/{symbol}/extended-trading?{parameters}"
        try:
            payload = json.loads(fetch_text(url, REFERER, timeout=10, retries=0))
            quote = parse_extended(payload, symbol, name, session, history, clock() if clock else now)
            if quote:
                getattr(result, session)[symbol] = quote
        except (OSError, ValueError) as exc:
            result.notes.append(f"Nasdaq {symbol} {session}暂不可用（{type(exc).__name__}）")
    return result
