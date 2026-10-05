"""免费历史常规场日线；盘前、夜盘和盘后成交需要当时真实留存。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from urllib.parse import quote as url_quote, urlencode

from common.http import fetch_text
from common.publication import InsufficientData

from .calendar import is_trading_day, previous_trading_day, session_close
from .models import INDEX_NAMES, MEGA_NAMES, NY, SECTOR_NAMES, MarketData, Quote, new_york_time
from .sources import cboe, nasdaq, yahoo


def _rows(history: dict[date, Quote], start: date, end: date, required_day: date | None) -> list[Quote]:
    # 实际补录时间由capture记录；不能当成历史参考时点之前的成交观察时间。
    return [replace(quote, observed_at=None) for day, quote in history.items()
            if start <= day <= end] if required_day is None or required_day in history else []


def load_daily(symbol: str, name: str, start: date, end: date, observed_at: datetime,
               *, required_day: date | None = None) -> list[Quote]:
    """优先Yahoo，按标的备用Nasdaq/Cboe；单条行情的基准与股数保持同源。"""
    errors = []
    params = urlencode({"period1": int(datetime.combine(start, time.min, NY).timestamp()),
                        "period2": int(datetime.combine(end + timedelta(days=1), time.min, NY).timestamp()), "interval": "1d"})
    for host in yahoo.CHART_HOSTS:
        try:
            url = f"https://{host}/v8/finance/chart/{url_quote(symbol, safe='')}?{params}"
            payload = json.loads(fetch_text(url, yahoo.YAHOO, timeout=10, retries=0))
            quotes = _rows(yahoo.parse_history(payload, symbol, name, now=observed_at), start, end, required_day)
            if quotes:
                return quotes
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            errors.append(type(exc).__name__)
    if symbol in nasdaq.ASSET_CLASSES or symbol == "^IXIC":
        try:
            code, assetclass = ("COMP", "index") if symbol == "^IXIC" else (symbol, nasdaq.ASSET_CLASSES[symbol])
            params = urlencode({"assetclass": assetclass, "fromdate": start.isoformat(), "todate": end.isoformat(), "limit": 100})
            raw = json.loads(fetch_text(f"{nasdaq.BASE_URL}/{code}/historical?{params}", nasdaq.REFERER, timeout=10, retries=0))
            history = nasdaq.parse_history(raw, code, name, observed_at)
            if symbol == "^IXIC":
                history = {day: replace(quote, symbol=symbol, unit="点", source="Nasdaq指数日线") for day, quote in history.items()}
            quotes = _rows(history, start, end, required_day)
            if quotes:
                return quotes
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            errors.append(type(exc).__name__)
    if symbol in cboe.SYMBOLS:
        try:
            raw = json.loads(fetch_text(f"{cboe.BASE_URL}/charts/historical/_{cboe.SYMBOLS[symbol]}.json", cboe.REFERER,
                                       timeout=10, retries=0))
            quotes = _rows(cboe.parse_history(raw, symbol, name, observed_at), start, end, required_day)
            if quotes:
                return quotes
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            errors.append(type(exc).__name__)
    raise ValueError(f"{name}历史来源不可用（{'、'.join(errors) or '目标日期无数据'}）")


def _collect(names: dict[str, str], day: date, observed_at: datetime) -> tuple[dict[str, Quote], list[str]]:
    completed, notes = {}, []
    try:
        start = previous_trading_day(day)
    except ValueError:
        start = day
        notes.append("前一NYSE交易日未在日历覆盖范围内，涨跌与成交股数比较待确认")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [(symbol, name, pool.submit(load_daily, symbol, name, start, day, observed_at, required_day=day))
                   for symbol, name in names.items()]
        for symbol, name, future in futures:
            try:
                rows = future.result()
                quote = next(row for row in rows if row.trade_date == day)
            except (OSError, ValueError, TypeError, StopIteration) as exc:
                notes.append(f"{name}历史日线暂缺（{type(exc).__name__}）")
                continue
            completed[symbol] = quote
            if quote.pct is None or quote.previous_date != start:
                notes.append(f"{name}同源上个交易日基准暂缺，涨跌比较待确认")
    return completed, notes


def load_history(kind: str, day: date, observed_at: datetime) -> tuple[MarketData, datetime]:
    if kind not in {"premarket", "postmarket"}:
        raise ValueError("kind 只能是 premarket 或 postmarket")
    if not is_trading_day(day):
        raise ValueError(f"{day}不是NYSE交易日")
    if kind == "premarket":
        raise InsufficientData(f"{day}美股盘前未留存当日盘前或夜盘快照；常规场日线不能恢复盘前，请使用有真实归档的日期")
    clock = session_close(day) + timedelta(minutes=30)
    observed_at = new_york_time(observed_at)
    if clock > observed_at:
        raise ValueError(f"{day}的盘后历史参考时点尚未到达")
    completed, notes = _collect(INDEX_NAMES, day, observed_at)
    if not completed:
        raise InsufficientData(f"{day}美股盘后历史核心行情不足，未取得当日主指数收盘日线")
    more, missing = _collect(MEGA_NAMES | SECTOR_NAMES | {"SPY": "标普500ETF"}, day, observed_at)
    completed.update(more)
    notes = ["历史常规场日线补录；当时新闻、宏观参考及盘后延长成交未留存，暂缺", "历史参考时点与实际补录时间分别保存，不采用今天的行情或快讯"] + notes + missing
    return MarketData(completed=completed, notes=notes), clock
