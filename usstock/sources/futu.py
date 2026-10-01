"""富途公开页面：常规行情、分钟图和盘前/盘后报价，不提供夜盘。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.error import URLError

from common.http import fetch_text

from ..calendar import (
    SUPPORTED_YEARS, extended_close, is_trading_day, previous_trading_day,
    session_close, session_open,
)
from ..models import MEGA_NAMES, NY, SECTOR_NAMES, MarketData, Quote, finite_number, new_york_time

SYMBOLS = {
    **{symbol: (f"{symbol}-US", symbol) for symbol in (*MEGA_NAMES, *SECTOR_NAMES, "SPY")},
    "^DJI": (".DJI-US", ".DJI"), "^IXIC": (".IXIC-US", ".IXIC"),
    "^GSPC": (".SPX-US", ".SPX"), "^RUT": (".RUT-US", ".RUT"),
    "^VIX": (".VIX-US", ".VIX"), "^TNX": (".TNX-US", ".TNX"),
    "DX-Y.NYB": ("USDINDEX-FX", "USDindex"),
    **{f"{symbol}=F": (f"{symbol}MAIN-US", f"{symbol}MAIN")
       for symbol in ("ES", "NQ", "YM", "RTY", "GC", "CL")},
}


@dataclass(frozen=True)
class _Bar:
    at: datetime
    price: float


def _time(value: object, now: datetime, *, milliseconds: bool = False) -> datetime | None:
    stamp = finite_number(value, positive=True)
    if stamp is None:
        return None
    stamp /= 1000 if milliseconds else 1
    if not now.timestamp() - 7 * 86400 <= stamp <= now.timestamp():
        return None
    try:
        return datetime.fromtimestamp(stamp, NY)
    except (ValueError, OverflowError, OSError):
        return None


def _trading_day(day: date) -> bool:
    return day.year in SUPPORTED_YEARS and is_trading_day(day)


def _bars(chart: dict, now: datetime) -> list[_Bar]:
    rows = chart.get("list")
    if not isinstance(rows, list):
        return []
    parsed = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        at = _time(row.get("time"), now)
        price = finite_number(row.get("cc_price"), positive=True)
        if price is None and (raw := finite_number(row.get("price"), positive=True)) is not None:
            price = raw / 1000
        if at is None or price is None or not _trading_day(at.date()):
            continue
        parsed[at] = _Bar(at, price)
    return sorted(parsed.values(), key=lambda bar: bar.at)


def _unit(symbol: str) -> str:
    if symbol == "^TNX":
        return "%"
    if symbol == "GC=F":
        return "USD/盎司"
    if symbol == "CL=F":
        return "USD/桶"
    return "点" if symbol.startswith("^") or symbol.endswith("=F") or symbol == "DX-Y.NYB" else "USD"


def parse(html: str, symbol: str, name: str, now: datetime) -> MarketData:
    """只使用匹配标的的真实来源时间；分钟时间是区间结束时刻。"""
    now = new_york_time(now)
    result = MarketData()
    expected = SYMBOLS.get(symbol)
    marker = re.search(r"<script\b[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*", html)
    if expected is None or marker is None:
        result.notes.append(f"富途 {symbol} 公开行情暂缺")
        return result
    try:
        state = json.JSONDecoder().raw_decode(html[marker.end():])[0]
    except (ValueError, TypeError):
        result.notes.append(f"富途 {symbol} 页面行情格式无效")
        return result
    info = state.get("stock_info") if isinstance(state, dict) else None
    market = "FX" if symbol == "DX-Y.NYB" else "US"
    if not isinstance(info, dict) or info.get("stockCode") != expected[1] or info.get("marketLabel") != market:
        result.notes.append(f"富途 {symbol} 标的身份不匹配")
        return result

    delay = finite_number(info.get("delaySeconds", info.get("delayTime")))
    delay_minutes = max(0, int(delay / 60)) if delay is not None else None
    scale = 10 if symbol == "^TNX" else 1

    def quote(last: float, at: datetime, reference: float | None, session: str) -> Quote:
        previous_date = None
        if reference is not None and session not in {"futures", "reference"}:
            previous_date = at.date() if session == "postmarket" else previous_trading_day(at.date())
        return Quote(
            symbol, name, last / scale,
            finite_number((last / reference - 1) * 100) if reference is not None else None,
            reference / scale if reference is not None else None, at,
            session=session, previous_date=previous_date,
            source="Futu", unit=_unit(symbol), observed_at=now, delay_minutes=delay_minutes,
        )

    last = finite_number(info.get("price"), positive=True)
    at = _time(info.get("exchangeDataTimeMs"), now, milliseconds=True)
    previous = finite_number(info.get("priceLastClose"), positive=True)
    futures = symbol.endswith("=F")
    reference_market = symbol in {"^VIX", "^TNX", "DX-Y.NYB"}
    if futures:
        future = info.get("future")
        settlement = finite_number(future.get("lastSettlementPrice"), positive=True) if isinstance(future, dict) else None
        previous = settlement if settlement is not None else previous
    if futures or reference_market:
        if last is not None and at is not None:
            result.quotes[symbol] = quote(last, at, previous, "futures" if futures else "reference")
        if delay_minutes:
            result.notes.append(f"富途 {symbol} 行情延迟 {delay_minutes} 分钟")
        if not result.quotes:
            result.notes.append(f"富途 {symbol} 价格或真实时间暂缺")
        return result

    charts = state.get("stock_charts_data")
    chart = charts.get("minuteChartsData") if isinstance(charts, dict) else None
    chart = chart if isinstance(chart, dict) else {}
    if chart and (not info.get("stockId") or chart.get("stockId") != info.get("stockId")):
        chart = {}
    bars = _bars(chart, now)
    chart_previous = finite_number(chart.get("last_close_price"), positive=True)
    chart_previous = chart_previous / 1000 if chart_previous is not None else None
    chart_days = {bar.at.date() for bar in bars}
    # 页面总股数已取整，分钟图也未覆盖全日总量；不拼成精确成交量。

    if last is not None and at is not None and _trading_day(at.date()):
        if session_open(at.date()) <= at <= session_close(at.date()) + timedelta(minutes=1):
            regular = quote(last, at, previous, "regular")
            result.quotes[symbol] = regular
            if at >= session_close(at.date()):
                result.completed[symbol] = regular
    for bar in bars:
        day = bar.at.date()
        if not session_open(day) < bar.at <= session_close(day):
            continue
        basis = chart_previous if chart_days == {day} else previous if at is not None and at.date() == day else None
        regular = quote(bar.price, bar.at, basis, "regular")
        current = result.quotes.get(symbol)
        if current is None or bar.at > current.asof:
            result.quotes[symbol] = regular
        if bar.at == session_close(day):
            result.completed[symbol] = regular

    def extended(last: float, stamp: datetime, phase: str) -> Quote:
        day = stamp.date()
        reference = None
        target = previous_trading_day(day) if phase == "premarket" else day
        completed = result.completed.get(symbol)
        regular = result.quotes.get(symbol)
        if completed is not None and completed.trade_date == target:
            reference = completed.last
        elif phase == "premarket" and regular is not None and regular.trade_date == day:
            reference = regular.previous_close
        elif phase == "premarket" and chart_days == {day}:
            reference = chart_previous
        return quote(last, stamp, reference, phase)

    for bar in bars:
        day = bar.at.date()
        pre_start = datetime.combine(day, time(4), tzinfo=NY)
        phase = ("premarket" if pre_start < bar.at <= session_open(day)
                 else "postmarket" if session_close(day) < bar.at <= extended_close(day) else None)
        if phase is not None:
            getattr(result, phase)[symbol] = extended(bar.price, bar.at, phase)

    snapshot = info.get("before_open_stock_info")
    if isinstance(snapshot, dict):
        stamp = _time(snapshot.get("exchange_time"), now, milliseconds=True)
        price = finite_number(snapshot.get("price"), positive=True)
        phase = {1: "premarket", 2: "postmarket"}.get(snapshot.get("status"))
        if stamp is not None and price is not None and phase is not None and _trading_day(stamp.date()):
            start = datetime.combine(stamp.date(), time(4), tzinfo=NY) if phase == "premarket" else session_close(stamp.date())
            end = session_open(stamp.date()) if phase == "premarket" else extended_close(stamp.date())
            current = getattr(result, phase).get(symbol)
            if start <= stamp < end and (current is None or stamp > current.asof):
                getattr(result, phase)[symbol] = extended(price, stamp, phase)
    if not any((result.quotes, result.premarket, result.postmarket)):
        result.notes.append(f"富途 {symbol} 价格或真实时间暂缺")
    if any(record.pct is None for records in (result.premarket, result.postmarket) for record in records.values()):
        result.notes.append(f"富途 {symbol} 延长时段收盘基准暂缺")
    return result


def load(
    symbol: str, name: str, now: datetime, *, clock: Callable[[], datetime] | None = None,
) -> MarketData:
    if symbol not in SYMBOLS:
        return MarketData(notes=[f"富途不支持 {symbol}"])
    url = f"https://www.futunn.com/stock/{SYMBOLS[symbol][0]}"
    try:
        html = fetch_text(url, referer="https://www.futunn.com/", timeout=10, retries=0)
        return parse(html, symbol, name, clock() if clock is not None else now)
    except (URLError, OSError, ValueError) as exc:
        return MarketData(notes=[f"富途 {symbol} 暂不可用：{type(exc).__name__}"])
