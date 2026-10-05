from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.editorial import build_focus
from common.format import fmt_pct, fmt_px, weekday_cn
from common.render import (
    AMBER, GREEN, MUTED, RED, TEXT, WIDTH, Canvas, change_color,
)

from .calendar import previous_trading_day
from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, Brief, Quote, new_york_time,
)
from .narrative import (
    afterhours_quotes as _afterhours, available_quotes as _available,
    current_quotes as _current, focus_items, sector_leaders, selected_stocks as _selected_stocks,
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
    return sorted(MACRO_NAMES, key=lambda symbol: (symbol in available, any(term in story for term in terms[symbol])),
                  reverse=True)[:2]


def _header(canvas: Canvas, brief: Brief) -> None:
    day = brief.edition_date
    now = new_york_time(brief.generated_at)
    canvas.text(32, 24, "INFOUPDATES", 28, AMBER, "bold")
    clock_label = "历史参考" if any(note.startswith("历史") for note in brief.notes) else "生成"
    canvas.text(1588, 27, clock_label + now.strftime("%m.%d %H:%M %Z"), 22, MUTED, align="right")
    canvas.text(32, 68, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · {brief.title}",
                34, TEXT, "bold", max_width=1556)
    canvas.text(32, 120, brief.headline, 40, AMBER, "bold", max_width=1300, min_size=32)
    canvas.text(1588, 133, f"情绪{brief.sentiment}", 24, MUTED, align="right", max_width=230, min_size=22)
    canvas.text(32, 182, brief.market_summary, 26, TEXT, "medium", max_width=1556, min_size=22)
    canvas.line(32, 230, 1588, color=AMBER, width=2)


def _primary(canvas: Canvas, brief: Brief) -> None:
    names = FUTURE_NAMES if brief.kind == "premarket" else INDEX_NAMES
    quotes = _available(brief.futures if brief.kind == "premarket" else brief.indices)
    col = (1556 - 16 * (len(names) - 1)) / len(names)
    for i, (symbol, name) in enumerate(names.items()):
        quote = quotes.get(symbol)
        x = 32 + i * (col + 16)
        canvas.card(x, 248, col, 128)
        canvas.text(x + 20, 266, name, 26, MUTED, "medium", max_width=col - 40)
        canvas.pair(x + 20, 309, col - 40, _price(quote), fmt_pct(quote.pct if quote else None),
                    34, value_size=30, color=change_color(quote.pct if quote else None), weight="bold", gap=14)
        canvas.text(x + 20, 353, _row_stamp(quote, brief), 18, MUTED, max_width=col - 40)

    canvas.card(32, 392, 1556, 56)
    references = _available(brief.references)
    for i, symbol in enumerate(_macro_symbols(brief)):
        x, quote = 54 + i * 786, references.get(symbol)
        label = "WTI原油" if symbol == "CL=F" else MACRO_NAMES[symbol]
        canvas.pair(x, 399, 726, label, _price(quote), 24,
                    color=TEXT, label_color=MUTED)
        canvas.text(x + 726, 430, _stamp(quote, brief, compact=True), 16, MUTED,
                    align="right", max_width=726)
        if i:
            canvas.line(810, 404, 810, 438)


def _stocks(canvas: Canvas, brief: Brief) -> None:
    quotes = _current(brief.stocks, brief)
    extended = _afterhours(brief)
    selected = _selected_stocks(brief)
    canvas.card(32, 464, 770, 296)
    canvas.text(54, 482, "重点个股", 26, AMBER, "bold")
    label = brief.stocks_label or "行情待确认"
    if brief.kind == "premarket":
        label += f" · {sum(symbol in quotes for symbol in MEGA_NAMES)} / {len(MEGA_NAMES)}家可用"
    canvas.text(780, 491, label, 18, MUTED, align="right", max_width=480, min_size=17)
    if not selected:
        canvas.text(54, 573, "最新个股报价待确认", 28, MUTED)
        canvas.text(54, 618, "等待有效行情后再筛选重点公司", 23, MUTED)
        return
    for i, quote in enumerate(selected):
        y = 524 + i * 76
        canvas.pair(54, y, 726, f"{quote.symbol} {quote.name}", fmt_pct(quote.pct), 26,
                    value_size=28, color=change_color(quote.pct), weight="medium")
        after = extended.get(quote.symbol)
        if after:
            canvas.text(54, y + 30, f"{_price(quote)} · {_stamp(quote, brief)}", 18, MUTED, max_width=726)
            canvas.text(54, y + 51, f"盘后{_price(after)} {fmt_pct(after.pct)} · {_stamp(after, brief)}",
                        18, MUTED, max_width=726)
        else:
            canvas.text(54, y + 33, f"{_price(quote)} · {_stamp(quote, brief)}", 18, MUTED, max_width=726)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    strongest, weakest = sector_leaders(brief)
    canvas.card(818, 464, 770, 296)
    canvas.text(840, 482, "板块ETF强弱", 26, AMBER, "bold")
    canvas.text(1566, 491, "相对表现 · 各两项", 18, MUTED, align="right", max_width=380)
    if brief.kind == "premarket":
        base = f"{brief.reference_date:%m.%d}基准" if brief.reference_date else "基准待确认"
        canvas.text(840, 526, f"最新板块ETF · 相对{base} · 各项实际时点", 20, MUTED, max_width=726)
    else:
        canvas.text(840, 526, _activity(brief), 20, MUTED, max_width=726, min_size=18)
    canvas.line(1203, 559, 1203, 741)
    for x, label, rows, color in ((840, "相对较强", strongest, RED), (1226, "相对较弱", weakest, GREEN)):
        canvas.text(x, 558, label, 22, color, "medium")
        for i in range(2):
            quote = rows[i] if i < len(rows) else None
            y = 594 + i * 84
            canvas.pair(x, y, 340, f"{quote.name} {quote.symbol}" if quote else "待确认",
                        fmt_pct(quote.pct if quote else None), 24,
                        color=change_color(quote.pct if quote else None), weight="regular")
            canvas.paragraph(x, y + 31, _row_stamp(quote, brief), 340, 18, 2, MUTED, pitch=21)


def _focus(canvas: Canvas, brief: Brief) -> None:
    items = focus_items(brief)
    for i, item in enumerate(items[:2]):
        x = 32 + i * 786
        canvas.card(x, 784, 770, 244)
        canvas.text(x + 22, 804, item.label, 24, AMBER, "medium", max_width=726)
        canvas.paragraph(x + 22, 843, item.title, 726, 29, 2, TEXT, "medium", pitch=38)
        canvas.paragraph(x + 22, 929, item.context, 726, 24, 2, MUTED, pitch=28)
        canvas.text(x + 748, 1004, item.stamp, 18, MUTED, align="right", max_width=726)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief)
    _primary(canvas, brief)
    _stocks(canvas, brief)
    _sectors(canvas, brief)
    _focus(canvas, brief)
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
