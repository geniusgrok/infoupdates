from __future__ import annotations

from math import isfinite
from pathlib import Path

from common.format import fmt_pct, fmt_px, weekday_cn
from common.render import (
    AMBER, BG, CARD, GREEN, HEIGHT, MUTED, RED, TEXT, WIDTH, Canvas, change_color,
)

from .calendar import previous_trading_day
from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, Brief, Quote, new_york_time,
)


def _available(quotes: list[Quote]) -> dict[str, Quote]:
    return {quote.symbol: quote for quote in quotes if isfinite(quote.last) and quote.last > 0}


def _current(quotes: list[Quote], brief: Brief) -> dict[str, Quote]:
    available = _available(quotes)
    if brief.kind == "premarket":
        return {symbol: quote for symbol, quote in available.items()
                if quote.session in {"overnight", "premarket", "postmarket"} and (quote.asof is not None or quote.is_snapshot)}
    return available


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


def _distribution(quotes: list[Quote]) -> str:
    values = [quote.pct for quote in quotes if quote.pct is not None and isfinite(quote.pct)]
    if not values:
        return "待确认"
    up, down = sum(value > 0 for value in values), sum(value < 0 for value in values)
    flat = len(values) - up - down
    return f"{up}涨 / {down}跌" + (f" / {flat}平" if flat else "")


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
        canvas.text(x + col / 2, 320, _stamp(quote, brief), 17, MUTED, align="center", max_width=col - 24)
        canvas.text(x + col / 2, 341, fmt_pct(quote.pct if quote else None), 42,
                    change_color(quote.pct if quote else None), "bold", "center", max_width=col - 24, min_size=30)
        canvas.text(x + col / 2, 385, _price(quote), 25, TEXT, align="center", max_width=col - 24, min_size=22)

    canvas.card(28, 432, 1024, 78)
    if brief.kind == "premarket":
        stocks, sectors = _current(brief.stocks, brief), _current(brief.sectors, brief)
        count = sum(symbol in stocks for symbol in MEGA_NAMES)
        stats = (("最新科技股", _distribution(list(stocks.values()))),
                 ("最新板块ETF", _distribution(list(sectors.values()))),
                 ("科技股报价", f"{count} / {len(MEGA_NAMES)}家"))
    else:
        sectors = [q for q in brief.sectors if q.pct is not None and isfinite(q.pct)]
        strongest = max(sectors, key=lambda q: q.pct) if sectors else None
        stats = (("四大指数", _distribution(brief.indices)),
                 ("七大科技", _distribution(brief.stocks)),
                 ("ETF相对最强", f"{strongest.name} {fmt_pct(strongest.pct)}" if strongest else "待确认"))
    for i, (label, value) in enumerate(stats):
        center = 28 + (i + .5) * 1024 / 3
        canvas.text(center, 444, label, 26, MUTED, align="center", max_width=315)
        canvas.text(center, 472, value, 29, TEXT, "medium", "center", max_width=315, min_size=23)
        if i:
            canvas.line(28 + i * 1024 / 3, 446, 28 + i * 1024 / 3, 496)


