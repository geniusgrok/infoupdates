from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .fetch import CST, MarketData, load_market
from .format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
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
        notes=list(data.notes),
    )
    brief.narrative = build_narrative(brief)
    return brief


def edition_date(brief: Brief) -> date:
    if brief.kind != "morning":
        return brief.trade_date
    now = brief.generated_at
    if now.time() < time(12, 0):
        return now.date()
    day = brief.trade_date + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def load_brief(kind: str, now: datetime | None = None) -> Brief:
    return build_brief(kind, load_market(), now=now)


def brief_text(brief: Brief) -> str:
    lines = [
        f"{brief.title}  {brief.trade_date.isoformat()} {weekday_cn(brief.trade_date)}",
        f"方向 {brief.narrative.style}  情绪 {brief.narrative.sentiment}",
        brief.narrative.summary,
        "",
        "股指",
    ]
    for quote in brief.indices:
        lines.append(f"  {quote.name}  {fmt_px(quote.last)}  {fmt_pct(quote.pct)}")
    if brief.turnover:
        lines.append(f"沪深成交额 {fmt_amount(brief.turnover)}")
    if brief.breadth:
        breadth = brief.breadth
        lines.append(
            f"上涨 {breadth.up}  下跌 {breadth.down}  平 {breadth.flat}  "
            f"涨停 {breadth.limit_up}  跌停 {breadth.limit_down}"
        )
    if brief.main_net is not None:
        lines.append(f"沪深主力 {fmt_yi(brief.main_net, signed=True)}")
    if brief.cross:
        lines.append(
            f"南向 {fmt_yi(brief.cross.south_net, signed=True, unit='亿港元')}  "
            f"北向成交 {fmt_amount(brief.cross.north_turnover)}"
        )
    lines.append("")
    lines.append("要闻")
    for item in brief.news:
        lines.append(f"  {item.published.strftime('%H:%M')}  {item.title}")
    if brief.narrative.watch:
        lines.append("")
        lines.append("关注")
        lines.extend(f"  {item}" for item in brief.narrative.watch)
    if brief.notes:
        lines.append("")
        lines.extend(brief.notes)
    return "\n".join(lines)
