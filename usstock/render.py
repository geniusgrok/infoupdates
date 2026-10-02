from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.editorial import build_focus
from common.format import fmt_pct, fmt_px, weekday_cn
from common.render import (
    AMBER, BG, GREEN, HEIGHT, MUTED, RED, TEXT, WIDTH, Canvas, change_color, font, text_width,
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
    canvas.text(28, 24, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(1052, 27, now.strftime("生成%m.%d %H:%M %Z"), 24, MUTED, align="right")
    canvas.text(28, 75, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · {brief.title}",
                51, TEXT, "bold", max_width=1024, min_size=44)
    canvas.text(28, 143, brief.headline, 44, AMBER, "bold", max_width=780, min_size=34)
    canvas.text(1052, 155, f"情绪{brief.sentiment}", 29, MUTED, align="right", max_width=210, min_size=23)
    canvas.card(28, 199, 1024, 54, radius=12)
    canvas.text(50, 213, brief.market_summary, 29, TEXT, "medium", max_width=980, min_size=22)


def _primary(canvas: Canvas, brief: Brief) -> None:
    names = FUTURE_NAMES if brief.kind == "premarket" else INDEX_NAMES
    quotes = _available(brief.futures if brief.kind == "premarket" else brief.indices)
    col = (1024 - 12 * (len(names) - 1)) / len(names)
    for i, (symbol, name) in enumerate(names.items()):
        quote = quotes.get(symbol)
        x = 28 + i * (col + 12)
        canvas.card(x, 270, col, 148)
        canvas.text(x + col / 2, 285, name, 30, MUTED, "medium", "center", max_width=col - 24)
        canvas.text(x + col / 2, 320, _row_stamp(quote, brief), 18, MUTED,
                    align="center", max_width=col - 16, min_size=17)
        canvas.text(x + col / 2, 341, fmt_pct(quote.pct if quote else None), 42,
                    change_color(quote.pct if quote else None), "bold", "center", max_width=col - 24, min_size=30)
        canvas.text(x + col / 2, 385, _price(quote), 25, TEXT, align="center", max_width=col - 24, min_size=22)

    canvas.card(28, 432, 1024, 78)
    references = _available(brief.references)
    for i, symbol in enumerate(_macro_symbols(brief)):
        x, quote = 50 + i * 512, references.get(symbol)
        label = "WTI原油" if symbol == "CL=F" else MACRO_NAMES[symbol]
        canvas.pair(x, 445, 468, label, _price(quote), 26,
                    color=TEXT, label_color=MUTED)
        canvas.text(x + 468, 479, _stamp(quote, brief, compact=True), 19, MUTED,
                    align="right", max_width=468)
        if i:
            canvas.line(540, 447, 540, 493)


def _stocks(canvas: Canvas, brief: Brief) -> None:
    quotes = _current(brief.stocks, brief)
    extended = _afterhours(brief)
    selected = _selected_stocks(brief)
    canvas.card(28, 528, 1024, 306)
    canvas.heading(50, 548, "重点个股")
    label = brief.stocks_label or "行情待确认"
    if brief.kind == "premarket":
        label += f" · {sum(symbol in quotes for symbol in MEGA_NAMES)} / {len(MEGA_NAMES)}家可用"
    canvas.text(1030, 558, label, 23, MUTED, align="right", max_width=580, min_size=20)
    if not selected:
        canvas.text(50, 620, "最新个股报价待确认", 31, MUTED)
        canvas.text(50, 670, "等待有效行情后再筛选重点公司", 25, MUTED)
        return
    for i, quote in enumerate(selected):
        y = 607 + i * 73
        canvas.pair(50, y, 980, f"{quote.symbol} {quote.name}", fmt_pct(quote.pct), 32,
                    value_size=35, color=change_color(quote.pct), weight="medium")
        after = extended.get(quote.symbol)
        if after:
            canvas.pair(50, y + 41, 980, f"{_price(quote)} · {_stamp(quote, brief)}",
                        f"盘后{_price(after)} {fmt_pct(after.pct)} · {_stamp(after, brief)}", 20,
                        color=MUTED, label_color=MUTED, gap=18)
        else:
            canvas.pair(50, y + 41, 980, _price(quote), _stamp(quote, brief), 23,
                        color=MUTED, label_color=TEXT, gap=18)
        if i < len(selected) - 1:
            canvas.line(50, y + 69, 1030)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    strongest, weakest = sector_leaders(brief)
    canvas.card(28, 852, 1024, 258)
    canvas.heading(50, 871, "板块ETF强弱")
    canvas.text(1030, 881, "相对表现 · 各两项", 23, MUTED, align="right", max_width=380)
    if brief.kind == "premarket":
        base = f"{brief.reference_date:%m.%d}基准" if brief.reference_date else "基准待确认"
        canvas.text(50, 917, f"最新板块ETF · 相对{base} · 各项实际时点", 22, MUTED, max_width=980, min_size=19)
    else:
        canvas.text(50, 917, _activity(brief), 22, MUTED, max_width=980, min_size=19)
    canvas.line(540, 952, 540, 1091)
    for x, label, rows, color in ((50, "相对较强", strongest, RED), (574, "相对较弱", weakest, GREEN)):
        canvas.text(x, 949, label, 24, color, "medium")
        for i in range(2):
            quote = rows[i] if i < len(rows) else None
            y = 986 + i * 56
            canvas.pair(x, y, 454, f"{quote.name} {quote.symbol}" if quote else "待确认",
                        fmt_pct(quote.pct if quote else None), 28,
                        color=change_color(quote.pct if quote else None), weight="regular")
            canvas.text(x + 454, y + 33, _row_stamp(quote, brief), 18, MUTED, align="right", max_width=454)


def _focus(canvas: Canvas, brief: Brief) -> None:
    items = focus_items(brief)
    canvas.card(28, 1128, 1024, 428)
    canvas.heading(50, 1150, "最重要的两件事")
    for i, item in enumerate(items[:2]):
        y = 1210 + i * 173
        canvas.text(50, y + 4, item.label, 23, AMBER, "medium", max_width=130)
        canvas.paragraph(204, y, item.title, 826, 32, 2, TEXT, "medium", pitch=38)
        if text_width(item.title, font(32, "medium")) <= 826:
            canvas.text(204, y + 44, item.context, 26, MUTED, max_width=826)
        canvas.text(1030, y + 114, item.stamp, 20, MUTED, align="right", max_width=826)
        if i == 0 and len(items) > 1:
            canvas.line(50, y + 153, 1030)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief)
    _primary(canvas, brief)
    _stocks(canvas, brief)
    _sectors(canvas, brief)
    _focus(canvas, brief)
    status = "数据限制见文案" if brief.notes else "公开行情可能延迟"
    quotes = brief.indices + brief.futures + brief.stocks + brief.sectors + brief.references + brief.extended_stocks
    providers = sorted({quote.source.split("（", 1)[0] for quote in quotes} | {item.source for item in brief.news})
    source = " / ".join(providers) if providers else "公开行情暂缺"
    canvas.text(28, 1585, f"{source} · {status}", 18, MUTED, max_width=650)
    canvas.text(1052, 1585, "美东时间 · 红涨绿跌 · 不构成投资建议", 18, MUTED, align="right", max_width=410)
    canvas.save(path)
    return Path(path)
