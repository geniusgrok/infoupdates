from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from math import isfinite
from typing import Callable
from urllib.parse import quote as url_quote, urlencode

from common.http import fetch_text

from ..calendar import (
    SUPPORTED_YEARS, edition_date, extended_close, is_trading_day, overnight_window,
    previous_trading_day, session_close, session_open,
)
from ..models import NY, Quote, MarketData, MEGA_NAMES, SECTOR_NAMES, new_york_time


def finite_number(value: object, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(number) or (positive and number <= 0):
        return None
    return number


def _timestamp(value: object, now: datetime) -> datetime | None:
    number = finite_number(value)
    if number is None:
        return None
    try:
        moment = datetime.fromtimestamp(number, NY)
    except (ValueError, OSError, OverflowError):
        return None
    # Equal ZoneInfo objects compare wall clocks during the repeated DST hour.
    return moment if number <= now.timestamp() else None


def chart_result(payload: object, symbol: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    result = results[0]
    meta = result.get("meta")
    if not isinstance(meta, dict) or meta.get("symbol") != symbol:
        return None
    return result


@dataclass(frozen=True)
class _Bar:
    at: datetime
    close: float
    volume: float | None


def _bars(result: dict, now: datetime) -> list[_Bar]:
    stamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(stamps, list) or not isinstance(indicators, dict):
        return []
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        return []
    closes = quotes[0].get("close")
    volumes = quotes[0].get("volume")
    if not isinstance(closes, list):
        return []
    volumes = volumes if isinstance(volumes, list) else []
    parsed: dict[datetime, _Bar] = {}
    for index, stamp in enumerate(stamps):
        at = _timestamp(stamp, now)
        close = finite_number(closes[index] if index < len(closes) else None, positive=True)
        volume = finite_number(volumes[index] if index < len(volumes) else None)
        if at is None or close is None:
            continue
        if volume is not None and volume < 0:
            volume = None
        parsed[at] = _Bar(at, close, volume)
    return sorted(parsed.values(), key=lambda bar: bar.at)


def _unit(symbol: str) -> str:
    if symbol == "^TNX":
        return "%"
    if symbol.startswith("^") or symbol in {"ES=F", "NQ=F", "YM=F", "RTY=F", "DX-Y.NYB"}:
        return "点"
    if symbol == "GC=F":
        return "USD/盎司"
    if symbol == "CL=F":
        return "USD/桶"
    return "USD"


def _prior_bar(bars: list[_Bar], day: date) -> _Bar | None:
    try:
        target = previous_trading_day(day)
    except ValueError:
        return None
    return next((bar for bar in reversed(bars) if bar.at.date() == target), None)


def _known_trading_day(day: date) -> bool:
    return day.year in SUPPORTED_YEARS and is_trading_day(day)


def _bar_completed(bar: _Bar, bars: list[_Bar], result: dict, now: datetime) -> bool:
    end = session_close(bar.at.date())
    if end > now:
        return False
    # A later daily bar demonstrates that this historical session has ended.
    if any(later.at.date() > bar.at.date() for later in bars):
        return True
    source_time = _timestamp(result["meta"].get("regularMarketTime"), now)
    if source_time is None or source_time < end:
        return False
    if source_time.date() == bar.at.date():
        last = finite_number(result["meta"].get("regularMarketPrice"), positive=True)
        # Daily and last-tick prices must describe the same completed session.
        return last is not None and abs(last - bar.close) <= max(0.02, last * 2e-6)
    return True


def _vendor_reference(meta: dict, last: float) -> tuple[float | None, float | None]:
    """Futures and dollar-index changes use Yahoo's stated reference basis."""
    percent = finite_number(meta.get("regularMarketChangePercent"))
    full_price = finite_number(meta.get("fulldayPrice"), positive=True)
    change = finite_number(meta.get("fulldayChange"))
    if full_price is not None and change is not None and abs(full_price - last) < max(1e-6, last * 1e-6):
        reference = last - change
        if reference > 0:
            computed = (last / reference - 1) * 100
            if percent is None or abs(computed - percent) <= 0.02:
                return reference, computed
    if percent is not None and percent > -100:
        return last / (1 + percent / 100), percent
    return None, None


def parse_quote(
    payload: object, symbol: str, name: str, *, now: datetime | None = None,
) -> Quote | None:
    """Latest regular quote; chartPreviousClose is deliberately never used."""
    now = new_york_time(now or datetime.now(NY))
    result = chart_result(payload, symbol)
    if result is None:
        return None
    meta = result["meta"]
    last = finite_number(meta.get("regularMarketPrice"), positive=True)
    asof = _timestamp(meta.get("regularMarketTime"), now)
    if last is None or asof is None:
        return None
    bars = _bars(result, now)
    volume = finite_number(meta.get("regularMarketVolume"))
    if volume is not None and volume < 0:
        volume = None
    if symbol.endswith("=F") or symbol == "DX-Y.NYB":
        reference, pct = _vendor_reference(meta, last)
        return Quote(
            symbol, name, last, pct, reference, asof,
            session="futures" if symbol.endswith("=F") else "reference",
            volume=volume, source="Yahoo Finance（供应商参考基准）", unit=_unit(symbol),
        )
    current = next((bar for bar in reversed(bars) if bar.at.date() == asof.date()), None)
    if current is None or not is_trading_day(asof.date()):
        return None
    previous = _prior_bar(bars, asof.date())
    reference = previous.close if previous else None
    return Quote(
        symbol, name, last, (last / reference - 1) * 100 if reference else None,
        reference, asof, volume=current.volume if current.volume is not None else volume,
        previous_volume=previous.volume if previous else None,
        previous_date=previous.at.date() if previous else None, unit=_unit(symbol),
    )


def parse_completed(
    payload: object, symbol: str, name: str, *, now: datetime | None = None,
) -> Quote | None:
    """Select a complete NYSE daily bar, including the previous day during trading."""
    now = new_york_time(now or datetime.now(NY))
    result = chart_result(payload, symbol)
    if result is None or symbol.endswith("=F") or symbol == "DX-Y.NYB":
        return None
    bars = _bars(result, now)
    current = next(
        (bar for bar in reversed(bars)
         if _known_trading_day(bar.at.date()) and _bar_completed(bar, bars, result, now)),
        None,
    )
    if current is None:
        return None
    previous = _prior_bar(bars, current.at.date())
    reference = previous.close if previous else None
    return Quote(
        symbol, name, current.close, (current.close / reference - 1) * 100 if reference else None,
        reference, session_close(current.at.date()), volume=current.volume,
        previous_volume=previous.volume if previous else None,
        previous_date=previous.at.date() if previous else None, unit=_unit(symbol),
    )


def parse_history(payload: object, symbol: str, name: str, *, now: datetime) -> dict[date, Quote]:
    """补录日线保留真实采集时钟，以后续日线或供应商时点确认已闭市。"""
    now = new_york_time(now)
    result = chart_result(payload, symbol)
    if result is None or symbol.endswith("=F") or symbol == "DX-Y.NYB":
        return {}
    bars = _bars(result, now)
    history = {}
    for bar in bars:
        if not _known_trading_day(bar.at.date()) or not _bar_completed(bar, bars, result, now):
            continue
        previous = _prior_bar(bars, bar.at.date())
        reference = previous.close if previous else None
        history[bar.at.date()] = Quote(
            symbol, name, bar.close, (bar.close / reference - 1) * 100 if reference else None,
            reference, session_close(bar.at.date()), volume=bar.volume,
            previous_volume=previous.volume if previous else None,
            previous_date=previous.at.date() if previous else None, unit=_unit(symbol),
        )
    return history


def _extended_reference(
    result: dict, bars: list[_Bar], selected: _Bar, session: str, now: datetime,
    daily_payload: object | None, symbol: str,
) -> tuple[float | None, date | None]:
    day = selected.at.date()
    try:
        target = previous_trading_day(day) if session == "premarket" else day
    except ValueError:
        return None, None
    daily = chart_result(daily_payload, symbol)
    if daily is not None:
        daily_bars = _bars(daily, now)
        reference = next((bar for bar in reversed(daily_bars) if bar.at.date() == target), None)
        if reference is not None and _bar_completed(reference, daily_bars, daily, now):
            return reference.close, target
        return None, None
    meta = result["meta"]
    regular_time = _timestamp(meta.get("regularMarketTime"), now)
    regular_price = finite_number(meta.get("regularMarketPrice"), positive=True)
    if regular_time is not None and regular_time.date() == target and regular_time >= session_close(target):
        return regular_price, target if regular_price else None
    # A final 5-minute regular bar closes at the exchange's close, including half days.
    end = session_close(target)
    reference = next(
        (bar for bar in reversed(bars) if end - timedelta(minutes=5) <= bar.at < end), None,
    )
    if reference is not None and end <= now:
        return reference.close, target
    return None, None


def parse_extended(
    payload: object, symbol: str, name: str, session: str, *,
    now: datetime | None = None, daily_payload: object | None = None,
) -> Quote | None:
    if session not in {"premarket", "postmarket"}:
        raise ValueError("session 必须是 premarket 或 postmarket")
    now = new_york_time(now or datetime.now(NY))
    result = chart_result(payload, symbol)
    if result is None:
        return None
    bars = _bars(result, now)
    eligible: list[_Bar] = []
    for bar in bars:
        day = bar.at.date()
        if not _known_trading_day(day):
            continue
        start = datetime.combine(day, time(4), tzinfo=NY) if session == "premarket" else session_close(day)
        end = session_open(day) if session == "premarket" else extended_close(day)
        if start <= bar.at < end:
            eligible.append(bar)
    if not eligible:
        return None
    selected = eligible[-1]
    reference, reference_day = _extended_reference(result, bars, selected, session, now, daily_payload, symbol)
    same_day = [bar for bar in eligible if bar.at.date() == selected.at.date()]
    volume = sum(bar.volume for bar in same_day) if all(bar.volume is not None for bar in same_day) else None
    return Quote(
        symbol, name, selected.close, (selected.close / reference - 1) * 100 if reference else None,
        reference, selected.at, session=session, volume=volume,
        previous_date=reference_day, unit=_unit(symbol),
    )


class _YahooPriceScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "application/json" and "data-sveltekit-fetched" in attributes:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def _page_prices(html: str, symbol: str):
    parser = _YahooPriceScripts()
    parser.feed(html)
    parser.close()
    for script in parser.scripts:
        try:
            outer = json.loads(script)
            if not isinstance(outer, dict) or outer.get("status") != 200 or not isinstance(outer.get("body"), str):
                continue
            body = json.loads(outer["body"])
        except (ValueError, TypeError):
            continue
        if not isinstance(body, dict) or not isinstance(body.get("quoteSummary"), dict):
            continue
        results = body["quoteSummary"].get("result")
        if not isinstance(results, list):
            continue
        for result in results:
            price = result.get("price") if isinstance(result, dict) else None
            if isinstance(price, dict) and price.get("symbol") == symbol:
                yield price


def _raw_price(value: object) -> float | None:
    return finite_number(value.get("raw"), positive=True) if isinstance(value, dict) else None


def parse_overnight(
    html: str, symbol: str, name: str, *, now: datetime | None = None,
) -> Quote | None:
    """A real Yahoo/BOATS night quote; the chart endpoint does not carry this session."""
    if not isinstance(html, str):
        return None
    now = new_york_time(now or datetime.now(NY))
    target = edition_date("premarket", now)
    start, end = overnight_window(target)
    basis_day = previous_trading_day(target)
    candidates: list[Quote] = []
    for price in _page_prices(html, symbol):
        last = _raw_price(price.get("overnightMarketPrice"))
        at = _timestamp(price.get("overnightMarketTime"), now)
        source = price.get("overnightMarketSource")
        if last is None or at is None or not start <= at < end or not isinstance(source, str) or not source.strip():
            continue
        if price.get("currency") not in (None, "USD"):
            continue
        reference = _raw_price(price.get("regularMarketPrice"))
        reference_at = _timestamp(price.get("regularMarketTime"), now)
        if reference_at is None or reference_at.date() != basis_day or reference_at < session_close(basis_day):
            reference = None
        pct = finite_number((last / reference - 1) * 100) if reference is not None else None
        candidates.append(Quote(
            symbol, name, last, pct, reference, at, session="overnight",
            previous_date=basis_day if reference is not None else None,
            source=source.strip(), unit="USD",
        ))
    return max(candidates, key=lambda item: item.asof) if candidates else None


YAHOO = "https://finance.yahoo.com/"
CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def _fetch_chart(symbol: str, *, extended: bool = False) -> dict:
    params = {"range": "2d" if extended else "5d", "interval": "5m" if extended else "1d"}
    if extended:
        params["includePrePost"] = "true"
    error: Exception | None = None
    for host in CHART_HOSTS:
        url = f"https://{host}/v8/finance/chart/{url_quote(symbol, safe='')}?{urlencode(params)}"
        try:
            payload = json.loads(fetch_text(url, YAHOO, timeout=10, retries=0))
            if chart_result(payload, symbol) is None:
                raise ValueError("行情为空、标的不匹配或接口返回错误")
            return payload
        except (OSError, ValueError, RuntimeError) as exc:
            error = exc
    raise RuntimeError(f"Yahoo 两个行情入口均不可用：{error}") from error


def _daily(symbol: str, name: str, now: datetime, *, clock=None) -> tuple[dict, Quote | None, Quote | None]:
    payload = _fetch_chart(symbol)
    now = new_york_time(clock() if clock is not None else now)
    return (
        payload,
        parse_quote(payload, symbol, name, now=now),
        parse_completed(payload, symbol, name, now=now),
    )


def _extended(symbol: str, name: str, now: datetime, daily_payload: dict | None, *, clock=None) -> tuple[Quote | None, Quote | None]:
    payload = _fetch_chart(symbol, extended=True)
    now = new_york_time(clock() if clock is not None else now)
    return (
        parse_extended(payload, symbol, name, "premarket", now=now, daily_payload=daily_payload),
        parse_extended(payload, symbol, name, "postmarket", now=now, daily_payload=daily_payload),
    )


def _overnight(symbol: str, name: str, now: datetime, *, clock=None) -> Quote | None:
    html = fetch_text(f"{YAHOO}quote/{url_quote(symbol, safe='')}/", YAHOO, timeout=12, retries=0)
    now = new_york_time(clock() if clock is not None else now)
    return parse_overnight(html, symbol, name, now=now)



def load(symbol: str, name: str, now: datetime, *, clock: Callable[[], datetime] | None = None) -> MarketData:
    """单标的Yahoo适配器，各时段保留同源基准和真实成交时间。"""
    now = new_york_time(now)
    result = MarketData()
    payload = None
    options = {"clock": clock} if clock is not None else {}
    try:
        payload, latest, completed = _daily(symbol, name, now, **options)
        if latest is not None:
            result.quotes[symbol] = latest
        if completed is not None:
            result.completed[symbol] = completed
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        result.notes.append(f"Yahoo {symbol} 常规行情暂缺：{exc}")
    if symbol in MEGA_NAMES or symbol in SECTOR_NAMES:
        try:
            premarket, postmarket = _extended(symbol, name, now, payload, **options)
            if premarket is not None:
                result.premarket[symbol] = premarket
            if postmarket is not None:
                result.postmarket[symbol] = postmarket
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            result.notes.append(f"Yahoo {symbol} 延长时段行情暂缺：{exc}")
        try:
            overnight = _overnight(symbol, name, now, **options)
            if overnight is not None:
                result.overnight[symbol] = overnight
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            result.notes.append(f"Yahoo {symbol} 夜盘行情暂缺：{exc}")
    return result
