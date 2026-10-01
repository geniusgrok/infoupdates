from __future__ import annotations

from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from .models import Brief, Quote
from .narrative import market_summary
from .parse import INDEX_ORDER

MORNING_ABROAD = ("道琼斯", "纳斯达克", "标普500", "日经225", "韩国KOSPI", "韩国KOSDAQ")
CLOSE_ABROAD = MORNING_ABROAD + ("恒生指数", "恒生科技")


def social_copy(brief: Brief) -> str:
    if brief.kind == "morning":
        return _morning_copy(brief)
    return _close_copy(brief)


def _dated_title(brief: Brief) -> str:
    shown = brief.edition_date
    return f"A股{brief.title}｜{shown.month}月{shown.day}日 {weekday_cn(shown)}"


def _quote_lines(quotes: list[Quote]) -> list[str]:
    lines = []
    for quote in quotes:
        lines.append(f"{quote.name}  {fmt_px(quote.last)}  {fmt_pct(quote.pct)}")
    return lines


def _pick(brief: Brief, names: tuple[str, ...], source: list[Quote]) -> list[Quote]:
    by_name = {quote.name: quote for quote in source}
    if brief.hero.last > 0:
        by_name[brief.hero.name] = brief.hero
    return [by_name[name] for name in names if name in by_name and by_name[name].last > 0]


def _sector_lines(brief: Brief) -> list[str]:
    lines: list[str] = []
    if brief.sectors_up:
        lines.append("领涨")
        for item in brief.sectors_up:
            leader = f"（{item.leader}）" if item.leader else ""
            lines.append(f"{item.name}  {fmt_pct(item.pct)}{leader}")
    if brief.sectors_down:
        lines.append("领跌")
        for item in brief.sectors_down:
            leader = f"（{item.leader}）" if item.leader else ""
            lines.append(f"{item.name}  {fmt_pct(item.pct)}{leader}")
    return lines


def _capital_lines(brief: Brief) -> list[str]:
    lines: list[str] = []
    if brief.main_net is not None:
        detail = "  ".join(f"{item.market}{fmt_yi(item.main, signed=True)}" for item in brief.capital)
        lines.append(f"沪深主力 {fmt_yi(brief.main_net, signed=True)}" + (f"（{detail}）" if detail else ""))
    else:
        for item in brief.capital:
            lines.append(f"{item.market}主力 {fmt_yi(item.main, signed=True)}")
    if brief.sector_in:
        lines.append("净流入  " + "  ".join(f"{item.name}{fmt_yi(item.net, signed=True)}" for item in brief.sector_in[:3]))
    if brief.sector_out:
        lines.append("净流出  " + "  ".join(f"{item.name}{fmt_yi(item.net, signed=True)}" for item in brief.sector_out[:3]))
    if brief.cross and brief.cross.south_net is not None:
        lines.append(f"南向净买入 {fmt_yi(brief.cross.south_net, signed=True, unit='亿港元')}")
    if brief.cross and brief.cross.north_turnover is not None:
        lines.append(f"北向成交 {fmt_amount(brief.cross.north_turnover)}，净买入不再逐日披露")
    return lines


def _close_copy(brief: Brief) -> str:
    blocks = [
        _dated_title(brief),
        "",
        f"{brief.narrative.style}，情绪{brief.narrative.sentiment}。",
        market_summary(brief),
        brief.narrative.summary,
        "",
        "主要指数",
        *_quote_lines(_pick(brief, INDEX_ORDER, brief.indices)),
    ]
    if brief.turnover is not None:
        blocks.append(f"沪深成交额 {fmt_amount(brief.turnover)}")
    if brief.breadth:
        breadth = brief.breadth
        blocks.append(
            f"上涨{breadth.up}家，下跌{breadth.down}家，平{breadth.flat}家，涨停{breadth.limit_up if breadth.limit_up is not None else '—'}，跌停{breadth.limit_down if breadth.limit_down is not None else '—'}"
        )
    sectors = _sector_lines(brief)
    if sectors:
        blocks.extend(["", "板块", *sectors])
    capital = _capital_lines(brief)
    if capital:
        blocks.extend(["", "资金", *capital])
    abroad = _quote_lines(_pick(brief, CLOSE_ABROAD, brief.overseas))
    if abroad:
        blocks.extend(["", "外围", *abroad])
    fx = _quote_lines(brief.fx)
    if fx:
        blocks.extend(fx)
    blocks.extend(["", *_notes(brief), "公开行情可能延迟，只做信息整理，不构成投资建议。"])
    return "\n".join(blocks).strip() + "\n"


def _morning_copy(brief: Brief) -> str:
    reference = "盘中行情" if brief.is_intraday else "收盘"
    target = "下个交易日" if brief.preview else "今天"
    blocks = [
        _dated_title(brief),
        f"对照{brief.trade_date.month}月{brief.trade_date.day}日{reference}，看海外行情和{target}该盯的板块。",
        "",
        f"{brief.session_label()}情绪",
        f"{brief.narrative.style}，情绪{brief.narrative.sentiment}。",
        market_summary(brief),
        brief.narrative.summary,
        "",
        f"{brief.session_label()}指数",
        *_quote_lines(_pick(brief, INDEX_ORDER, brief.indices)),
    ]
    if brief.breadth:
        breadth = brief.breadth
        blocks.append(
            f"上涨{breadth.up}家，下跌{breadth.down}家，涨停{breadth.limit_up if breadth.limit_up is not None else '—'}，跌停{breadth.limit_down if breadth.limit_down is not None else '—'}"
        )
    sectors = _sector_lines(brief)
    if sectors:
        blocks.extend(["", f"{brief.session_label()}板块", *sectors])
    abroad = _quote_lines(_pick(brief, MORNING_ABROAD, brief.overseas))
    if abroad:
        blocks.extend(["", "隔夜外盘", *abroad])
    if brief.news:
        blocks.append("")
        blocks.append("隔夜要闻")
        for item in brief.news:
            blocks.append(f"{item.published.strftime('%H:%M')}  {item.title}")
    if brief.narrative.watch:
        blocks.extend(["", "下个交易日关注" if brief.preview else "今日关注"])
        for index, item in enumerate(brief.narrative.watch, start=1):
            blocks.append(f"{index}. {item}")
    blocks.extend(["", *_notes(brief), "公开行情可能延迟，只做信息整理，不构成投资建议。"])
    return "\n".join(blocks).strip() + "\n"


def _notes(brief: Brief) -> list[str]:
    return [note for note in brief.notes if note]
