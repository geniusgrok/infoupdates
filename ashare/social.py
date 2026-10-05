from __future__ import annotations

from math import isfinite

from common.format import fmt_amount, fmt_pct, fmt_yi, weekday_cn
from .models import Brief, china_time
from .narrative import MORNING_ABROAD, focus_items, headline, market_summary


def social_copy(brief: Brief) -> str:
    """文字版是精选图片的摘要，仅保留核心事实与同一组要点。"""
    day = brief.edition_date
    title = "早盘精选" if brief.kind == "morning" else "盘中快照" if brief.is_intraday else "收盘精选"
    lead = headline(brief)
    if brief.narrative.style == "数据暂缺":
        lead += "（A股数据暂缺）"
    summary = market_summary(brief)
    if brief.kind == "morning":
        summary = f"A股参考{_a_reference(brief)}：" + summary
    lines = [f"A股{title}｜{day.year}年{day.month}月{day.day}日 {weekday_cn(day).replace('周', '星期')}",
             "", "【市场概况】", lead + "。", summary,
             "", "【关键表现】", "  • " + _indices(brief)]
    if brief.breadth:
        line = f"上涨{brief.breadth.up}家、下跌{brief.breadth.down}家"
        if brief.turnover is not None:
            line += f"，沪深成交{fmt_amount(brief.turnover)}元"
        lines.append("  • " + line + "。")
    lines.append("  • " + _sectors_and_funds(brief))
    lines.extend(["", "【两条重点】"])
    for index, item in enumerate(focus_items(brief), 1):
        if index > 1:
            lines.append("")
        lines.append(f"{index}. {item.label}：{item.title}")
        if item.stamp:
            lines.append("   " + item.stamp)
    lines.extend(["", "【数据说明】", _reference(brief)])
    limits = _limits(brief)
    if limits:
        lines.append(limits)
    lines.extend(note for note in brief.notes if note.startswith(("历史", "实际采集")))
    lines.append("公开行情可能延迟，不构成投资建议。")
    return "\n".join(lines) + "\n"


def _indices(brief: Brief) -> str:
    morning = brief.kind == "morning"
    names = MORNING_ABROAD[:3] if morning else ("上证指数", "深证成指", "创业板指", "科创50")
    aliases = {"上证指数": "上证", "深证成指": "深成指", "创业板指": "创业板", "道琼斯": "道指", "纳斯达克": "纳指"}
    quotes = {quote.name: quote for quote in (brief.overseas if morning else brief.indices)
              if isfinite(quote.last) and quote.last > 0}
    selected = [quotes[name] for name in names if name in quotes]
    prefix = "外盘" if morning else "指数"
    stamps = {quote.session or (quote.trade_day[5:] + " 收盘" if quote.trade_day else "日期未披露") for quote in selected}
    if morning and len(stamps) == 1:
        prefix += f"（{next(iter(stamps)).replace('-', '.')}）"
    parts = []
    for name in names:
        quote = quotes.get(name)
        part = f"{aliases.get(name, name)}{fmt_pct(quote.pct if quote else None)}"
        if morning and quote and len(stamps) > 1:
            part += f"（{quote.session or quote.trade_day or '日期未披露'}）"
        parts.append(part)
    return prefix + "：" + "、".join(parts) + "。"


def _sectors_and_funds(brief: Brief) -> str:
    parts = []
    if brief.sectors_up:
        parts.append(f"{brief.sectors_up[0].name}领涨")
    if brief.sectors_down:
        parts.append(f"{brief.sectors_down[0].name}承压")
    if brief.main_net is not None:
        parts.append(f"沪深主力{fmt_yi(brief.main_net, signed=True, unit='亿元')}")
    elif len(brief.capital) == 1:
        row = brief.capital[0]
        parts.append(f"{row.market}主力{fmt_yi(row.main, signed=True, unit='亿元')}")
    else:
        parts.append("主力资金待确认")
    return "；".join(parts) + "。"


def _a_reference(brief: Brief) -> str:
    phase = "行情待确认" if brief.hero.last <= 0 else "盘中行情" if brief.is_intraday else "收盘"
    return f"{brief.trade_date.month}月{brief.trade_date.day}日{phase}"


def _reference(brief: Brief) -> str:
    if brief.kind != "morning":
        reference = "行情：" + _a_reference(brief) + "；"
    elif brief.preview:
        reference = f"下次交易{brief.edition_date.month}月{brief.edition_date.day}日；"
    else:
        reference = ""
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    return reference + clock_label + f"{china_time(brief.generated_at):%m.%d %H:%M} CST。"


def _limits(brief: Brief) -> str:
    flags = []
    if brief.sectors_up or brief.sectors_down:
        flags.append("行业日期未披露")
    if any("日期不一致" in note for note in brief.notes):
        flags.append("日期不一致的数据已跳过")
    if any("日期无效" in note for note in brief.notes):
        flags.append("无效日期行情已跳过")
    if any("交易日历" in note for note in brief.notes):
        flags.append("交易日历待补，量能比较待确认")
    if any("主力资金改用腾讯" in note for note in brief.notes):
        flags.append("主力资金来自腾讯")
    return "；".join(flags) + "。" if flags else ""
