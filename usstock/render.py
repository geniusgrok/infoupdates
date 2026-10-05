from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.editorial import build_focus
from common.format import fmt_pct, fmt_px, weekday_cn
from common.render import AMBER, DEFAULT_WATERMARK, MUTED, TEXT, WIDTH, Canvas, change_color

from .calendar import previous_trading_day
from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, SECTOR_NAMES, Brief, Quote, new_york_time,
)
from .narrative import (
    afterhours_quotes as _afterhours, available_quotes as _available,
    current_quotes as _current, focus_items, selected_stocks as _selected_stocks,
)


def _stamp(quote: Quote | None, brief: Brief, *, compact: bool = False) -> str:
    if quote is None:
        return "时点待确认"
    status = " · 缓存" if quote.cached else ""
    if quote.delay_minutes:
        status += f" · 延迟{quote.delay_minutes}分"
    if quote.is_snapshot:
        return f"夜盘快照 · 采集{new_york_time(quote.observed_at):%H:%M} · 成交时间未披露{status}"
    if quote.asof is None:
        return "时点待确认"
    if compact:
        parts = [new_york_time(quote.asof).strftime("%m.%d %H:%M")]
        if quote.delay_minutes:
            parts.append(f"延{quote.delay_minutes}分")
        if quote.cached:
            parts.append("缓存")
        return " ".join(parts)
    phase = {"premarket": "盘前", "postmarket": "盘后", "overnight": "夜盘", "reference": "参考", "futures": "期货"}.get(quote.session)
    if phase is None:
        phase = "前收" if quote.trade_date != brief.edition_date else "常规"
        if brief.kind == "postmarket" and brief.complete and quote.trade_date == brief.edition_date:
            phase = "收盘"
    return f"{phase}{new_york_time(quote.asof):%m.%d %H:%M}{status}"


def _row_stamp(quote: Quote | None, brief: Brief) -> str:
    return (_stamp(quote, brief).replace(" · 缓存", " 缓存")
            .replace(" · 延迟", " 延").replace("夜盘快照 ·", "夜盘快照"))


def _table_stamp(quote: Quote, brief: Brief) -> str:
    if quote.is_snapshot:
        stamp = f"快照{new_york_time(quote.observed_at):%m.%d %H:%M}*"
        if quote.delay_minutes:
            stamp += f" 延{quote.delay_minutes}分"
        return stamp + (" 缓存" if quote.cached else "")
    return _row_stamp(quote, brief)


def _price(quote: Quote | None) -> str:
    if quote is None:
        return "—"
    value = fmt_px(quote.last, 2)
    if quote.unit == "%":
        return value + "%"
    if quote.unit == "USD" or quote.unit.startswith("USD/"):
        return "$" + value + quote.unit.removeprefix("USD")
    return value


def _volume(value: float | None) -> str:
    if value is None or not isfinite(value) or value < 0:
        return "—"
    if value >= 1e8:
        return f"{value / 1e8:.2f}亿股"
    if value >= 1e4:
        return f"{value / 1e4:.0f}万股"
    return f"{value:.0f}股"


def _activity(brief: Brief) -> str:
    quote = brief.activity
    if quote is None or quote.volume is None or not isfinite(quote.volume) or quote.volume < 0:
        return "SPY日线股数待确认；供应商披露口径。"
    day = f"{quote.trade_date:%m.%d} " if quote.trade_date is not None else ""
    caption = f"{day}SPY日线股数 {_volume(quote.volume)}"
    if (quote.previous_volume is not None and isfinite(quote.previous_volume) and quote.previous_volume >= 0
            and quote.previous_date is not None and quote.trade_date is not None
            and quote.previous_date == previous_trading_day(quote.trade_date)):
        delta = quote.volume - quote.previous_volume
        direction = "增加" if delta > 0 else "减少" if delta < 0 else "持平"
        caption += f" · 较{quote.previous_date:%m.%d}{direction}"
        if delta:
            caption += _volume(abs(delta))
    return caption + f" · {quote.source}"


