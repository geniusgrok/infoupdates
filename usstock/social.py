from __future__ import annotations

from math import isfinite

from common.format import fmt_pct, fmt_px, weekday_cn
from .models import Brief, Quote, new_york_time, quote_clock
from .narrative import afterhours_quotes, available_quotes, current_quotes, focus_items, sector_leaders, selected_stocks


def _phase(quote: Quote) -> str:
    return {"regular": "常规收盘", "overnight": "夜盘", "premarket": "盘前",
            "postmarket": "盘后参考", "futures": "期货", "reference": "参考"}.get(quote.session, quote.session)


def _clock(quotes: list[Quote]) -> str:
    quote = quotes[0]
    stamps = []
    for item in quotes:
        moment = quote_clock(item)
        if moment is not None:
            stamps.append(new_york_time(moment))
    if not stamps:
        return "时点待确认"
    first, last = min(stamps), max(stamps)
    value = first.strftime("%m-%d %H:%M")
    if last.strftime("%Y-%m-%d %H:%M") != first.strftime("%Y-%m-%d %H:%M"):
        value += "–" + last.strftime("%H:%M" if first.date() == last.date() else "%m-%d %H:%M")
    value += " ET"
    value = f"夜盘快照·采集{value}·成交时间未披露" if quote.is_snapshot else f"{_phase(quote)} {value}"
    if quote.cached:
        value += "·缓存"
    if quote.delay_minutes:
        value += f"·延迟{quote.delay_minutes}分钟"
    return value


def _quote_line(label: str, quotes: list[Quote], *, stocks: bool = False, prices: bool = False) -> str:
    if not quotes:
        return f"{label}：行情待确认。"

    def fact(quote: Quote) -> str:
        name = f"{quote.name}({quote.symbol})" if stocks else quote.name
        price = f" ${fmt_px(quote.last, 2)}" if prices else ""
        return f"{name}{price} {fmt_pct(quote.pct)}"

    states = {(_phase(item), item.is_snapshot, item.cached, item.delay_minutes,
               new_york_time(quote_clock(item)).date() if quote_clock(item) is not None else None)
              for item in quotes}
    if len(states) == 1:
        return f"{label}：{'、'.join(fact(quote) for quote in quotes)}（{_clock(quotes)}）。"
    return f"{label}：{'、'.join(f'{fact(quote)}（{_clock([quote])}）' for quote in quotes)}。"


def _limitations(brief: Brief) -> str:
    notes: list[str] = []
    if brief.kind == "premarket":
        stocks = len(current_quotes(brief.stocks, brief))
        sectors = len(current_quotes(brief.sectors, brief))
        if stocks < 7 or sectors < 11:
            notes.append(f"最新个股{stocks}/7、ETF{sectors}/11可用")
    else:
        if brief.reference_date is None:
            notes.append("已完成收盘数据暂缺")
        elif brief.reference_date != brief.edition_date:
            notes.append(f"收盘行情仅到{brief.reference_date.isoformat()}")
        if len(available_quotes(brief.indices)) < 4:
            notes.append("部分主指数暂缺")
    if any("日期不一致" in note for note in brief.notes):
        notes.append("日期不一致的行情已剔除")
    if brief.activity is not None and (brief.activity.cached or brief.activity.delay_minutes):
        notes.append(f"SPY日线时点：{_clock([brief.activity])}")
    if any("：" in note and note.endswith(("Error", "Exception")) for note in brief.notes):
        notes.append("部分数据源暂缺，采用可用报价")
    return f"数据说明：{'；'.join(notes)}。" if notes else ""


def social_copy(brief: Brief) -> str:
    """文字版进一步概括图片，沿用同一选股、板块与重点消息。"""
    day = brief.edition_date
    weekday = weekday_cn(day).replace("周", "星期")
    lines = [f"{brief.title}｜{day.year}年{day.month}月{day.day}日 {weekday}（美东日期）",
             "", "【市场概况】", f"{brief.headline}。", f"{brief.market_summary.rstrip('。')}。"]
    if brief.kind == "premarket" and brief.reference_date is not None:
        lines.append(f"比较基准日期：{brief.reference_date.isoformat()}收盘（个股/ETF）；期货按供应商基准。")
    primary = brief.futures if brief.kind == "premarket" else brief.indices
    lines.extend(["", "【关键表现】"])
    lines.append("  • " + _quote_line("股指期货" if brief.kind == "premarket" else "主指数", list(available_quotes(primary).values())[:4]))
    stocks = selected_stocks(brief)
    lines.append("  • " + _quote_line("重点个股", stocks, stocks=True))
    extended = afterhours_quotes(brief)
    after = [extended[quote.symbol] for quote in stocks if quote.symbol in extended]
    if after:
        def priority(quote: Quote) -> tuple[bool, float]:
            known = quote.pct is not None and isfinite(quote.pct)
            return known, abs(quote.pct) if known else 0

        after = [max(after, key=priority)]
        lines.append("  • " + _quote_line("盘后延长交易（相对当日常规收盘）", after, stocks=True, prices=True))
    strong, weak = sector_leaders(brief)
    sectors = strong[:1] + weak[:1]
    if sectors:
        label = "板块ETF（相对较强/较弱）" if weak else "板块ETF（相对较强）"
        lines.append("  • " + _quote_line(label, sectors))
    else:
        lines.append("  • 板块ETF：最新相对强弱待确认。")
    focus = focus_items(brief)
    lines.extend(["", "【两条重点】"])
    for number, item in enumerate(focus, start=1):
        if number > 1:
            lines.append("")
        lines.append(f"{number}. {item.label}：{item.title.rstrip('。')}。")
        if item.stamp:
            lines.append("   " + item.stamp)
    lines.extend(["", "【数据说明】"])
    limits = _limitations(brief)
    if limits:
        lines.append(limits.removeprefix("数据说明："))
    lines.extend(note for note in brief.notes if note.startswith(("历史", "实际采集")))
    shown = primary + stocks + after + sectors
    if brief.kind == "postmarket" and brief.activity is not None:
        shown.append(brief.activity)
    providers = sorted({quote.source.split("（", 1)[0] for quote in shown})
    now = new_york_time(brief.generated_at)
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    lines.append(f"{clock_label}{now:%m-%d %H:%M ET}；行情来源{' / '.join(providers) or '暂缺'}。")
    if brief.kind == "postmarket":
        lines.append("SPY为供应商披露日线股数代理，非全市场成交额。")
    lines.append("公开行情可能延迟，不构成投资建议。")
    return "\n".join(lines) + "\n"
