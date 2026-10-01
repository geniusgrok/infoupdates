from __future__ import annotations

from common.format import fmt_pct, fmt_px, weekday_cn
from .models import Brief, Quote, new_york_time


def _quotes(quotes: list[Quote]) -> list[str]:
    rows: list[str] = []
    for quote in quotes:
        session = {"regular": "常规场", "overnight": "夜盘", "premarket": "盘前", "postmarket": "盘后延长交易", "futures": "期货", "reference": "参考"}.get(quote.session, quote.session)
        if quote.is_snapshot:
            stamp = f"夜盘快照 · 采集{new_york_time(quote.observed_at):%m-%d %H:%M ET} · 成交时间未披露"
        else:
            stamp = new_york_time(quote.asof).strftime("%m-%d %H:%M ET") if quote.asof else "时点待确认"
        status = " · 缓存" if quote.cached else ""
        if quote.delay_minutes:
            status += f" · 延迟{quote.delay_minutes}分钟"
        unit = {"USD": "美元", "points": "点", "%": "%"}.get(quote.unit, quote.unit)
        rows.append(f"{quote.name} ({quote.symbol})  {fmt_px(quote.last)}{unit}  {fmt_pct(quote.pct)}  [{session} {stamp}；{quote.source}{status}]")
    return rows


def social_copy(brief: Brief) -> str:
    day = brief.edition_date
    lines = [
        f"{brief.title}｜{day.month}月{day.day}日 {weekday_cn(day)}（美东日期）",
        f"生成时间 {brief.generated_at.strftime('%Y-%m-%d %H:%M %Z')}",
        "", brief.headline, brief.market_summary,
    ]
    if brief.reference_date is not None:
        label = "比较基准日期" if brief.kind == "premarket" else "常规场参考"
        lines.extend(["", f"{label}：{brief.reference_date.isoformat()}收盘"])
    if brief.kind == "postmarket":
        lines.extend(["", "主指数（完成常规场）", *_quotes(brief.indices)])
    if brief.futures:
        lines.extend(["", "股指期货（各项实际时点；相对供应商参考基准）", *_quotes(brief.futures)])
    if brief.stocks:
        lines.extend(["", f"大型科技股｜{brief.stocks_label}", *_quotes(brief.stocks)])
    if brief.extended_stocks:
        lines.extend(["", "盘后延长交易（相对当日常规收盘）", *_quotes(brief.extended_stocks)])
    if brief.sectors:
        label = "最新延长交易" if brief.kind == "premarket" else "参考常规场"
        lines.extend(["", f"11类板块ETF（{label}，不代表全市场涨跌家数）", *_quotes(brief.sectors)])
    if brief.kind == "postmarket" and brief.activity is not None and brief.activity.volume is not None:
        lines.extend(["", f"SPY披露日线股数代理 {brief.activity.volume:,.0f}股（{brief.activity.source}；供应商日线口径，非全市场成交额）"])
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
