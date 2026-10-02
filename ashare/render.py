from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from common.render import AMBER, BG, GREEN, HAIR, LINE, MUTED, RED, TEXT, WIDTH, Canvas, change_color, font, text_width

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
    canvas.text(28, 24, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(1052, 27, china_time(brief.generated_at).strftime("生成%m.%d %H:%M CST"), 24, MUTED, align="right")
    canvas.text(28, 75, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · {title}", 51, TEXT, "bold", max_width=1024, min_size=44)
    canvas.text(28, 143, headline(brief), 44, AMBER, "bold", max_width=740, min_size=36)
    canvas.text(1052, 155, "外盘参考" if morning else brief.narrative.style, 27, MUTED, align="right", max_width=264)
    canvas.card(28, 199, 1024, 54, radius=12)
    canvas.text(52, 214, market_summary(brief), 30, TEXT, "medium", max_width=976, min_size=25)

    names = MORNING_ABROAD if morning else ("上证指数", "深证成指", "创业板指", "科创50")
    col = (1024 - 36) / 4
    for i, name in enumerate(names):
        x = 28 + i * (col + 12)
        quote = quotes.get(name)
        canvas.card(x, 270, col, 186)
        canvas.text(x + col / 2, 288, name, 33, MUTED, "medium", "center", max_width=col - 24)
        canvas.text(x + col / 2, 335, fmt_pct(quote.pct if quote else None), 44,
                    change_color(quote.pct if quote else None), "medium", "center", max_width=col - 24, min_size=35)
        canvas.text(x + col / 2, 386, fmt_px(quote.last, 2) if quote else "—", 28,
                    TEXT, align="center", max_width=col - 24, min_size=24)
        canvas.text(x + col / 2, 425, _stamp(quote, morning, brief.is_intraday), 20,
                    MUTED, align="center", max_width=col - 24)


def _breadth(canvas: Canvas, brief: Brief) -> None:
    canvas.card(28, 474, 1024, 176)
    canvas.heading(50, 493, "市场温度")
    reference = "盘中快照" if brief.is_intraday else "收盘参考"
    if brief.kind == "morning" and brief.preview:
        today = china_time(brief.generated_at).date()
        label = "休市前瞻" if not is_trading_day(today) or (brief.edition_date - today).days > 1 else "次日预览"
        reference = f"{label} · 下次交易{brief.edition_date:%m.%d} · A股参考{brief.trade_date:%m.%d}"
    else:
        reference = f"{brief.trade_date:%m.%d} A股{reference}"
    canvas.text(1030, 503, reference, 24, MUTED, align="right", max_width=770)
    breadth = brief.breadth
    for x, label, value, color in (
        (50, "上涨家数", str(breadth.up) if breadth else "—", RED),
        (235, "下跌家数", str(breadth.down) if breadth else "—", GREEN),
        (430, "沪深成交额", fmt_amount(brief.turnover), TEXT),
    ):
        canvas.text(x, 548, label, 27, MUTED, max_width=165)
        canvas.text(x, 585, value, 36, color, "medium", max_width=170, min_size=29)
    canvas.line(620, 544, 620, 630, LINE)
    counts = [str(count) if count is not None else "—" for count in (
        breadth.limit_up if breadth else None, breadth.limit_down if breadth else None, breadth.flat if breadth else None)]
    canvas.text(644, 548, f"涨停 {counts[0]} · 跌停 {counts[1]}", 28, max_width=384)
    cursor = 644.0
    if breadth and breadth.total > 0:
        for count, color in ((breadth.up, RED), (breadth.down, GREEN), (breadth.flat, MUTED)):
            length = 384 * count / breadth.total
            if length > 0:
                canvas.draw.rectangle((cursor, 588, cursor + length, 605), fill=color)
            cursor += length
    else:
        canvas.draw.rectangle((644, 588, 1028, 605), fill=HAIR)
    canvas.text(644, 618, f"平盘 {counts[2]}", 22, MUTED)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    canvas.card(28, 668, 1024, 249)
    canvas.heading(50, 690, "板块强弱")
    canvas.text(1030, 699, brief.sector_source, 24, MUTED, align="right", max_width=600)
    canvas.line(540, 749, 540, 895, LINE)
    for x, label, rows, color in ((50, "领涨", brief.sectors_up, RED), (572, "承压", brief.sectors_down, GREEN)):
        canvas.text(x, 748, label, 28, color, "medium")
        for i in range(2):
            row = rows[i] if i < len(rows) else None
            y = 791 + i * 62
            canvas.pair(x, y, 456, row.name if row else "待确认", fmt_pct(row.pct) if row else "—", 32, 34,
                        change_color(row.pct if row else None), weight="medium")
            if i == 0:
                canvas.line(x, y + 45, x + 456)


def _capital(canvas: Canvas, brief: Brief) -> None:
    canvas.card(28, 935, 1024, 217)
    canvas.heading(50, 955, "关键资金")
    canvas.text(1030, 964, brief.flow_source, 24, MUTED, align="right", max_width=600)
    label, net = _funds(brief)
    canvas.text(50, 1010, label, 28, MUTED, max_width=460)
    canvas.text(50, 1051, fmt_yi(net, signed=True), 36, change_color(net), "medium", max_width=460)
    canvas.line(552, 1010, 552, 1090, LINE)
    cross = brief.cross
    canvas.pair(580, 1010, 448, "南向净买入", fmt_yi(cross.south_net if cross else None, unit="亿港元", signed=True),
                29, color=change_color(cross.south_net if cross else None), label_color=MUTED, weight="medium")
    canvas.pair(580, 1055, 448, "北向成交额", fmt_yi(cross.north_turnover if cross else None),
                29, color=TEXT, label_color=MUTED, weight="medium")
    canvas.line(50, 1102, 1030)
    flows = [flow for flow in brief.sector_out + brief.sector_in if isfinite(flow.net)]
    flow = max(flows, key=lambda item: abs(item.net)) if flows else None
    canvas.pair(50, 1114, 978, f"行业主力 · {flow.name}" if flow else "行业主力待确认",
                fmt_yi(flow.net, signed=True) if flow else "—", 29, 31,
                change_color(flow.net if flow else None), label_color=MUTED, weight="medium")


def _focus(canvas: Canvas, brief: Brief) -> None:
    items = focus_items(brief)
    canvas.card(28, 1170, 1024, 398)
    canvas.heading(50, 1192, "最重要的两件事")
    for i, item in enumerate(items[:2]):
        y = 1248 + i * 164
        canvas.text(50, y + 2, item.label, 25, AMBER, "medium", max_width=130)
        canvas.paragraph(204, y, item.title, 824, 32, 2, weight="medium", pitch=38)
        if item.context and text_width(item.title, font(32, "medium")) <= 824:
            canvas.text(204, y + 42, item.context, 25, MUTED, max_width=824)
        if item.stamp:
            canvas.text(204, y + 114, item.stamp, 20, MUTED, max_width=824)
        if i == 0:
            canvas.line(50, y + 142, 1030)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief, _quotes(brief))
    _breadth(canvas, brief)
    _sectors(canvas, brief)
    _capital(canvas, brief)
    _focus(canvas, brief)
    canvas.text(28, 1590, "行业日期未披露 · 精简摘要及数据说明见文案", 18, MUTED, max_width=600)
    canvas.text(1052, 1590, "公开行情可能延迟 · 不构成投资建议", 18, MUTED, align="right", max_width=420)
    canvas.save(path)
    return Path(path)
