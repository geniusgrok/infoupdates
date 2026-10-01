from __future__ import annotations

from brief_common.format import fmt_pct, fmt_px, weekday_cn
from .models import Brief, Quote, new_york_time


def _quotes(quotes: list[Quote]) -> list[str]:
    rows: list[str] = []
    for quote in quotes:
        session = {"regular": "常规场", "premarket": "盘前", "postmarket": "盘后延长交易", "futures": "期货", "reference": "参考"}.get(quote.session, quote.session)
        stamp = new_york_time(quote.asof).strftime("%m-%d %H:%M ET") if quote.asof else "时点待确认"
        unit = {"USD": "美元", "points": "点", "%": "%"}.get(quote.unit, quote.unit)
        rows.append(f"{quote.name} ({quote.symbol})  {fmt_px(quote.last)}{unit}  {fmt_pct(quote.pct)}  [{session} {stamp}]")
    return rows


def social_copy(brief: Brief) -> str:
    day = brief.edition_date
    lines = [
        f"{brief.title}｜{day.month}月{day.day}日 {weekday_cn(day)}（美东日期）",
        f"生成时间 {brief.generated_at.strftime('%Y-%m-%d %H:%M %Z')}",
        "", brief.headline, brief.market_summary,
    ]
    if brief.reference_date is not None:
        lines.extend(["", f"常规场参考：{brief.reference_date.isoformat()}收盘"])
    lines.extend(["", "主指数（完成常规场）", *_quotes(brief.indices)])
    if brief.futures:
        lines.extend(["", "股指期货（各项实际时点；相对供应商参考基准）", *_quotes(brief.futures)])
    if brief.stocks:
        lines.extend(["", f"大型科技股｜{brief.stocks_label}", *_quotes(brief.stocks)])
    if brief.extended_stocks:
        lines.extend(["", "盘后延长交易（相对当日常规收盘）", *_quotes(brief.extended_stocks)])
    if brief.sectors:
        lines.extend(["", "11类板块ETF（参考常规场，不代表全市场涨跌家数）", *_quotes(brief.sectors)])
    if brief.activity is not None and brief.activity.volume is not None:
        lines.extend(["", f"SPY全日成交量 {brief.activity.volume:,.0f}股（单只ETF，不是成交额或全市场量能）"])
    if brief.references:
        lines.extend(["", "宏观参考（按各项实际时点）", *_quotes(brief.references)])
    if brief.news:
        lines.extend(["", "美股相关要闻"])
        for item in brief.news:
            lines.append(f"{item.published.strftime('%m-%d %H:%M ET')}  {item.title}  [{item.source}]")
    if brief.notes:
        lines.extend(["", "数据说明", *brief.notes])
    lines.extend(["", "公开行情可能延迟，ETF表现仅作参考，不构成投资建议。"])
    return "\n".join(lines).strip() + "\n"