def _macro_symbols(brief: Brief) -> list[str]:
    focus = build_focus(brief.news, brief.generated_at, market="usstock")
    story = " ".join([brief.headline, focus[0].title]).lower()
    terms = {"^VIX": ("vix", "恐慌"), "^TNX": ("美债", "利率", "通胀", "美联储"),
             "DX-Y.NYB": ("美元指数", "美元汇率", "美元走强", "美元走弱", "美元升值", "美元贬值", "dollar index"),
             "GC=F": ("黄金", "gold"), "CL=F": ("原油", "油价", "oil")}
    available = _available(brief.references)
    symbols = [symbol for symbol in MACRO_NAMES if symbol in available]
    return sorted(symbols, key=lambda symbol: any(term in story for term in terms[symbol]), reverse=True)[:4]


def _header(canvas: Canvas, brief: Brief) -> None:
    day = brief.edition_date
    now = new_york_time(brief.generated_at)
    session = "美股盘前" if brief.kind == "premarket" else "美股收盘"
    title = f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')}·{session} {brief.headline}"
    canvas.text(32, 24, "INFOUPDATES", 26, AMBER, "bold")
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    canvas.text(1588, 29, clock_label + now.strftime("%m.%d %H:%M %Z"), 20, MUTED,
                align="right", max_width=370)
    canvas.text(32, 70, title, 36, AMBER, "bold", max_width=1556, min_size=24)
    canvas.text(32, 122, brief.market_summary, 26, TEXT, "medium", max_width=1556, min_size=22)
    canvas.line(32, 156, 1588, color=AMBER, width=2)


def _primary(canvas: Canvas, brief: Brief) -> None:
    names = FUTURE_NAMES if brief.kind == "premarket" else INDEX_NAMES
    quotes = _available(brief.futures if brief.kind == "premarket" else brief.indices)
    col = (1556 - 16 * (len(names) - 1)) / len(names)
    for i, (symbol, name) in enumerate(names.items()):
        quote = quotes.get(symbol)
        x = 32 + i * (col + 16)
        canvas.card(x, 174, col, 108, radius=14)
        canvas.text(x + 20, 188, name, 24, MUTED, "medium", max_width=col - 40)
        canvas.pair(x + 20, 219, col - 40, _price(quote), fmt_pct(quote.pct if quote else None),
                    32, value_size=29, color=change_color(quote.pct if quote else None), weight="bold", gap=14)
        canvas.text(x + 20, 256, _row_stamp(quote, brief), 17, MUTED, max_width=col - 40)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    quotes = _current(brief.sectors, brief)
    rows = [quotes[symbol] for symbol in SECTOR_NAMES if symbol in quotes]
    sector_data = bool(rows)
    title = "板块 ETF 涨跌对比"
    coverage = f"{len(rows)} / {len(SECTOR_NAMES)} 个板块样本"
    if not rows:
        names = FUTURE_NAMES if brief.kind == "premarket" else INDEX_NAMES
        quotes = _available(brief.futures if brief.kind == "premarket" else brief.indices)
        rows = [quotes[symbol] for symbol in names if symbol in quotes]
        title = "股指期货对比" if brief.kind == "premarket" else "主要指数对比"
        coverage = f"板块 ETF 暂缺 · {len(rows)} 个{'期货' if brief.kind == 'premarket' else '指数'}参考"
    canvas.card(32, 298, 1004, 394, radius=14)
    canvas.text(54, 314, title, 27, AMBER, "bold", max_width=500)
    canvas.text(1014, 320, coverage, 20, MUTED, align="right", max_width=480)
    if not rows:
        canvas.text(54, 399, "暂无有效的横截面对比行情", 28, MUTED)
        canvas.text(54, 442, "等待有效数据，不用前收价格补位盘前。", 22, MUTED, max_width=960)
        return
    scale = max((abs(quote.pct) for quote in rows if quote.pct is not None and isfinite(quote.pct)), default=1)
    scale = max(scale, 0.01)
    bar_x, bar_width = 300, 348
    canvas.text(54, 348, "板块 / 品种", 18, MUTED)
    for x, value, align in ((bar_x, f"-{scale:.2f}%", "left"),
                            (bar_x + bar_width / 2, "0%", "center"),
                            (bar_x + bar_width, f"+{scale:.2f}%", "right")):
        canvas.text(x, 348, value, 17, MUTED, align=align)
    canvas.text(758, 348, "涨跌幅", 18, MUTED, align="right")
    canvas.text(794, 348, "实际时点 · 美东", 18, MUTED, max_width=220)
    pitch = 270 / max(len(rows) - 1, 1)
    start = 507 if len(rows) == 1 else 372
    for i, quote in enumerate(rows):
        y = start + i * pitch
        canvas.text(54, y, f"{quote.name} {quote.symbol}", 22, TEXT, max_width=228, min_size=20)
        if quote.pct is not None and isfinite(quote.pct):
            canvas.change_bar(bar_x, y + 8, bar_width, 9, quote.pct, scale)
        canvas.text(758, y, fmt_pct(quote.pct), 22, change_color(quote.pct), align="right", max_width=95)
        canvas.text(794, y + 3, _table_stamp(quote, brief), 17, MUTED, max_width=220, min_size=15)
    snapshot = any(quote.is_snapshot for quote in rows)
    if snapshot:
        note = "* 快照为原始采集时点，成交时间未披露；板块样本不代表全市场个股广度。"
    elif not sector_data:
        note = "板块 ETF 暂缺，本图仅对比实际可用股指期货。" if brief.kind == "premarket" else "本图仅对比主要指数。 " + _activity(brief)
    else:
        note = _activity(brief) if brief.kind == "postmarket" else "板块 ETF 仅代表对应板块，各项保留实际报价时点。"
    canvas.text(54, 668, note, 15, MUTED, max_width=960)


