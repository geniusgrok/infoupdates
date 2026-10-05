from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from common.render import AMBER, DEFAULT_WATERMARK, GREEN, MUTED, RED, TEXT, WIDTH, Canvas, change_color

from .calendar import is_trading_day
from .models import Brief, Quote, china_time
from .narrative import MORNING_ABROAD, focus_items, headline, market_summary


def _quotes(brief: Brief) -> dict[str, Quote]:
    quotes = {quote.name: quote for quote in brief.indices + brief.overseas
              if isfinite(quote.last) and quote.last > 0}
    if isfinite(brief.hero.last) and brief.hero.last > 0:
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


def _reference(brief: Brief) -> str:
    if brief.kind == "morning" and brief.preview:
        today = china_time(brief.generated_at).date()
        label = "休市前瞻" if not is_trading_day(today) or (brief.edition_date - today).days > 1 else "次日预览"
        return f"{label} · 下次交易{brief.edition_date:%m.%d} · A股参考{brief.trade_date:%m.%d}"
    phase = "盘中快照" if brief.is_intraday else "收盘参考"
    return f"{brief.trade_date:%m.%d} A股{phase}"


def _header(canvas: Canvas, brief: Brief, quotes: dict[str, Quote]) -> None:
    morning = brief.kind == "morning"
    day = brief.edition_date
    session = "A股早盘" if morning else "A股盘中" if brief.is_intraday else "A股收盘"
    title = f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')}·{session} {headline(brief)}"
    canvas.text(32, 24, "INFOUPDATES", 24, AMBER, "bold")
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    canvas.text(1588, 29, clock_label + china_time(brief.generated_at).strftime("%m.%d %H:%M CST"),
                18, MUTED, align="right")
    canvas.text(32, 70, title, 36, AMBER, "bold", max_width=1556, min_size=24)
    canvas.text(32, 122, market_summary(brief), 26, TEXT, "medium", max_width=1556, min_size=22)
    canvas.line(32, 156, 1588, color=AMBER, width=2)

    names = MORNING_ABROAD if morning else ("上证指数", "深证成指", "创业板指", "科创50")
    col = (1556 - 48) / 4
    for i, name in enumerate(names):
        x = 32 + i * (col + 16)
        quote = quotes.get(name)
        canvas.card(x, 174, col, 108, radius=12)
        canvas.text(x + 20, 187, name, 24, MUTED, "medium", max_width=col - 40)
        canvas.pair(x + 20, 217, col - 40, fmt_px(quote.last, 2) if quote else "—",
                    fmt_pct(quote.pct if quote else None), 30, 32,
                    change_color(quote.pct if quote else None), weight="medium")
        canvas.text(x + 20, 254, _stamp(quote, morning, brief.is_intraday), 18,
                    MUTED, max_width=col - 40, min_size=16)