def _stocks(canvas: Canvas, brief: Brief) -> None:
    quotes = _current(brief.stocks, brief)
    extended = _available(getattr(brief, "extended_stocks", [])) if brief.kind == "postmarket" else {}
    canvas.card(28, 528, 1024, 292)
    canvas.heading(50, 548, "核心科技股")
    canvas.text(1030, 558, brief.stocks_label or "行情待确认", 23, MUTED, align="right", max_width=510, min_size=20)
    canvas.line(540, 594, 540, 798)
    for i, (symbol, name) in enumerate(MEGA_NAMES.items()):
        x, y = (50 if i % 2 == 0 else 574), 600 + (i // 2) * 50
        quote = quotes.get(symbol)
        canvas.pair(x, y, 454, f"{symbol} {quote.name if quote else name}",
                    fmt_pct(quote.pct if quote else None), 30, color=change_color(quote.pct if quote else None), weight="medium")
        after = extended.get(symbol)
        detail = f"盘后{_price(after)} {fmt_pct(after.pct)}" if after else _price(quote)
        stamp = new_york_time(after.asof).strftime("%m.%d %H:%M") if after and after.asof else _stamp(quote, brief)
        canvas.pair(x, y + 32, 454, detail, stamp, 17,
                    color=MUTED, label_color=MUTED, gap=10)
        if i // 2 < 3:
            canvas.line(x, y + 47, x + 454)


def _sectors(canvas: Canvas, brief: Brief) -> None:
    values = [q for q in _current(brief.sectors, brief).values() if q.pct is not None and isfinite(q.pct)]
    ordered = sorted(values, key=lambda quote: quote.pct, reverse=True)
    strongest = ordered[:3]
    strong_symbols = {quote.symbol for quote in strongest}
    weakest = [quote for quote in reversed(ordered) if quote.symbol not in strong_symbols][:3]
    canvas.card(28, 838, 1024, 270)
    canvas.heading(50, 857, "板块ETF强弱")
    canvas.text(1030, 867, "相对表现 · 涨跌幅", 23, MUTED, align="right", max_width=380)
    if brief.kind == "premarket":
        base = f"{brief.reference_date:%m.%d}基准" if brief.reference_date else "基准待确认"
        canvas.text(50, 902, f"相对最近常规收盘 · {base} · 各项实际时点", 22, MUTED, max_width=980, min_size=19)
        group_y, row_y, pitch = 926, 960, 46
    else:
        canvas.text(50, 902, _activity(brief), 22, MUTED, max_width=980, min_size=19)
        dates = sorted({q.trade_date for q in values if q.trade_date is not None})
        date_label = f"ETF参考{dates[0]:%m.%d}收盘" if len(dates) == 1 else "ETF时点不同，详见文案" if dates else "ETF收盘数据待确认"
        canvas.text(50, 932, date_label, 19, MUTED, max_width=980)
        group_y, row_y, pitch = 959, 993, 35
    canvas.line(540, group_y - 1, 540, 1088)
    for x, label, rows, color in ((50, "相对较强", strongest, RED), (574, "相对较弱", weakest, GREEN)):
        canvas.text(x, group_y, label, 25, color, "medium")
        for i in range(3):
            quote = rows[i] if i < len(rows) else None
            y = row_y + i * pitch
            canvas.pair(x, y, 454, f"{quote.name} {quote.symbol}" if quote else "待确认",
                        fmt_pct(quote.pct if quote else None), 28,
                        color=change_color(quote.pct if quote else None), weight="regular")
            if brief.kind == "premarket":
                canvas.text(x + 454, y + 30, _stamp(quote, brief), 15, MUTED, align="right", max_width=454)


def _macro_news(canvas: Canvas, brief: Brief) -> None:
    quotes = _available(brief.references)
    canvas.card(28, 1126, 390, 430)
    canvas.card(436, 1126, 616, 430)
    canvas.heading(50, 1147, "宏观参考")
    canvas.heading(458, 1147, "市场要闻")
    for i, (symbol, name) in enumerate(MACRO_NAMES.items()):
        quote = quotes.get(symbol)
        y = 1207 + i * 62
        canvas.pair(50, y, 346, name, fmt_pct(quote.pct if quote else None), 27,
                    color=change_color(quote.pct if quote else None), label_color=MUTED)
        canvas.pair(50, y + 31, 346, _price(quote), _stamp(quote, brief, compact=True), 17, value_size=16,
                    color=MUTED, label_color=TEXT, gap=10)
        if i < 4:
            canvas.line(50, y + 54, 396)
    canvas.text(50, 1528, "各品种实际时点 · 美东时间", 18, MUTED, max_width=346)

    for i in range(3):
        y = 1204 + i * 116
        item = brief.news[i] if i < len(brief.news) else None
        canvas.paragraph(458, y, item.title if item else "暂无可核实要闻", 572, 28, 2,
                         weight="medium", pitch=33)
        if item:
            published = new_york_time(item.published)
            canvas.text(1030, y + 78, f"{published:%m.%d %H:%M %Z} · {item.source}",
                        18, MUTED, align="right", max_width=572)
        if i < 2:
            canvas.line(458, y + 102, 1030)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    _header(canvas, brief)
    _primary(canvas, brief)
    _stocks(canvas, brief)
    _sectors(canvas, brief)
    _macro_news(canvas, brief)
    status = "数据限制见文案" if brief.notes else "公开行情可能延迟"
    quotes = brief.indices + brief.futures + brief.stocks + brief.sectors + brief.references + brief.extended_stocks
    providers = sorted({quote.source.split("（", 1)[0] for quote in quotes} | {item.source for item in brief.news})
    source = " / ".join(providers) if providers else "公开行情暂缺"
    canvas.text(28, 1585, f"{source} · {status}", 18, MUTED, max_width=650)
    canvas.text(1052, 1585, "美东时间 · 红涨绿跌 · 不构成投资建议", 18, MUTED, align="right", max_width=410)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.image.save(path, optimize=True)
    return path