def _stocks(canvas: Canvas, brief: Brief) -> None:
    quotes = _current(brief.stocks, brief)
    rows = [quotes[symbol] for symbol in MEGA_NAMES if symbol in quotes]
    extended = _afterhours(brief)
    priority = {quote.symbol for quote in _selected_stocks(brief)}
    canvas.card(32, 708, 1004, 320, radius=14)
    canvas.text(54, 724, "大型科技股 · MAG7", 27, AMBER, "bold")
    label = brief.stocks_label or "行情待确认"
    canvas.text(1014, 731, f"{label} · {len(rows)} / {len(MEGA_NAMES)}家可用", 18, MUTED,
                align="right", max_width=590)
    if not rows:
        canvas.text(54, 810, "最新个股报价待确认", 28, MUTED)
        canvas.text(54, 854, "等待有效行情后再展示重点公司", 22, MUTED)
        return
    for x, label, align in ((54, "股票", "left"), (374, "价格", "right"), (478, "涨跌幅", "right"),
                            (513, "实际时点 · 美东", "left"),
                            (725, f"盘后延长交易 · {brief.edition_date:%m.%d}" if extended else "来源", "left")):
        canvas.text(x, 762, label, 17, MUTED, align=align, max_width=290)
    for i, quote in enumerate(rows):
        y = 790 + i * 31
        weight = "bold" if quote.symbol in priority else "regular"
        canvas.text(54, y, f"{quote.symbol} {quote.name}", 22, TEXT, weight, max_width=185, min_size=20)
        canvas.text(374, y, _price(quote), 22, TEXT, weight, "right", max_width=120)
        canvas.text(478, y, fmt_pct(quote.pct), 22, change_color(quote.pct), weight, "right", max_width=95)
        canvas.text(513, y + 3, _table_stamp(quote, brief), 17, MUTED, max_width=194, min_size=15)
        after = extended.get(quote.symbol)
        if after:
            canvas.text(725, y + 1, f"{_price(after)} {fmt_pct(after.pct)} {new_york_time(after.asof):%H:%M}",
                        20, change_color(after.pct), max_width=289, min_size=18)
        elif not extended:
            canvas.text(725, y + 3, quote.source, 17, MUTED, max_width=289)
    note = "加粗为消息相关或波动较大的重点公司。"
    if extended:
        note = "盘后涨跌幅相对当日常规收盘；延长交易缺项留空，保留实际报价时间。"
    if any(quote.is_snapshot for quote in rows):
        note += " * 快照为原始采集时点，成交时间未披露。"
    canvas.text(54, 1003, note, 15, MUTED, max_width=960)


