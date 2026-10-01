from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from typing import Callable

from common.news import em_items, wscn_items
from .cache import apply_cache
from .calendar import (
    edition_date, extended_close, is_trading_day, last_completed_session,
    overnight_window, previous_trading_day, session_close, session_open,
)
from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, NY, QUOTE_BAGS,
    SECTOR_NAMES, SNAPSHOT_MAX_AGE_SECONDS, MarketData, Quote, clean_quote, new_york_time, quote_clock,
)
from .sources import cboe, futu, nasdaq, webull, yahoo

Clock = Callable[[], datetime]
SYMBOLS = {**INDEX_NAMES, **FUTURE_NAMES, **MEGA_NAMES, **SECTOR_NAMES, **MACRO_NAMES, "SPY": "SPY日线股数"}


def _clock() -> datetime:
    return datetime.now(NY)


def _end_inclusive(quote: Quote) -> bool:
    # 富途分钟时间代表该分钟结束；它的20:00/半日17:00仍是盘后结束bar。
    return quote.source == "Futu"


def _quality(quote: Quote, bag: str, symbol: str, now: datetime) -> int:
    target = edition_date("premarket", now)
    basis = previous_trading_day(target)
    completed = last_completed_session(now)
    stamp = quote_clock(quote)
    if stamp is None:
        return 0
    day = stamp.date()
    reference_date = None
    if bag == "completed":
        if quote.session != "regular" or quote.trade_date != completed or quote.asof < session_close(completed):
            return 0
        reference_date = previous_trading_day(completed)
    elif bag == "premarket":
        if (quote.session != "premarket" or day != target or not is_trading_day(day)
                or not time(4) <= stamp.time().replace(tzinfo=None) < time(9, 30)):
            return 0
        reference_date = basis
    elif bag == "postmarket":
        if quote.session != "postmarket" or day != completed:
            return 0
        end = extended_close(day)
        if not session_close(day) <= stamp or not (stamp <= end if _end_inclusive(quote) else stamp < end):
            return 0
        reference_date = day
    elif bag == "overnight":
        start, end = overnight_window(target)
        if quote.session != "overnight" or not start <= stamp < end:
            return 0
        if quote.is_snapshot and (not start <= now < end or now.timestamp() - stamp.timestamp() > SNAPSHOT_MAX_AGE_SECONDS):
            return 0
        reference_date = basis
    elif bag == "quotes":
        if quote.session in {"futures", "reference"}:
            if quote.session == "futures" and now.timestamp() - stamp.timestamp() > 6 * 3600:
                return 0
            if day < completed:
                return 0
        elif quote.session == "regular":
            latest = now.date() if is_trading_day(now.date()) and now >= session_open(now.date()) else completed
            if day != latest:
                return 0
            reference_date = previous_trading_day(day)
        else:
            return 0
    score = 2 if quote.pct is not None and quote.previous_close is not None else 1
    if reference_date is not None and quote.previous_date != reference_date:
        score = 1
    if symbol == "SPY" and bag == "completed":
        if (quote.volume is None or quote.volume < 0 or quote.previous_volume is None
                or quote.previous_volume <= 0 or quote.previous_date != previous_trading_day(completed)):
            score = 1
    return score


def _required(symbol: str, now: datetime) -> tuple[str, ...]:
    if symbol in FUTURE_NAMES or symbol in MACRO_NAMES:
        return ("quotes",)
    bags = ["quotes", "completed"]
    if symbol in MEGA_NAMES or symbol in SECTOR_NAMES:
        bags.append("postmarket")
        target = edition_date("premarket", now)
        if now.date() == target and datetime.combine(target, time(4), tzinfo=NY) <= now < session_open(target):
            bags.append("premarket")
    return tuple(bags)