def _market(canvas: Canvas, brief: Brief, quotes: dict[str, Quote]) -> None:
    canvas.card(32, 298, 1004, 394, radius=14)
    breadth = brief.breadth
    canvas.text(54, 318, "市场温度" if breadth and breadth.total > 0 else "量能对照", 26, AMBER, "bold")
    reference = _reference(brief)
    if brief.kind == "morning" and brief.preview:
        first, last = reference.rsplit(" · ", 1)
        canvas.text(54, 355, first, 18, MUTED, max_width=330)
        canvas.text(54, 380, last, 18, MUTED, max_width=330)
    else:
        canvas.text(54, 355, reference, 18, MUTED, max_width=330)
    canvas.line(406, 318, 406, 670)
    if breadth and breadth.total > 0:
        for x, label, value, color in ((54, "上涨家数", breadth.up, RED), (224, "下跌家数", breadth.down, GREEN)):
            canvas.text(x, 414, label, 21, MUTED)
            canvas.text(x, 447, str(value), 32, color, "medium", max_width=160)
        canvas.pair(54, 501, 330, "沪深成交额", fmt_amount(brief.turnover), 22, 28,
                    TEXT, label_color=MUTED, weight="medium")
        up = str(breadth.limit_up) if breadth.limit_up is not None else "—"
        down = str(breadth.limit_down) if breadth.limit_down is not None else "—"
        canvas.text(54, 550, f"涨停 {up} · 跌停 {down}", 22, max_width=330)
        canvas.text(54, 587, f"平盘 {breadth.flat}", 20, MUTED)
        cursor = 54.0
        for count, color in ((breadth.up, RED), (breadth.down, GREEN), (breadth.flat, MUTED)):
            length = 330 * count / breadth.total
            if length > 0:
                canvas.draw.rectangle((cursor, 625, cursor + length, 642), fill=color)
            cursor += length
    else:
        rows = []
        comparison = brief.turnover_comparison
        if comparison is not None:
            rows.extend((
                ("沪市全日", fmt_px(comparison.current / 1e8, 2) + "亿", TEXT),
                (f"上日 {comparison.previous_date:%m.%d}", fmt_px(comparison.previous / 1e8, 2) + "亿", MUTED),
                ("较上日", f"{(comparison.current - comparison.previous) / 1e8:+,.1f}亿",
                 change_color(comparison.current - comparison.previous)),
            ))
        if brief.turnover is not None and isfinite(brief.turnover) and brief.turnover >= 0:
            rows.append(("沪深成交额", fmt_amount(brief.turnover), TEXT))
        if not rows:
            rows = [(quote.name, fmt_px(quote.last, 2), TEXT) for quote in brief.indices[:4] if isfinite(quote.last) and quote.last > 0]
        for i, (label, value, color) in enumerate(rows[:4]):
            canvas.pair(54, 412 + i * 55, 330, label, value, 22, 26, color,
                        label_color=MUTED, weight="medium")
        canvas.text(54, 651, "个股涨跌分布暂缺", 18, MUTED, max_width=330)

    sectors = brief.sectors_up[:2] + brief.sectors_down[:2]
    rows = [(row.name, row.pct, row.leader) for row in sectors if isfinite(row.pct)]
    if rows:
        title, source = "板块强弱", brief.sector_source + " · 日期未披露"
    else:
        names = ("沪深300", "上证50", "中证500", "中证1000")
        selected = [quotes[name] for name in names if name in quotes and quotes[name].pct is not None and isfinite(quotes[name].pct)]
        if not selected:
            selected = [quote for quote in brief.indices if quote.pct is not None and isfinite(quote.pct)][:4]
        rows = [(quote.name, quote.pct, "") for quote in selected]
        title, source = "指数强弱对照", f"{brief.trade_date:%m.%d} · 行业数据暂缺"
    canvas.text(430, 318, title, 26, AMBER, "bold")
    canvas.text(430, 357, source, 18, MUTED, max_width=584)
    scale = max((abs(value) for _, value, _ in rows), default=0)
    for i, (name, value, leader) in enumerate(rows[:4]):
        y = 406 + i * 64
        canvas.text(430, y, name, 23, TEXT, "medium", max_width=224)
        canvas.change_bar(678, y + 7, 218, 12, value, scale)
        canvas.text(1014, y, fmt_pct(value), 25, change_color(value), "medium", "right", max_width=104)
        if leader:
            canvas.text(430, y + 29, f"领涨股 {leader}", 18, MUTED, max_width=584)
    if rows:
        canvas.text(787, 660, "0%", 18, MUTED, align="center")
    else:
        canvas.text(430, 412, "板块与指数对照暂缺", 22, MUTED, max_width=584)