def _focus(canvas: Canvas, brief: Brief) -> bool:
    items = focus_items(brief)
    has_news = bool(items[0].stamp)
    if has_news:
        cards = [(298 + i * 238, item) for i, item in enumerate(items[:2])]
    else:
        canvas.card(1052, 298, 536, 84, radius=14)
        canvas.text(1074, 314, items[0].label, 22, AMBER, "medium", max_width=492)
        canvas.text(1074, 347, items[0].title, 22, MUTED, max_width=492)
        cards = [(398, items[1])]
    for y, item in cards:
        canvas.card(1052, y, 536, 222, radius=14)
        canvas.text(1074, y + 16, item.label, 24, AMBER, "medium", max_width=492)
        bottom = canvas.paragraph(1074, y + 53, item.title, 492, 26, 3, TEXT, "medium", pitch=33)
        bottom = canvas.paragraph(1074, bottom + 12, item.context, 492, 22, 1, MUTED, pitch=27)
        if item.stamp:
            canvas.text(1074, bottom + 10, item.stamp, 18, MUTED, max_width=492)
    return has_news


def _macro(canvas: Canvas, brief: Brief, *, expanded: bool = False) -> None:
    references = _available(brief.references)
    symbols = _macro_symbols(brief)
    top, height = (636, 392) if expanded else (774, 254)
    canvas.card(1052, top, 536, height, radius=14)
    canvas.text(1074, top + 16, "宏观参考", 27, AMBER, "bold")
    canvas.text(1566, top + 25, "实际时点 · 美东", 18, MUTED, align="right", max_width=290)
    if not symbols:
        canvas.text(1074, top + 85, "宏观参考报价暂缺", 24, MUTED)
        return
    for i, symbol in enumerate(symbols):
        quote = references[symbol]
        y = top + 58 + i * (78 if expanded else 46)
        label = "WTI原油" if symbol == "CL=F" else MACRO_NAMES[symbol]
        canvas.pair(1074, y, 492, label, _price(quote), 24 if expanded else 22,
                    color=TEXT, label_color=TEXT)
        canvas.text(1074, y + (30 if expanded else 26), _stamp(quote, brief, compact=True),
                    18 if expanded else 17, MUTED, max_width=492)
        if expanded:
            canvas.text(1074, y + 54, quote.source, 17, MUTED, max_width=492)


def render_png(brief: Brief, path: Path, *, watermark: str = DEFAULT_WATERMARK) -> Path:
    canvas = Canvas(watermark)
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief)
    _primary(canvas, brief)
    _sectors(canvas, brief)
    _stocks(canvas, brief)
    has_news = _focus(canvas, brief)
    _macro(canvas, brief, expanded=not has_news)
    status = "数据限制见文案" if brief.notes else "公开行情可能延迟"
    if any(note.startswith("历史") for note in brief.notes):
        status = "历史行情回放，缺项与采集时间见文案"
    quotes = brief.indices + brief.futures + brief.stocks + brief.sectors + brief.references + brief.extended_stocks
    providers = sorted({quote.source.split("（", 1)[0] for quote in quotes} | {item.source for item in brief.news})
    source = " / ".join(providers) if providers else "公开行情暂缺"
    canvas.text(32, 1048, f"{source} · {status}", 18, MUTED, max_width=900)
    canvas.text(1588, 1048, "美东时间 · 红涨绿跌 · 不构成投资建议", 18, MUTED, align="right", max_width=610)
    path = Path(path)
    canvas.save(path)
    return path
