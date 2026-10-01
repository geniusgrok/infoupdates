from __future__ import annotations

from datetime import date, datetime, time
from math import isfinite

from .fetch import CST, MarketData, load_market
from .models import HOLIDAY_RANGES, Brief, Narrative, Quote, china_time, is_trading_day, previous_trading_day
from .narrative import build_narrative
from .rank import select_news


def _hero(indices: list[Quote]) -> Quote:
    for quote in indices:
        if quote.name == "上证指数" and isfinite(quote.last) and quote.last > 0:
            return quote
    return Quote("sh000001", "上证指数", 0.0)


def _turnover(indices: list[Quote], trade_date: date) -> float | None:
    by_symbol = {quote.symbol: quote for quote in indices if quote.symbol in {"sh000001", "sz399001"}}
    if set(by_symbol) != {"sh000001", "sz399001"}:
        return None
    amounts: list[float] = []
    for quote in by_symbol.values():
        if quote.trade_day != trade_date.isoformat():
            return None
        if quote.amount is None or not isfinite(quote.amount) or quote.amount < 0:
            return None
        amounts.append(quote.amount)
    return sum(amounts)


def build_brief(kind: str, data: MarketData, now: datetime | None = None) -> Brief:
    if kind not in {"close", "morning"}:
        raise ValueError("kind 只能是 close 或 morning")
    now = china_time(now or datetime.now(CST))
    hero = _hero(data.indices)
    notes = [note for note in data.notes if not (kind == "close" and note == "要闻暂缺")]
    indices: list[Quote] = []
    for quote in data.indices:
        if not isfinite(quote.last) or quote.last <= 0:
            continue
        try:
            if quote.trade_day and date.fromisoformat(quote.trade_day) > now.date():
                raise ValueError("future quote")
        except ValueError:
            notes.append(f"{quote.name}日期无效，行情暂缺")
            continue
        indices.append(quote)
    trade_date = now.date()
    if hero.last > 0:
        try:
            trade_date = date.fromisoformat(hero.trade_day)
            if trade_date > now.date():
                raise ValueError("future quote")
        except ValueError:
            hero = Quote("sh000001", "上证指数", 0.0)
            trade_date = now.date()
            notes.append("上证指数日期无效，市场结论待确认")
    indices = [quote for quote in indices if (not quote.trade_day or quote.trade_day == trade_date.isoformat())
               and not (hero.last <= 0 and quote.name == "上证指数")]
    capital = [item for item in data.capital if not item.trade_day or item.trade_day == trade_date.isoformat()]
    if len(capital) != len(data.capital):
        notes.append("主力资金日期不一致，已跳过旧数据")
    cross = data.cross
    if cross and cross.trade_day and cross.trade_day != trade_date.isoformat():
        cross = None
        notes.append("跨境资金日期不一致，已跳过旧数据")
    known_calendar = now.year in HOLIDAY_RANGES
    if not known_calendar:
        notes.append(f"交易日历尚未覆盖{now.year}年，量能比较待确认")
    preview = kind == "morning" and (not known_calendar or not is_trading_day(now.date()) or now.time() >= time(15, 0))
    news_limit = 7 if kind == "morning" else 6
    news = select_news(data.news, kind=kind, trade_date=trade_date, now=now, limit=news_limit)
    brief = Brief(
        kind=kind,
        generated_at=now,
        trade_date=trade_date,
        preview=preview,
        hero=hero,
        indices=indices,
        spark=data.spark,
        sectors_up=data.sectors_up,
        sectors_down=data.sectors_down,
        capital=capital,
        sector_in=data.sector_in,
        sector_out=data.sector_out,
        breadth=data.breadth,
        cross=cross,
        overseas=data.overseas,
        fx=data.fx,
        futures=data.futures,
        news=news,
        narrative=Narrative(style="", sentiment="", summary="", watch=[]),
        turnover=_turnover(indices, trade_date),
        sector_source=data.sector_source,
        flow_source=data.flow_source,
        notes=notes,
    )
    comparison = data.turnover_comparison
    if comparison is not None and hero.last > 0 and not brief.is_intraday:
        try:
            valid = (
                comparison.trade_date == trade_date
                and is_trading_day(trade_date)
                and comparison.previous_date == previous_trading_day(trade_date)
                and isfinite(comparison.current) and comparison.current > 0
                and isfinite(comparison.previous) and comparison.previous > 0
            )
        except ValueError:
            valid = False
        if valid:
            brief.turnover_comparison = comparison
        else:
            brief.notes.append("沪市成交额比较日期或数据不完整，量能待确认")
    brief.narrative = build_narrative(brief)
    return brief


def load_brief(kind: str, now: datetime | None = None) -> Brief:
    return build_brief(kind, load_market(), now=now)