def _price_table(canvas: Canvas, brief: Brief) -> None:
    rows = [quote for quote in brief.overseas if isfinite(quote.last) and quote.last > 0][:4]
    overseas = bool(rows)
    if not rows:
        rows = [quote for quote in brief.indices if isfinite(quote.last) and quote.last > 0][:4]
    canvas.card(32, 708, 1004, 320, radius=14)
    canvas.text(54, 728, "外盘价格对照" if overseas else "A股价格对照", 26, AMBER, "bold")
    canvas.text(54, 766, "资金与行业数据暂缺 · 仅列已有行情", 18, MUTED, max_width=960)
    for x, label in ((54, "指数"), (360, "前收"), (532, "收盘 / 现价"), (706, "变化点数"), (744, "时点 / 来源")):
        canvas.text(x, 803, label, 18, MUTED, align="right" if x in (360, 532, 706) else "left")
    pitch = 60 if len(rows) <= 3 else 48
    for i, quote in enumerate(rows):
        y = 835 + i * pitch
        canvas.text(54, y, quote.name, 24, TEXT, max_width=152)
        previous = quote.prev_close
        if previous is not None and (not isfinite(previous) or previous <= 0):
            previous = None
        canvas.text(360, y, fmt_px(previous, 2), 24, MUTED, align="right", max_width=148)
        canvas.text(532, y, fmt_px(quote.last, 2), 24, TEXT, "medium", "right", max_width=160)
        delta = quote.change
        if delta is None and previous is not None:
            delta = quote.last - previous
        canvas.text(706, y, f"{delta:+,.2f}" if delta is not None and isfinite(delta) else "—", 24,
                    change_color(delta), "medium", "right", max_width=154)
        canvas.text(744, y, _stamp(quote, overseas, brief.is_intraday), 18, MUTED, max_width=270)
        if quote.source:
            canvas.text(744, y + 25, quote.source, 16, MUTED, max_width=270)
    if not rows:
        canvas.text(54, 835, "补充行情暂缺", 22, MUTED, max_width=960)


def _capital(canvas: Canvas, brief: Brief) -> None:
    has_capital = any(isfinite(value) for item in brief.capital
                      for value in (item.main, item.super_order, item.large, item.mid, item.small))
    has_flows = any(isfinite(flow.net) for flow in brief.sector_in + brief.sector_out)
    cross = brief.cross
    has_cross = cross and (
        cross.south_net is not None and isfinite(cross.south_net)
        or cross.north_turnover is not None and isfinite(cross.north_turnover) and cross.north_turnover >= 0
    )
    if not (has_capital or has_flows or has_cross):
        _price_table(canvas, brief)
        return
    canvas.card(32, 708, 1004, 320, radius=14)
    canvas.text(54, 728, "资金结构", 26, AMBER, "bold")
    source = "腾讯" if any("主力资金改用腾讯" in note for note in brief.notes) else "东财"
    canvas.text(54, 765, f"主力资金 · {source}" if has_capital else "主力金额未返回", 18, MUTED, max_width=452)
    canvas.line(528, 730, 528, 1006)
    label, net = _funds(brief)
    if net is not None and isfinite(net):
        canvas.pair(54, 801, 452, label, fmt_yi(net, signed=True), 22, 28,
                    change_color(net), label_color=MUTED, weight="medium")
    else:
        canvas.text(54, 801, "主力资金数据暂缺", 22, MUTED, max_width=452)
    orders = []
    if brief.capital and (len(brief.capital) == 1 or brief.main_net is not None):
        for label, field in (("超大单", "super_order"), ("大单", "large"), ("中单", "mid"), ("小单", "small")):
            values = [getattr(item, field) for item in brief.capital]
            if all(isfinite(value) for value in values):
                orders.append((label, sum(values)))
    scale = max((abs(value) for _, value in orders), default=0)
    for i, (label, value) in enumerate(orders):
        y = 838 + i * 38
        canvas.text(54, y, label, 22, MUTED)
        canvas.change_bar(174, y + 7, 176, 12, value, scale)
        canvas.text(506, y, fmt_yi(value, signed=True), 25, change_color(value), "medium", "right", max_width=142)

    flows = [flow for flow in brief.sector_in[:2] + brief.sector_out[:2] if isfinite(flow.net)]
    canvas.text(552, 728, "行业主力" if flows else "市场与跨境", 26, AMBER, "bold")
    canvas.text(552, 765, brief.flow_source + " · 日期未披露" if flows else f"{brief.trade_date:%m.%d} 资金参考", 18, MUTED, max_width=462)
    if flows:
        scale = max(abs(flow.net) for flow in flows)
        for i, flow in enumerate(flows):
            y = 815 + i * 43
            canvas.text(552, y, flow.name, 22, TEXT, max_width=176)
            canvas.change_bar(730, y + 6, 156, 12, flow.net, scale)
            canvas.text(1014, y, fmt_yi(flow.net, signed=True), 24, change_color(flow.net), "medium", "right", max_width=120)
        if cross and cross.south_net is not None and isfinite(cross.south_net):
            canvas.pair(552, 993, 462, "南向净买入", fmt_yi(cross.south_net, unit="亿港元", signed=True),
                        19, 21, change_color(cross.south_net), label_color=MUTED)
        if cross and cross.north_turnover is not None and isfinite(cross.north_turnover) and cross.north_turnover >= 0:
            canvas.pair(54, 993, 452, "北向成交额", fmt_yi(cross.north_turnover), 19, 21,
                        TEXT, label_color=MUTED)
    else:
        rows = [(f"{item.market}主力", fmt_yi(item.main, signed=True), change_color(item.main))
                for item in brief.capital if isfinite(item.main)]
        cross = brief.cross
        if cross and cross.south_net is not None and isfinite(cross.south_net):
            rows.append(("南向净买入", fmt_yi(cross.south_net, unit="亿港元", signed=True), change_color(cross.south_net)))
        if cross and cross.north_turnover is not None and isfinite(cross.north_turnover) and cross.north_turnover >= 0:
            rows.append(("北向成交额", fmt_yi(cross.north_turnover), TEXT))
        for i, (label, value, color) in enumerate(rows[:4]):
            canvas.pair(552, 815 + i * 43, 462, label, value, 22, 26, color, label_color=MUTED, weight="medium")
        if not rows:
            canvas.text(552, 815, "行业与跨境资金数据暂缺", 22, MUTED, max_width=462)


