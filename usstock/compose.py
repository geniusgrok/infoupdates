from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from math import isfinite

from .calendar import (
    extended_close, edition_date, is_trading_day, last_completed_session,
    overnight_window, previous_trading_day, session_close, session_open,
)
from .models import (
    NY, Brief, Quote, MarketData, INDEX_NAMES, FUTURE_NAMES, MEGA_NAMES,
    SECTOR_NAMES, MACRO_NAMES, SNAPSHOT_MAX_AGE_SECONDS, clean_quote, new_york_time, quote_clock,
)
from .narrative import build_narrative, fresh_futures, select_news


def _valid_quote(quote: Quote, now: datetime, *, allow_snapshot: bool = False) -> Quote | None:
    return clean_quote(quote, quote.symbol, now, allow_snapshot=allow_snapshot)


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
    end = extended_close(reference)
    if not session_close(reference) <= quote.asof or not (quote.asof <= end if quote.source == "Futu" else quote.asof < end):
        return None
    if quote.previous_date != reference or quote.previous_close is None or not isfinite(quote.previous_close) or quote.previous_close <= 0:
        quote = replace(quote, pct=None)
    return quote


def _latest_premarket(symbol: str, data: MarketData, now: datetime, target: date) -> Quote | None:
    basis = previous_trading_day(target)
    candidates: list[Quote] = []
    raw = data.premarket.get(symbol)
    if raw is not None:
        premarket = _premarket_quote(raw, now, target)
        if premarket is not None:
            candidates.append(premarket)
    raw = data.overnight.get(symbol)
    if raw is not None:
        overnight = _valid_quote(raw, now, allow_snapshot=True)
        start, end = overnight_window(target)
        stamp = quote_clock(overnight) if overnight is not None else None
        if overnight is not None and overnight.session == "overnight" and stamp is not None and start <= stamp < end:
            if not overnight.is_snapshot or (start <= now < end and 0 <= now.timestamp() - stamp.timestamp() <= SNAPSHOT_MAX_AGE_SECONDS):
                candidates.append(overnight)
    # 当前盘后可作为明示参考；跨周末、假日的陈旧盘后不补位。
    raw = data.postmarket.get(symbol)
    if raw is not None:
        postmarket = _postmarket_quote(raw, now, basis)
        if postmarket is not None and 0 <= now.timestamp() - postmarket.asof.timestamp() <= 16 * 3600:
            candidates.append(postmarket)
    candidates = [quote for quote in candidates if quote.symbol == symbol]
    if not candidates:
        return None
    latest = max(candidates, key=lambda quote: quote_clock(quote).timestamp())
    if (latest.previous_date != basis or latest.previous_close is None
            or not isfinite(latest.previous_close) or latest.previous_close <= 0):
        latest = replace(latest, pct=None)
    return latest


def _latest_label(quotes: list[Quote]) -> str:
    sessions = {quote.session for quote in quotes}
    if len(sessions) > 1:
        return "最新延长行情（混合时段）"
    labels = {"premarket": "盘前行情", "overnight": "夜盘行情", "postmarket": "盘后参考"}
    return labels.get(next(iter(sessions), ""), "行情暂缺")


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
    if kind == "postmarket":
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
        reference = previous_trading_day(target)
        indices = []
        stocks = [quote for symbol in MEGA_NAMES
                  if (quote := _latest_premarket(symbol, data, now, target)) is not None]
        sectors = [quote for symbol in SECTOR_NAMES
                   if (quote := _latest_premarket(symbol, data, now, target)) is not None]
        stocks_label = _latest_label(stocks)
        complete = (len(stocks) == len(MEGA_NAMES) and len(sectors) == len(SECTOR_NAMES)
                    and all(quote.pct is not None for quote in stocks + sectors))
        for names, quotes, label in ((MEGA_NAMES, stocks, "个股"), (SECTOR_NAMES, sectors, "板块ETF")):
            present = {quote.symbol for quote in quotes}
            missing = "、".join(symbol for symbol in names if symbol not in present)
            if missing:
                notes.append(f"最新{label}行情暂缺：{missing}（无本版夜盘/盘前或最近盘后报价）")
        post_only = [quote.symbol for quote in stocks + sectors if quote.session == "postmarket"]
        if post_only:
            notes.append("夜盘暂缺，以下仅有最近盘后参考：" + "、".join(post_only))
        unknown_base = [quote.symbol for quote in stocks + sectors if quote.pct is None]
        if unknown_base:
            notes.append("比较基准或涨跌幅暂缺：" + "、".join(unknown_base))
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
        if quote is not None and quote.symbol == symbol and quote.session == "futures":
            futures.append(quote)
    if kind == "premarket":
        futures = fresh_futures(futures, now, target)
    for symbol in MACRO_NAMES:
        # 宏观参考各有市场时段，保留实际时点，不套用NYSE股票16:00规则。
        raw = data.quotes.get(symbol) or aligned.get(symbol)
        quote = _valid_quote(raw, now) if raw is not None else None
        if quote is not None:
            references.append(replace(quote, session="reference") if quote.session == "regular" else quote)
    brief = Brief(
        kind=kind, generated_at=now, edition_date=target, reference_date=reference,
        indices=indices, futures=futures, stocks=stocks, sectors=sectors,
        references=references, activity=aligned.get("SPY") if kind == "postmarket" else None,
        notes=list(dict.fromkeys(notes)), stocks_label=stocks_label, complete=complete, extended_stocks=extended_stocks,
    )
    brief.news = select_news(data.news, kind=kind, reference_date=reference, now=now, limit=6)
    brief.headline, brief.sentiment, brief.market_summary = build_narrative(brief)
    return brief
