from __future__ import annotations

from datetime import date, datetime, time

from .fetch import CST, MarketData, load_market
from .models import Brief, Narrative, Quote
from .narrative import build_narrative
from .rank import select_news


def _hero(indices: list[Quote]) -> Quote:
    for quote in indices:
        if quote.name == "上证指数" and quote.last > 0:
            return quote
    return Quote("sh000001", "上证指数", 0.0)


def _turnover(indices: list[Quote]) -> float | None:
    amounts = [quote.amount for quote in indices if quote.symbol in {"sh000001", "sz399001"} and quote.amount]
    if len(amounts) < 2:
        return None
    return float(sum(amounts))


def build_brief(kind: str, data: MarketData, now: datetime | None = None) -> Brief:
    if kind not in {"close", "morning"}:
        raise ValueError("kind 只能是 close 或 morning")
    now = now or datetime.now(CST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CST)
    hero = _hero(data.indices)
    trade_date = date.fromisoformat(hero.trade_day) if hero.trade_day else now.date()
    close_at = datetime.combine(trade_date, time(15, 0), tzinfo=CST)
    preview = kind == "morning" and now >= close_at
    news_limit = 7 if kind == "morning" else 6
    news = select_news(data.news, kind=kind, trade_date=trade_date, now=now, limit=news_limit)
    brief = Brief(
        kind=kind,
        generated_at=now,
        trade_date=trade_date,
        preview=preview,
        hero=hero,
        indices=data.indices,
        spark=data.spark,
        sectors_up=data.sectors_up,
        sectors_down=data.sectors_down,
        capital=data.capital,
        sector_in=data.sector_in,
        sector_out=data.sector_out,
        breadth=data.breadth,
        cross=data.cross,
        overseas=data.overseas,
        fx=data.fx,
        futures=data.futures,
        news=news,
        narrative=Narrative(style="", sentiment="", summary="", watch=[]),
        turnover=_turnover(data.indices),
        sector_source=data.sector_source,
        flow_source=data.flow_source,
        notes=[note for note in data.notes if not (kind == "close" and note == "要闻暂缺")],
    )
    brief.narrative = build_narrative(brief)
    return brief


def load_brief(kind: str, now: datetime | None = None) -> Brief:
    return build_brief(kind, load_market(), now=now)
