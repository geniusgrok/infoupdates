from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from common.render import AMBER, GREEN, HAIR, MUTED, RED, TEXT, WIDTH, Canvas, change_color

from .calendar import is_trading_day
from .models import Brief, Quote, china_time
from .narrative import MORNING_ABROAD, focus_items, headline, market_summary


def _quotes(brief: Brief) -> dict[str, Quote]:
    quotes = {quote.name: quote for quote in brief.indices + brief.overseas
              if isfinite(quote.last) and quote.last > 0}
    if brief.hero.last > 0:
        quotes[brief.hero.name] = brief.hero
    return quotes


def _funds(brief: Brief) -> tuple[str, float | None]:
    if brief.main_net is not None:
        return "沪深主力净额", brief.main_net
    if len(brief.capital) == 1:
        return f"{brief.capital[0].market}主力净额", brief.capital[0].main
    return "沪深主力净额", None


def _stamp(quote: Quote | None, morning: bool, intraday: bool) -> str:
    if quote is None:
        return "行情待确认"
    if morning and quote.session:
        return quote.session.replace("-", ".")
    if not quote.trade_day:
        return "行情日期未披露"
    day = quote.trade_day[5:].replace("-", ".")
    if morning:
        return f"{day} {'收盘' if quote.name in MORNING_ABROAD[:3] else '行情'}"
    return f"{day} {quote.session[:5]} CST" if intraday and quote.session else f"{day} {'盘中' if intraday else '收盘'}"