def _merge(target: MarketData, partial: MarketData, symbol: str, now: datetime) -> None:
    for bag in QUOTE_BAGS:
        raw = getattr(partial, bag).get(symbol)
        if raw is None:
            continue
        quote = clean_quote(raw, symbol, now, allow_snapshot=bag == "overnight")
        if quote is None:
            continue
        score = _quality(quote, bag, symbol, now)
        if score <= 0:
            continue
        current = getattr(target, bag).get(symbol)
        # 每次替换整条同源记录，不拼接其他供应商的价格、时间或前日股数。
        if current is None or score > _quality(current, bag, symbol, now):
            getattr(target, bag)[symbol] = quote
    target.notes.extend(partial.notes)


def _load_symbol(symbol: str, name: str, now: datetime, clock: Clock | None) -> MarketData:
    result = MarketData()
    if symbol in MEGA_NAMES or symbol in SECTOR_NAMES or symbol == "SPY":
        providers = (yahoo, nasdaq, futu)
    elif symbol in FUTURE_NAMES:
        providers = (yahoo, futu)
    else:
        providers = (yahoo, cboe, futu)
    for provider in providers:
        current = new_york_time(clock() if clock is not None else now)
        if provider is not yahoo and all(
            (quote := getattr(result, bag).get(symbol)) is not None and _quality(quote, bag, symbol, current) >= 2
            for bag in _required(symbol, current)
        ):
            break
        try:
            options = {"clock": clock} if clock is not None else {}
            partial = provider.load(symbol, name, current, **options)
            finished = new_york_time(clock() if clock is not None else now)
            _merge(result, partial, symbol, finished)
        except Exception as exc:
            result.notes.append(f"{provider.__name__.rsplit('.', 1)[-1]} {symbol}暂缺：{type(exc).__name__}")
    return result


def load_market(*, now: datetime | None = None) -> MarketData:
    """live使用响应完成时钟；显式now冻结验证时钟，便于严格回归测试。"""
    clock = _clock if now is None else None
    now = new_york_time(now if now is not None else _clock())
    edition_date("premarket", now)
    last_completed_session(now)
    result = MarketData()
    with ThreadPoolExecutor(max_workers=8) as pool:
        symbols = {symbol: pool.submit(_load_symbol, symbol, name, now, clock) for symbol, name in SYMBOLS.items()}
        news = (
            ("见闻美股快讯", pool.submit(wscn_items, "us-stock-channel", 2)),
            ("见闻全球快讯", pool.submit(wscn_items, "global-channel", 2)),
            ("东财国际快讯", pool.submit(em_items, "111")),
        )
        for symbol, future in symbols.items():
            try:
                partial = future.result()
                finished = new_york_time(clock() if clock is not None else now)
                _merge(result, partial, symbol, finished)
            except Exception as exc:
                result.notes.append(f"{symbol}行情暂缺：{type(exc).__name__}")
        for label, future in news:
            try:
                result.news.extend(future.result())
            except Exception as exc:
                result.notes.append(f"{label}暂缺：{type(exc).__name__}")
    finished = new_york_time(clock() if clock is not None else now)
    target = edition_date("premarket", finished)
    start, end = overnight_window(target)
    missing_night = [symbol for symbol in (*MEGA_NAMES, *SECTOR_NAMES)
                     if symbol not in result.overnight or _quality(result.overnight[symbol], "overnight", symbol, finished) < 2]
    if missing_night and start <= finished < end:
        try:
            snapshots = webull.night_snapshots(finished)
            finished = new_york_time(clock() if clock is not None else now)
            for symbol in missing_night:
                raw = snapshots.get(symbol)
                if raw is not None:
                    # 有真实源时钟的夜盘优先；观测快照只补缺失记录。
                    if symbol not in result.overnight:
                        _merge(result, MarketData(overnight={symbol: raw}), symbol, finished)
        except Exception as exc:
            result.notes.append(f"Webull夜盘快照暂缺：{type(exc).__name__}")
    if not result.news:
        result.notes.append("美股快讯暂缺")
    result.notes = list(dict.fromkeys(result.notes))
    return apply_cache(result, now=finished)