def _focus(canvas: Canvas, brief: Brief) -> bool:
    items = focus_items(brief)[:2]
    compact = not items[0].stamp
    if compact:
        canvas.card(1052, 298, 536, 76, radius=14)
        canvas.text(1074, 319, items[0].label, 22, AMBER, "medium", max_width=108)
        canvas.text(1200, 320, items[0].title, 20, MUTED, max_width=366)
        items = items[1:]
    for i, item in enumerate(items):
        y = 390 if compact else 298 + i * 238
        canvas.card(1052, y, 536, 222, radius=14)
        canvas.text(1074, y + 18, item.label, 24, AMBER, "medium", max_width=492)
        bottom = canvas.paragraph(1074, y + 55, item.title, 492, 26, 3, weight="medium", pitch=32)
        if item.context:
            bottom = canvas.paragraph(1074, bottom + 10, item.context, 492, 22, 1, MUTED, pitch=27)
        if item.stamp:
            canvas.text(1074, bottom + 10, item.stamp, 18, MUTED, max_width=492)
    return compact


def _references(canvas: Canvas, brief: Brief, quotes: dict[str, Quote], compact: bool) -> None:
    top = 628 if compact else 774
    canvas.card(1052, top, 536, 1028 - top, radius=14)
    morning = brief.kind == "morning"
    primary = ("上证指数", "深证成指", "创业板指", "科创50")
    domestic = [quotes[name] for name in primary if name in quotes]
    rows, ranges, foreign = [], False, False
    if morning:
        title = "A股指数参考"
        rows = domestic
    else:
        rows = [quote for quote in brief.overseas if isfinite(quote.last) and quote.last > 0][:4]
        if rows:
            title, foreign = "外围参考", True
            if brief.fx:
                fx = next((quote for quote in brief.fx if isfinite(quote.last) and quote.last > 0), None)
                if fx:
                    rows = rows[:3] + [fx]
        else:
            rows = [quote for quote in domestic if quote.high is not None and quote.low is not None
                    and isfinite(quote.high) and isfinite(quote.low) and quote.high >= quote.low > 0]
            if rows:
                title, ranges = "指数日内区间", True
            else:
                title = "更多指数参考"
                rows = [quotes[name] for name in ("沪深300", "上证50", "中证500", "中证1000") if name in quotes]
    canvas.text(1074, top + 20, title, 26, AMBER, "bold", max_width=492)
    if foreign:
        for i, quote in enumerate(rows):
            y = top + 63 + i * (68 if compact else 46)
            canvas.text(1074, y, quote.name, 22, TEXT, max_width=150)
            canvas.text(1390, y, fmt_px(quote.last), 23, TEXT, align="right", max_width=148)
            canvas.text(1566, y, fmt_pct(quote.pct), 23, change_color(quote.pct), align="right", max_width=160)
            stamp = _stamp(quote, True, False)
            if not quote.trade_day and not any(mark in quote.session for mark in ("-", ".")):
                stamp = "日期未披露 · " + stamp
            if quote.source:
                stamp += " · " + quote.source
            canvas.text(1074, y + 25, stamp, 18, MUTED, max_width=492)
        return
    reference = _reference(brief) if morning else f"{brief.trade_date:%m.%d} A股{'盘中' if brief.is_intraday else '收盘'}"
    sources = list(dict.fromkeys(quote.source for quote in rows if quote.source))
    if sources:
        reference += " · " + "/".join(sources)
    canvas.text(1074, top + 59, reference, 18, MUTED, max_width=492)
    canvas.text(1074, top + 89, "指数", 18, MUTED)
    canvas.text(1390, top + 89, "最高" if ranges else "现价 / 收盘", 18, MUTED, align="right")
    canvas.text(1566, top + 89, "最低" if ranges else "涨跌幅", 18, MUTED, align="right")
    for i, quote in enumerate(rows[:4]):
        y = top + 119 + i * (62 if compact else 34)
        canvas.text(1074, y, quote.name, 22, TEXT, max_width=150)
        canvas.text(1390, y, fmt_px(quote.high if ranges else quote.last), 23, TEXT, align="right", max_width=148)
        value = fmt_px(quote.low) if ranges else fmt_pct(quote.pct)
        canvas.text(1566, y, value, 23, TEXT if ranges else change_color(quote.pct), align="right", max_width=160)
        if compact:
            details = []
            if quote.prev_close is not None and isfinite(quote.prev_close) and quote.prev_close > 0:
                details.append(f"前收 {fmt_px(quote.prev_close, 2)}")
            if quote.amount is not None and isfinite(quote.amount) and quote.amount >= 0:
                details.append(f"成交额 {fmt_amount(quote.amount)}")
            if details:
                canvas.text(1074, y + 31, " · ".join(details), 18, MUTED, max_width=492)
    if not rows:
        canvas.text(1074, top + 119, "补充行情暂缺", 22, MUTED, max_width=492)


def render_png(brief: Brief, path: Path, *, watermark: str = DEFAULT_WATERMARK) -> Path:
    canvas = Canvas(watermark)
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    quotes = _quotes(brief)
    _header(canvas, brief, quotes)
    _market(canvas, brief, quotes)
    _capital(canvas, brief)
    compact = _focus(canvas, brief)
    _references(canvas, brief, quotes, compact)
    status = "历史行情回放 · 缺失项目与采集时间见文案" if any(note.startswith("历史") for note in brief.notes) else "行业日期未披露 · 精简摘要及数据说明见文案"
    canvas.text(32, 1048, status, 18, MUTED, max_width=800)
    canvas.text(1588, 1048, "公开行情可能延迟 · 不构成投资建议", 18, MUTED, align="right", max_width=600)
    path = Path(path)
    canvas.save(path)
    return path