def _header(canvas: Canvas, brief: Brief, quotes: dict[str, Quote]) -> None:
    morning = brief.kind == "morning"
    day = brief.edition_date
    title = "A股早盘精选" if morning else "A股盘中快照" if brief.is_intraday else "A股收盘精选"
    canvas.text(32, 24, "INFOUPDATES", 26, AMBER, "bold")
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    canvas.text(1588, 27, clock_label + china_time(brief.generated_at).strftime("%m.%d %H:%M CST"), 20, MUTED, align="right")
    canvas.text(32, 68, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · {title}", 34, TEXT, "bold", max_width=1556)
    canvas.text(32, 120, headline(brief), 40, AMBER, "bold", max_width=1280, min_size=34)
    canvas.text(1588, 134, "外盘参考" if morning else brief.narrative.style, 24, MUTED, align="right", max_width=240)
    canvas.text(32, 182, market_summary(brief), 28, TEXT, "medium", max_width=1556, min_size=22)
    canvas.line(32, 230, 1588, color=AMBER, width=2)

    names = MORNING_ABROAD if morning else ("上证指数", "深证成指", "创业板指", "科创50")
    col = (1556 - 48) / 4
    for i, name in enumerate(names):
        x = 32 + i * (col + 16)
        quote = quotes.get(name)
        canvas.card(x, 248, col, 128, radius=14)
        canvas.text(x + 20, 264, name, 26, MUTED, "medium", max_width=col - 40)
        canvas.pair(x + 20, 304, col - 40, fmt_px(quote.last, 2) if quote else "—",
                    fmt_pct(quote.pct if quote else None), 34, 30,
                    change_color(quote.pct if quote else None), weight="medium")
        canvas.text(x + 20, 348, _stamp(quote, morning, brief.is_intraday), 18,
                    MUTED, max_width=col - 40, min_size=16)


def _breadth(canvas: Canvas, brief: Brief) -> None:
    canvas.card(32, 392, 480, 368, radius=14)
    canvas.text(54, 414, "市场温度", 26, AMBER, "bold")
    reference = "盘中快照" if brief.is_intraday else "收盘参考"
    if brief.kind == "morning" and brief.preview:
        today = china_time(brief.generated_at).date()
        label = "休市前瞻" if not is_trading_day(today) or (brief.edition_date - today).days > 1 else "次日预览"
        reference = f"{label} · 下次交易{brief.edition_date:%m.%d} · A股参考{brief.trade_date:%m.%d}"
    else:
        reference = f"{brief.trade_date:%m.%d} A股{reference}"
    canvas.paragraph(54, 454, reference, 436, 18, 2, MUTED, pitch=25)
    breadth = brief.breadth
    for x, label, value, color in (
        (54, "上涨家数", str(breadth.up) if breadth else "—", RED),
        (286, "下跌家数", str(breadth.down) if breadth else "—", GREEN),
    ):
        canvas.text(x, 512, label, 22, MUTED, max_width=204)
        canvas.text(x, 548, value, 34, color, "medium", max_width=204)
    canvas.pair(54, 608, 436, "沪深成交额", fmt_amount(brief.turnover), 22, 30,
                TEXT, label_color=MUTED, weight="medium")
    canvas.line(54, 650, 490)
    counts = [str(count) if count is not None else "—" for count in (
        breadth.limit_up if breadth else None, breadth.limit_down if breadth else None, breadth.flat if breadth else None)]
    canvas.text(54, 669, f"涨停 {counts[0]} · 跌停 {counts[1]}", 22, max_width=436)
    cursor = 54.0
    if breadth and breadth.total > 0:
        for count, color in ((breadth.up, RED), (breadth.down, GREEN), (breadth.flat, MUTED)):
            length = 436 * count / breadth.total
            if length > 0:
                canvas.draw.rectangle((cursor, 707, cursor + length, 722), fill=color)
            cursor += length
    else:
        canvas.draw.rectangle((54, 707, 490, 722), fill=HAIR)
    canvas.text(54, 736, f"平盘 {counts[2]}", 18, MUTED)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    canvas.card(528, 392, 552, 368, radius=14)
    canvas.text(550, 414, "板块强弱", 26, AMBER, "bold")
    canvas.text(550, 454, brief.sector_source, 18, MUTED, max_width=508)
    canvas.line(550, 612, 1058)
    for y, label, rows, color in ((493, "领涨", brief.sectors_up, RED), (631, "承压", brief.sectors_down, GREEN)):
        canvas.text(550, y, label, 24, color, "medium")
        for i in range(2):
            row = rows[i] if i < len(rows) else None
            canvas.pair(550, y + 37 + i * 42, 508, row.name if row else "待确认", fmt_pct(row.pct) if row else "—", 26, 28,
                        change_color(row.pct if row else None), weight="medium")


def _capital(canvas: Canvas, brief: Brief) -> None:
    canvas.card(1096, 392, 492, 368, radius=14)
    canvas.text(1118, 414, "关键资金", 26, AMBER, "bold")
    canvas.text(1118, 454, brief.flow_source, 18, MUTED, max_width=448)
    label, net = _funds(brief)
    canvas.text(1118, 493, label, 22, MUTED, max_width=448)
    canvas.text(1118, 530, fmt_yi(net, signed=True), 34, change_color(net), "medium", max_width=448)
    canvas.line(1118, 586, 1566)
    cross = brief.cross
    canvas.pair(1118, 607, 448, "南向净买入", fmt_yi(cross.south_net if cross else None, unit="亿港元", signed=True),
                24, 26, color=change_color(cross.south_net if cross else None), label_color=MUTED, weight="medium")
    canvas.pair(1118, 650, 448, "北向成交额", fmt_yi(cross.north_turnover if cross else None),
                24, 26, color=TEXT, label_color=MUTED, weight="medium")
    canvas.line(1118, 697, 1566)
    flows = [flow for flow in brief.sector_out + brief.sector_in if isfinite(flow.net)]
    flow = max(flows, key=lambda item: abs(item.net)) if flows else None
    canvas.pair(1118, 715, 448, f"行业主力 · {flow.name}" if flow else "行业主力待确认",
                fmt_yi(flow.net, signed=True) if flow else "—", 22, 26,
                change_color(flow.net if flow else None), label_color=MUTED, weight="medium")


def _focus(canvas: Canvas, brief: Brief) -> None:
    items = focus_items(brief)
    for i, item in enumerate(items[:2]):
        x = 32 + i * 786
        canvas.card(x, 784, 770, 244, radius=14)
        canvas.text(x + 22, 806, item.label, 26, AMBER, "medium", max_width=726)
        canvas.paragraph(x + 22, 850, item.title, 726, 29, 2, weight="medium", pitch=38)
        if item.context:
            canvas.text(x + 22, 942, item.context, 24, MUTED, max_width=726)
        if item.stamp:
            canvas.text(x + 22, 995, item.stamp, 18, MUTED, max_width=726)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief, _quotes(brief))
    _breadth(canvas, brief)
    _sectors(canvas, brief)
    _capital(canvas, brief)
    _focus(canvas, brief)
    status = "历史行情回放 · 缺失项目与采集时间见文案" if any(note.startswith("历史") for note in brief.notes) else "行业日期未披露 · 精简摘要及数据说明见文案"
    canvas.text(32, 1048, status, 18, MUTED, max_width=800)
    canvas.text(1588, 1048, "公开行情可能延迟 · 不构成投资建议", 18, MUTED, align="right", max_width=600)
    path = Path(path)
    canvas.save(path)
    return path
