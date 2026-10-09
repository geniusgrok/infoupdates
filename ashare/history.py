"""免费历史日线补录；没有当时快照的新闻、资金和广度不补造。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time
from urllib.parse import urlencode

from common.http import fetch_text
from common.publication import InsufficientData

from .calendar import is_trading_day, previous_trading_day
from .models import CST, MarketData, Quote, TurnoverComparison, china_time
from usstock.models import finite_number

INDEX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000688": "科创50"}


def _with_changes(quotes: list[Quote]) -> list[Quote]:
    by_day = {quote.trade_day: quote for quote in quotes}
    result = []
    for quote in quotes:
        try:
            previous = by_day.get(previous_trading_day(date.fromisoformat(quote.trade_day)).isoformat())
        except ValueError:
            previous = None
        result.append(replace(quote, pct=finite_number((quote.last / previous.last - 1) * 100) if previous else None,
                              change=quote.last - previous.last if previous else None,
                              prev_close=previous.last if previous else None))
    return result


def parse_daily(payload, symbol: str, name: str, observed_at: datetime) -> list[Quote]:
    if (not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict)
            or payload[0].get("code") != "zs_" + symbol[2:]):
        raise ValueError("A股历史日线标的不匹配")
    rows = payload[0].get("hq")
    if payload[0].get("status") != 0 or not isinstance(rows, list):
        raise ValueError("A股历史日线不可用")
    quotes = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 9:
            continue
        try:
            day = date.fromisoformat(row[0])
            if not is_trading_day(day) or datetime.combine(day, time(15), CST) > observed_at:
                continue
            close, amount = finite_number(row[2], positive=True), finite_number(row[8])
            if close is not None:
                quotes.append(Quote(symbol, name, close, trade_day=day.isoformat(), session="15:00:00", source="搜狐日线",
                                    amount=finite_number(amount * 1e4) if amount is not None and amount >= 0 else None))
        except (ValueError, TypeError, IndexError):
            continue
    return _with_changes(quotes)


def _covers(quotes: list[Quote], required_day: date | None) -> bool:
    return bool(quotes) and (required_day is None or any(quote.trade_day == required_day.isoformat() for quote in quotes))


def _kept(quotes: list[Quote], start: date, end: date, observed_at: datetime) -> list[Quote]:
    kept = []
    for quote in quotes:
        try:
            day = date.fromisoformat(quote.trade_day)
        except ValueError:
            continue
        if start <= day <= end and is_trading_day(day) and datetime.combine(day, time(15), CST) <= observed_at:
            kept.append(quote)
    return kept


def load_daily(symbol: str, name: str, start: date, end: date, observed_at: datetime,
               *, required_day: date | None = None) -> list[Quote]:
    """每个标的一次选择来源；价格与前收盘、成交额不跨源拼接。"""
    errors = []
    try:
        url = "https://q.stock.sohu.com/hisHq?" + urlencode({"code": "zs_" + symbol[2:], "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"), "stat": 1, "order": "D", "period": "d"})
        quotes = _kept(parse_daily(json.loads(fetch_text(url, "https://q.stock.sohu.com/", encoding="gb18030",
                                timeout=12, retries=2)), symbol, name, observed_at), start, end, observed_at)
        if _covers(quotes, required_day):
            return quotes
    except (OSError, ValueError, TypeError) as exc:
        errors.append(type(exc).__name__)
    try:
        from .sources import push_json

        market = 1 if symbol.startswith("sh") else 0
        path = "/api/qt/stock/kline/get?" + urlencode({
            "secid": f"{market}.{symbol[2:]}", "klt": 101, "fqt": 0, "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
        payload = push_json(path, "https://quote.eastmoney.com/")
        data = payload.get("data") or {}
        if data.get("code") != symbol[2:] or data.get("market") != market:
            raise ValueError("东财历史标的不匹配")
        quotes = []
        for line in data.get("klines") or []:
            try:
                row = line.split(",")
                day, price, amount = date.fromisoformat(row[0]), finite_number(row[2], positive=True), finite_number(row[6])
                if price is None:
                    continue
                quotes.append(Quote(symbol, name, price, trade_day=day.isoformat(), session="15:00:00", source="东财日线",
                                    amount=amount if amount is not None and amount >= 0 else None))
            except (ValueError, TypeError, IndexError, AttributeError):
                continue
        quotes = _kept(quotes, start, end, observed_at)
        if _covers(quotes, required_day):
            return _with_changes(quotes)
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
        errors.append(type(exc).__name__)
    try:
        from .sources import tencent_kline

        span = max((end - start).days + 15, 40)
        quotes = _kept(tencent_kline(symbol, name, span), start, end, observed_at)
        if _covers(quotes, required_day):
            return _with_changes(quotes)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        errors.append(type(exc).__name__)
    raise ValueError(f"{name}历史来源不可用（{'、'.join(errors) or '目标日期无数据'}）")


def _indices(day: date, observed_at: datetime) -> tuple[list[Quote], TurnoverComparison | None, list[str]]:
    quotes, notes, comparison = [], [], None
    try:
        previous = previous_trading_day(day)
    except ValueError:
        previous = None
        notes.append("前一A股交易日未在日历覆盖范围内，涨跌与成交额比较待确认")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(symbol, name, pool.submit(load_daily, symbol, name, previous or day, day, observed_at, required_day=day))
                   for symbol, name in INDEX_NAMES.items()]
        for symbol, name, future in futures:
            try:
                rows = future.result()
                quote = next(row for row in rows if row.trade_day == day.isoformat())
            except (OSError, ValueError, TypeError, StopIteration) as exc:
                notes.append(f"{name}历史日线暂缺（{type(exc).__name__}）")
                continue
            quotes.append(quote)
            if quote.pct is None:
                notes.append(f"{name}同源上个交易日收盘暂缺，涨跌幅待确认")
            prior = next((row for row in rows if row.trade_day == previous.isoformat()), None) if previous else None
            if symbol == "sh000001" and prior and quote.amount and prior.amount:
                comparison = TurnoverComparison(day, previous, quote.amount, prior.amount, quote.source)
    return quotes, comparison, notes


def load_history(kind: str, day: date, observed_at: datetime) -> tuple[MarketData, datetime]:
    if kind not in {"close", "morning"}:
        raise ValueError("kind 只能是 close 或 morning")
    observed_at = china_time(observed_at)
    if not is_trading_day(day):
        raise ValueError(f"{day}不是A股交易日")
    clock = datetime.combine(day, time(8) if kind == "morning" else time(15, 30), CST)
    if clock > observed_at:
        raise ValueError(f"{day}的{kind}历史参考时点尚未到达")
    notes = ["历史日线补录；当时新闻、行业涨跌、资金与涨跌家数未留存，暂缺", "历史参考时点与实际补录时间分别保存，不采用今天的行情或快讯"]
    overseas = []
    if kind == "morning":
        from usstock.calendar import last_completed_session, previous_trading_day as us_previous
        from usstock.history import load_daily as load_us_daily
        from usstock.models import INDEX_NAMES as US_INDICES

        us_day = last_completed_session(clock)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [(symbol, name, pool.submit(load_us_daily, symbol, name, us_previous(us_day), us_day, observed_at,
                                                 required_day=us_day))
                       for symbol, name in US_INDICES.items() if symbol != "^RUT"]
            for symbol, name, future in futures:
                try:
                    row = next(quote for quote in future.result() if quote.trade_date == us_day and quote.asof < clock)
                except (OSError, ValueError, TypeError, StopIteration) as exc:
                    notes.append(f"{name}隔夜历史收盘暂缺（{type(exc).__name__}）")
                    continue
                overseas.append(Quote(symbol, name, row.last, row.pct, prev_close=row.previous_close, trade_day=us_day.isoformat(),
                                      session=f"{us_day:%m-%d} 收盘", source=row.source))
        if not overseas:
            raise InsufficientData(f"{day} A股早盘历史核心行情不足，未取得当日08:00前已完成的美股收盘")
        notes.append("外盘仅展示本版08:00前已完成的美股收盘；当时汇率与亚洲行情未留存")
    domestic_day = day
    if kind == "morning":
        try:
            domestic_day = previous_trading_day(day)
        except ValueError:
            notes.append("前一A股交易日未在日历覆盖范围内，国内指数与成交额暂缺")
            return MarketData(indices=[], overseas=overseas, notes=notes), clock
    indices, comparison, missing = _indices(domestic_day, observed_at)
    if kind == "close" and not indices:
        raise InsufficientData(f"{day} A股收盘历史核心行情不足，未取得当日指数日线")
    return MarketData(indices=indices, overseas=overseas, turnover_comparison=comparison, notes=notes + missing), clock
