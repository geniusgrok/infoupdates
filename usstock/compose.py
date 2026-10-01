from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from math import isfinite

from .calendar import extended_close, edition_date, is_trading_day, last_completed_session, session_close, session_open
from .models import (
    NY, Brief, Quote, MarketData, INDEX_NAMES, FUTURE_NAMES, MEGA_NAMES,
    SECTOR_NAMES, MACRO_NAMES, new_york_time,
)
from .narrative import build_narrative, select_news


def _valid_quote(quote: Quote, now: datetime) -> Quote | None:
    if not isfinite(quote.last) or quote.last <= 0 or quote.asof is None:
        return None
    asof = new_york_time(quote.asof)
    if asof > now:
        return None
    pct = quote.pct if quote.pct is not None and isfinite(quote.pct) else None
    return replace(quote, asof=asof, pct=pct)


def _completed_quote(quote: Quote, now: datetime, through: date) -> Quote | None:
    quote = _valid_quote(quote, now)
    if quote is None or quote.session != "regular" or quote.trade_date > through:
        return None
    try:
        if not is_trading_day(quote.trade_date) or quote.asof < session_close(quote.trade_date):
            return None
    except ValueError:
        return None
    return quote


def _premarket_quote(quote: Quote, now: datetime, target: date) -> Quote | None:
    quote = _valid_quote(quote, now)
    if quote is None or quote.session != "premarket" or quote.trade_date != target:
        return None
    if now.date() != target or not is_trading_day(target) or now >= session_open(target):
        return None
    if not time(4) <= quote.asof.time().replace(tzinfo=None) < time(9, 30):
        return None
    return quote


def _postmarket_quote(quote: Quote, now: datetime, reference: date | None) -> Quote | None:
    quote = _valid_quote(quote, now)
    if quote is None or quote.session != "postmarket" or reference is None or quote.trade_date != reference:
        return None
    if not session_close(reference) <= quote.asof < extended_close(reference):
        return None
    if quote.previous_date != reference or quote.previous_close is None or not isfinite(quote.previous_close) or quote.previous_close <= 0:
        quote = replace(quote, pct=None)
    return quote


def build_brief(kind: str, data: MarketData, now: datetime | None = None) -> Brief:
    now = new_york_time(now or datetime.now(NY))
    target = edition_date(kind, now)
    latest_completed = last_completed_session(now)
    notes = list(data.notes)
    completed: dict[str, Quote] = {}
    # 历史日线保留上一完成场；最新regular可作为缺少日线时的严格降级。
    for source in (data.quotes, data.completed):
        for symbol, raw in source.items():
            quote = _completed_quote(raw, now, latest_completed)
            if quote is not None and quote.symbol == symbol:
                previous = completed.get(symbol)
                if previous is None or quote.trade_date >= previous.trade_date:
                    completed[symbol] = quote
    index_dates = [quote.trade_date for symbol, quote in completed.items() if symbol in INDEX_NAMES]
    reference = max(index_dates, default=None)
    if reference is None:
        reference = max((quote.trade_date for quote in completed.values()), default=None)
    aligned = {symbol: quote for symbol, quote in completed.items() if quote.trade_date == reference}
    indices = [aligned[symbol] for symbol in INDEX_NAMES if symbol in aligned]
    sectors = [aligned[symbol] for symbol in SECTOR_NAMES if symbol in aligned]
    regular_stocks = [aligned[symbol] for symbol in MEGA_NAMES if symbol in aligned]
    complete = len(indices) == len(INDEX_NAMES) and all(quote.pct is not None for quote in indices)
    if reference is None:
        notes.append("已完成常规场行情暂缺；盘中或盘后延长交易不替代收盘数据")
    elif reference != latest_completed:
        notes.append(f"最新完成场为{latest_completed.isoformat()}；行情仅到{reference.isoformat()}收盘")
        complete = False
    if len(indices) != len(INDEX_NAMES):
        missing = "、".join(name for symbol, name in INDEX_NAMES.items() if symbol not in aligned)
        notes.append(f"同日主指数暂缺：{missing}")
    regular_symbols = set(INDEX_NAMES) | set(MEGA_NAMES) | set(SECTOR_NAMES) | {"SPY"}
    if reference is not None and any(
        quote.trade_date != reference for symbol, quote in completed.items() if symbol in regular_symbols
    ):
        notes.append("常规场行情日期不一致，已跳过其他日期的数据")
    stocks = regular_stocks
    extended_stocks: list[Quote] = []
    stocks_label = "常规场收盘"
    if kind == "premarket":
        stocks = []
        premarket_count = 0
        for symbol in MEGA_NAMES:
            raw = data.premarket.get(symbol)
            premarket = _premarket_quote(raw, now, target) if raw is not None else None
            if premarket is not None:
                stocks.append(premarket)
                premarket_count += 1
            elif symbol in aligned:
                stocks.append(aligned[symbol])
        if premarket_count == len(stocks) and premarket_count:
            stocks_label = "盘前行情"
        elif premarket_count:
            stocks_label = "盘前 / 前收行情"
            notes.append("部分个股盘前行情暂缺，已逐项标明前收")
        else:
            stocks_label = "前收行情"
            notes.append("本版盘前个股行情暂缺，所列股票均为前收")
        if target != now.date():
            notes.append("尚未进入本版交易日盘前，期货为当前参考，非下一交易日盘前实盘")
    if kind == "postmarket":
        for symbol in MEGA_NAMES:
            raw = data.postmarket.get(symbol)
            extended = _postmarket_quote(raw, now, reference) if raw is not None else None
            if extended is not None:
                extended_stocks.append(extended)
        if extended_stocks:
            stocks_label = "常规收盘 / 盘后"
        else:
            notes.append("同参考日的盘后延长交易行情暂缺，个股仅展示常规场收盘")
    futures: list[Quote] = []
    references: list[Quote] = []
    for symbol in FUTURE_NAMES:
        raw = data.quotes.get(symbol)
        quote = _valid_quote(raw, now) if raw is not None else None
        if quote is not None and quote.session == "futures":
            futures.append(quote)
    for symbol in MACRO_NAMES:
        # 宏观参考各有市场时段，保留实际时点，不套用NYSE股票16:00规则。
        raw = data.quotes.get(symbol) or aligned.get(symbol)
        quote = _valid_quote(raw, now) if raw is not None else None
        if quote is not None:
            references.append(replace(quote, session="reference") if quote.session == "regular" else quote)
    brief = Brief(
        kind=kind, generated_at=now, edition_date=target, reference_date=reference,
        indices=indices, futures=futures, stocks=stocks, sectors=sectors,
        references=references, activity=aligned.get("SPY"),
        notes=list(dict.fromkeys(notes)), stocks_label=stocks_label, complete=complete, extended_stocks=extended_stocks,
    )
    brief.news = select_news(data.news, kind=kind, reference_date=reference, now=now, limit=6)
    brief.headline, brief.sentiment, brief.market_summary = build_narrative(brief)
    return brief


def load_brief(kind: str, now: datetime | None = None) -> Brief:
    from .fetch import load_market

    now = new_york_time(now or datetime.now(NY))
    return build_brief(kind, load_market(now=now), now=now)
