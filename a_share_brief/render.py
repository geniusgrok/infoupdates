from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from .models import Brief, Quote

WIDTH = 1080
SCALE = 2
PAD = 48

BG = (8, 10, 13)
CARD = (18, 22, 27)
LINE = (46, 54, 64)
HAIR = (34, 40, 48)
AMBER = (232, 176, 74)
TEXT = (242, 244, 246)
MUTED = (164, 172, 182)
DIM = (112, 122, 134)
RED = (255, 92, 92)
GREEN = (38, 196, 146)
FLAT_BG = (36, 40, 46)

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "assets" / "fonts"
TAPE_ORDER = ("上证指数", "深证成指", "创业板指", "沪深300", "上证50", "中证500", "中证1000", "科创50")
MORNING_ABROAD = ("道琼斯", "纳斯达克", "标普500", "日经225", "韩国KOSPI", "韩国KOSDAQ")


def _font_file(weight: str) -> Path:
    names = {
        "regular": "NotoSansSC-Regular.ttf",
        "medium": "NotoSansSC-Medium.ttf",
        "bold": "NotoSansSC-Bold.ttf",
    }
    path = FONT_DIR / names[weight]
    if path.exists():
        return path
    return Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


class Canvas:
    def __init__(self) -> None:
        self.scale = SCALE
        self.w = WIDTH
        self.pad = PAD
        self.y = 0
        self.image = Image.new("RGB", (WIDTH * SCALE, 5200 * SCALE), BG)
        self.draw = ImageDraw.Draw(self.image)
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def font(self, weight: str, size: int) -> ImageFont.FreeTypeFont:
        key = (weight, size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(_font_file(weight)), size * self.scale)
        return self._fonts[key]

    def s(self, value: float) -> float:
        return value * self.scale

    def text(self, x: float, y: float, value: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
        self.draw.text((self.s(x), self.s(y)), value, font=font, fill=fill)

    def text_right(self, right: float, y: float, value: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
        width = font.getlength(value) / self.scale
        self.text(right - width, y, value, font, fill)

    def ellipsize(self, value: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
        if font.getlength(value) <= self.s(max_width):
            return value
        ellipsis = "…"
        while value and font.getlength(value + ellipsis) > self.s(max_width):
            value = value[:-1]
        return value + ellipsis

    def wrap(self, value: str, font: ImageFont.FreeTypeFont, max_width: float, max_lines: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for index, char in enumerate(value):
            if current and font.getlength(current + char) > self.s(max_width):
                if len(lines) == max_lines - 1:
                    lines.append(self.ellipsize(current + value[index:], font, max_width))
                    return lines
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(self.ellipsize(current, font, max_width))
        return lines[:max_lines]

    def round(self, x: float, y: float, w: float, h: float, fill: tuple[int, int, int], radius: float = 12, outline: tuple[int, int, int] | None = None) -> None:
        self.draw.rounded_rectangle(
            (self.s(x), self.s(y), self.s(x + w), self.s(y + h)),
            radius=self.s(radius),
            fill=fill,
            outline=outline,
            width=self.scale if outline else 1,
        )

    def rule(self, color: tuple[int, int, int] = HAIR) -> None:
        self.draw.line(
            (self.s(self.pad), self.s(self.y), self.s(self.w - self.pad), self.s(self.y)),
            fill=color,
            width=self.scale,
        )

    def gap(self, amount: float) -> None:
        self.y += amount

    def finish(self) -> Image.Image:
        height = int(self.s(self.y + 8))
        return self.image.crop((0, 0, WIDTH * SCALE, height))


def _tone(value: float | None) -> tuple[int, int, int]:
    if value is None or abs(value) < 0.005:
        return MUTED
    return RED if value > 0 else GREEN


def _content_width() -> int:
    return WIDTH - PAD * 2


def _style_color(style: str) -> tuple[int, int, int]:
    if style == "普跌":
        return GREEN
    if style in {"普涨", "成长占优"}:
        return RED
    return AMBER


def _masthead(canvas: Canvas, brief: Brief) -> None:
    canvas.draw.rectangle((0, 0, canvas.s(canvas.w), canvas.s(4)), fill=AMBER)
    canvas.y = 28
    canvas.text(canvas.pad, canvas.y, "INFOUPDATES", canvas.font("medium", 13), AMBER)
    shown = brief.edition_date()
    title = f"A股{brief.title}"
    title_font = canvas.font("bold", 40)
    canvas.y += 28
    canvas.text(canvas.pad, canvas.y, title, title_font, TEXT)
    date_label = f"{shown.month}月{shown.day}日  {weekday_cn(shown)}"
    date_font = canvas.font("bold", 28)
    title_w = title_font.getlength(title) / canvas.scale
    canvas.text(canvas.pad + title_w + 20, canvas.y + 10, date_label, date_font, AMBER)
    canvas.y += 52
    if brief.kind == "close":
        subtitle = "股指  ·  板块  ·  资金  ·  情绪  ·  方向"
    else:
        subtitle = f"昨日 {brief.trade_date.month}月{brief.trade_date.day}日收盘  ·  隔夜美日韩  ·  今日关注"
    canvas.text(canvas.pad, canvas.y, subtitle, canvas.font("regular", 14), MUTED)
    canvas.y += 28
    canvas.rule(AMBER)
    canvas.gap(22)


def _direction(canvas: Canvas, brief: Brief) -> None:
    font = canvas.font("regular", 16)
    inner_w = _content_width() - 40
    lines = canvas.wrap(brief.narrative.summary, font, inner_w, 4)
    height = 78 + len(lines) * 26
    x = canvas.pad
    y = canvas.y
    canvas.round(x, y, _content_width(), height, CARD, radius=16, outline=LINE)
    canvas.draw.rectangle((canvas.s(x), canvas.s(y + 16), canvas.s(x + 4), canvas.s(y + height - 16)), fill=AMBER)
    label = f"{brief.session_label()}情绪" if brief.kind == "morning" else "市场方向"
    canvas.text(x + 22, y + 16, label, canvas.font("regular", 13), AMBER)
    style_color = _style_color(brief.narrative.style)
    canvas.text(x + 22, y + 36, brief.narrative.style, canvas.font("bold", 28), style_color)
    sentiment = brief.narrative.sentiment
    pill_font = canvas.font("medium", 13)
    pill_w = pill_font.getlength(sentiment) / canvas.scale + 22
    pill_x = x + _content_width() - pill_w - 18
    canvas.round(pill_x, y + 40, pill_w, 28, FLAT_BG, radius=8)
    canvas.text(pill_x + 11, y + 45, sentiment, pill_font, TEXT)
    text_y = y + 78
    for line in lines:
        canvas.text(x + 22, text_y, line, font, MUTED)
        text_y += 26
    canvas.y += height + 18


def _tape(canvas: Canvas, brief: Brief, heading: str = "") -> None:
    by_name = {quote.name: quote for quote in brief.indices}
    if brief.hero.last > 0:
        by_name[brief.hero.name] = brief.hero
    quotes = [by_name[name] for name in TAPE_ORDER if name in by_name and by_name[name].last > 0]
    if not quotes:
        return
    if heading:
        canvas.text(canvas.pad, canvas.y, heading, canvas.font("medium", 18), TEXT)
        canvas.y += 32
    columns = 4 if len(quotes) > 4 else len(quotes)
    width = _content_width()
    cell_w = width / columns
    cell_h = 78
    rows = (len(quotes) + columns - 1) // columns
    height = rows * cell_h
    y = canvas.y
    canvas.round(canvas.pad, y, width, height, CARD, radius=14, outline=LINE)
    for index, quote in enumerate(quotes):
        col = index % columns
        row = index // columns
        cell_x = canvas.pad + col * cell_w
        cell_y = y + row * cell_h
        if col:
            canvas.draw.line(
                (canvas.s(cell_x), canvas.s(cell_y + 12), canvas.s(cell_x), canvas.s(cell_y + cell_h - 12)),
                fill=HAIR,
                width=canvas.scale,
            )
        if row:
            canvas.draw.line(
                (canvas.s(canvas.pad + 16), canvas.s(cell_y), canvas.s(canvas.pad + width - 16), canvas.s(cell_y)),
                fill=HAIR,
                width=canvas.scale,
            )
        text_x = cell_x + 16
        canvas.text(text_x, cell_y + 12, quote.name, canvas.font("regular", 13), DIM)
        canvas.text(text_x, cell_y + 32, fmt_px(quote.last), canvas.font("medium", 18), TEXT)
        canvas.text(text_x, cell_y + 54, fmt_pct(quote.pct), canvas.font("medium", 14), _tone(quote.pct))
    canvas.y += height + 22


def _section(canvas: Canvas, title: str, note: str = "") -> None:
    canvas.text(canvas.pad, canvas.y, title, canvas.font("medium", 18), TEXT)
    if note:
        canvas.text_right(canvas.w - canvas.pad, canvas.y + 4, note, canvas.font("regular", 12), DIM)
    canvas.y += 30
    canvas.rule()
    canvas.gap(14)


def _stat(canvas: Canvas, x: float, y: float, w: float, label: str, value: str, color: tuple[int, int, int]) -> None:
    canvas.text(x, y, label, canvas.font("regular", 12), DIM)
    canvas.text(x, y + 18, value, canvas.font("bold", 22), color)


def _sentiment(canvas: Canvas, brief: Brief) -> None:
    breadth = brief.breadth
    if breadth is None and brief.turnover is None:
        return
    _section(canvas, "市场情绪", "红涨  绿跌")
    if breadth:
        width = _content_width()
        cell = width / 5
        y = canvas.y
        stats = (
            ("上涨", f"{breadth.up}", RED),
            ("下跌", f"{breadth.down}", GREEN),
            ("平盘", f"{breadth.flat}", MUTED),
            ("涨停", f"{breadth.limit_up}", RED),
            ("跌停", f"{breadth.limit_down}", GREEN),
        )
        for index, (label, value, color) in enumerate(stats):
            _stat(canvas, canvas.pad + index * cell, y, cell, label, value, color)
        canvas.y += 58
        if breadth.total:
            ratio = breadth.up / breadth.total
            canvas.text(
                canvas.pad,
                canvas.y,
                f"上涨占比 {ratio * 100:.1f}%",
                canvas.font("regular", 13),
                MUTED,
            )
            canvas.y += 22
            _split_bar(canvas, canvas.pad, canvas.y, width, breadth.down, breadth.flat, breadth.up)
            canvas.y += 22
        if breadth.buckets:
            _histogram(canvas, canvas.pad, canvas.y, width, breadth.buckets)
            canvas.y += 118
    if brief.turnover is not None:
        canvas.text(canvas.pad, canvas.y, f"沪深成交额  {fmt_amount(brief.turnover)}", canvas.font("medium", 16), TEXT)
        canvas.gap(28)
    else:
        canvas.gap(8)


def _split_bar(canvas: Canvas, x: float, y: float, w: float, down: int, flat: int, up: int) -> None:
    total = down + flat + up or 1
    height = 8
    canvas.round(x, y, w, height, HAIR, radius=4)
    down_w = w * down / total
    flat_w = w * flat / total
    if down_w > 1:
        canvas.draw.rectangle((canvas.s(x), canvas.s(y), canvas.s(x + down_w), canvas.s(y + height)), fill=GREEN)
    if flat_w > 1:
        canvas.draw.rectangle(
            (canvas.s(x + down_w), canvas.s(y), canvas.s(x + down_w + flat_w), canvas.s(y + height)),
            fill=DIM,
        )
    up_x = x + down_w + flat_w
    if w - (up_x - x) > 1:
        canvas.draw.rectangle((canvas.s(up_x), canvas.s(y), canvas.s(x + w), canvas.s(y + height)), fill=RED)


def _histogram(canvas: Canvas, x: float, y: float, w: float, buckets) -> None:
    count = len(buckets)
    gap = 8
    cell = (w - gap * (count - 1)) / count
    peak = max((bucket.count for bucket in buckets), default=1) or 1
    chart_h = 64
    for index, bucket in enumerate(buckets):
        bar_h = 2 if bucket.count == 0 else max(4, chart_h * bucket.count / peak)
        bar_x = x + index * (cell + gap)
        color = {"up": RED, "down": GREEN, "flat": DIM}[bucket.side]
        canvas.draw.rectangle(
            (
                canvas.s(bar_x),
                canvas.s(y + chart_h - bar_h),
                canvas.s(bar_x + cell),
                canvas.s(y + chart_h),
            ),
            fill=color,
        )
        label_font = canvas.font("regular", 11)
        count_font = canvas.font("medium", 11)
        label = canvas.ellipsize(bucket.label, label_font, cell + 2)
        count_text = str(bucket.count)
        label_w = label_font.getlength(label) / canvas.scale
        count_w = count_font.getlength(count_text) / canvas.scale
        canvas.text(bar_x + max(0, (cell - label_w) / 2), y + chart_h + 8, label, label_font, DIM)
        canvas.text(bar_x + max(0, (cell - count_w) / 2), y + chart_h + 24, count_text, count_font, MUTED)


def _sectors(canvas: Canvas, brief: Brief, title: str = "板块涨跌") -> None:
    if not brief.sectors_up and not brief.sectors_down:
        return
    _section(canvas, title, brief.sector_source)
    gap = 28
    col_w = (_content_width() - gap) / 2
    left_rows = [(item.name, fmt_pct(item.pct), item.pct, f"领涨  {item.leader}" if item.leader else "") for item in brief.sectors_up]
    right_rows = [(item.name, fmt_pct(item.pct), item.pct, f"领跌  {item.leader}" if item.leader else "") for item in brief.sectors_down]
    top = canvas.y
    _column_bars(canvas, canvas.pad, top, col_w, "涨幅居前", left_rows)
    height = _column_bars(canvas, canvas.pad + col_w + gap, top, col_w, "跌幅居前", right_rows, measure_only=False)
    left_height = _column_bars(canvas, canvas.pad, top, col_w, "涨幅居前", left_rows, measure_only=True)
    canvas.y = top + max(height, left_height)


def _column_bars(
    canvas: Canvas,
    x: float,
    y: float,
    width: float,
    title: str,
    rows: list[tuple[str, str, float, str]],
    measure_only: bool = False,
) -> float:
    cursor = y
    if title:
        if not measure_only:
            canvas.text(x, cursor, title, canvas.font("regular", 13), AMBER)
        cursor += 24
    peak = max((abs(value) for _, _, value, _ in rows), default=1) or 1
    for name, extra, value, leader in rows:
        color = _tone(value)
        if not measure_only:
            canvas.text(x, cursor, canvas.ellipsize(name, canvas.font("medium", 15), width - 84), canvas.font("medium", 15), TEXT)
            canvas.text_right(x + width, cursor, extra, canvas.font("medium", 15), color)
        cursor += 22
        if not measure_only:
            canvas.round(x, cursor, width, 6, HAIR, radius=3)
            fill_w = max(4, width * abs(value) / peak)
            canvas.draw.rectangle((canvas.s(x), canvas.s(cursor), canvas.s(x + fill_w), canvas.s(cursor + 6)), fill=color)
        cursor += 12
        if leader:
            if not measure_only:
                canvas.text(x, cursor, canvas.ellipsize(leader, canvas.font("regular", 12), width), canvas.font("regular", 12), DIM)
            cursor += 18
        cursor += 8
    return cursor - y


def _capital(canvas: Canvas, brief: Brief) -> None:
    if not brief.capital and not brief.sector_in and brief.cross is None:
        return
    _section(canvas, "资金流向", "主力按沪市+深市")
    if brief.capital:
        total = _sum_capital(brief)
        color = _tone(1 if total.main > 0 else -1 if total.main < 0 else 0)
        canvas.text(canvas.pad, canvas.y, "沪深主力净额", canvas.font("regular", 13), DIM)
        canvas.text(canvas.pad, canvas.y + 18, fmt_yi(total.main, signed=True), canvas.font("bold", 32), color)
        parts = "   ".join(f"{item.market} {fmt_yi(item.main, signed=True)}" for item in brief.capital)
        canvas.text(canvas.pad + 250, canvas.y + 28, parts, canvas.font("regular", 14), MUTED)
        canvas.y += 64
        mixes = (
            ("超大单", total.super_order),
            ("大单", total.large),
            ("中单", total.mid),
            ("小单", total.small),
        )
        cell = _content_width() / 4
        for index, (label, value) in enumerate(mixes):
            _stat(canvas, canvas.pad + index * cell, canvas.y, cell, label, fmt_yi(value, signed=True), _tone(value))
        canvas.y += 62
    if brief.sector_in or brief.sector_out:
        gap = 28
        col_w = (_content_width() - gap) / 2
        top = canvas.y
        canvas.text(canvas.pad, top, "净流入", canvas.font("regular", 13), AMBER)
        canvas.text(canvas.pad + col_w + gap, top, "净流出", canvas.font("regular", 13), AMBER)
        canvas.text_right(canvas.w - canvas.pad, top + 1, brief.flow_source, canvas.font("regular", 12), DIM)
        left = [(item.name, fmt_yi(item.net, signed=True), item.net, "") for item in brief.sector_in]
        right = [(item.name, fmt_yi(item.net, signed=True), item.net, "") for item in brief.sector_out]
        left_h = _column_bars(canvas, canvas.pad, top + 22, col_w, "", left)
        right_h = _column_bars(canvas, canvas.pad + col_w + gap, top + 22, col_w, "", right)
        # _column_bars prints an empty title line of 24px. The headers are already drawn.
        canvas.y = top + 22 + max(left_h, right_h)
    if brief.cross:
        canvas.gap(4)
        cross = brief.cross
        canvas.text(canvas.pad, canvas.y, "南向净买入", canvas.font("regular", 13), DIM)
        canvas.text(
            canvas.pad,
            canvas.y + 18,
            fmt_yi(cross.south_net, signed=True, unit="亿港元"),
            canvas.font("bold", 24),
            _tone(cross.south_net),
        )
        detail = []
        if cross.south_sh is not None:
            detail.append(f"港股通(沪) {fmt_yi(cross.south_sh, signed=True, unit='亿港元')}")
        if cross.south_sz is not None:
            detail.append(f"港股通(深) {fmt_yi(cross.south_sz, signed=True, unit='亿港元')}")
        if cross.north_turnover is not None:
            detail.append(f"北向成交 {fmt_amount(cross.north_turnover)}")
        canvas.y += 54
        if detail:
            canvas.text(canvas.pad, canvas.y, "    ".join(detail), canvas.font("regular", 14), MUTED)
            canvas.gap(24)
        canvas.text(canvas.pad, canvas.y, "北向净买入不再逐日披露，这里只保留成交额。", canvas.font("regular", 12), DIM)
        canvas.gap(28)


def _sum_capital(brief: Brief):
    rows = brief.capital
    from .models import CapitalMix

    return CapitalMix(
        market="合计",
        main=sum(item.main for item in rows),
        super_order=sum(item.super_order for item in rows),
        large=sum(item.large for item in rows),
        mid=sum(item.mid for item in rows),
        small=sum(item.small for item in rows),
    )


def _quote_grid(canvas: Canvas, quotes: list[Quote], columns: int = 3) -> None:
    if not quotes:
        canvas.text(canvas.pad, canvas.y, "暂无", canvas.font("regular", 14), DIM)
        canvas.gap(28)
        return
    width = _content_width()
    rows = (len(quotes) + columns - 1) // columns
    cell_w = width / columns
    cell_h = 78
    y0 = canvas.y
    for index, quote in enumerate(quotes):
        col = index % columns
        row = index // columns
        x = canvas.pad + col * cell_w
        y = y0 + row * cell_h
        name_font = canvas.font("regular", 13)
        canvas.text(x, y, canvas.ellipsize(quote.name, name_font, cell_w - 16), name_font, DIM)
        canvas.text(x, y + 20, fmt_px(quote.last), canvas.font("medium", 18), TEXT)
        pct_y = y + 46
        canvas.text(x, pct_y, fmt_pct(quote.pct), canvas.font("medium", 14), _tone(quote.pct))
        if quote.session:
            session_x = x + canvas.font("medium", 14).getlength(fmt_pct(quote.pct)) / canvas.scale + 8
            canvas.text(session_x, pct_y + 1, quote.session, canvas.font("regular", 12), DIM)
    canvas.y = y0 + rows * cell_h + 8


def _morning_abroad(canvas: Canvas, brief: Brief) -> None:
    by_name = {quote.name: quote for quote in brief.overseas}
    quotes = [by_name[name] for name in MORNING_ABROAD if name in by_name]
    _section(canvas, "隔夜外盘", "美日韩")
    _quote_grid(canvas, quotes, columns=3)
    canvas.gap(8)


def _overseas(canvas: Canvas, brief: Brief) -> None:
    quotes = list(brief.overseas) + list(brief.fx)
    if not quotes:
        return
    _section(canvas, "外围市场", "收盘价")
    _quote_grid(canvas, quotes, columns=3)
    canvas.gap(8)


def _news(canvas: Canvas, brief: Brief, title: str) -> None:
    _section(canvas, title, "见闻 / 东财")
    if not brief.news:
        canvas.text(canvas.pad, canvas.y, "这一时段没有筛出重要快讯。", canvas.font("regular", 15), DIM)
        canvas.gap(28)
        return
    body = canvas.font("regular", 16)
    for item in brief.news:
        lines = canvas.wrap(item.title, body, _content_width() - 92, 2)
        row_h = max(40, 8 + len(lines) * 24)
        canvas.text(canvas.pad, canvas.y, item.published.strftime("%H:%M"), canvas.font("medium", 14), AMBER)
        canvas.text(canvas.pad, canvas.y + 20, item.source, canvas.font("regular", 12), DIM)
        for index, line in enumerate(lines):
            canvas.text(canvas.pad + 84, canvas.y + index * 24, line, body, TEXT)
        canvas.y += row_h
        canvas.rule()
        canvas.gap(10)
    canvas.gap(6)


def _watch(canvas: Canvas, brief: Brief) -> None:
    if not brief.narrative.watch:
        return
    _section(canvas, "今日关注")
    for index, item in enumerate(brief.narrative.watch, start=1):
        canvas.text(canvas.pad, canvas.y, f"{index:02d}", canvas.font("bold", 16), AMBER)
        lines = canvas.wrap(item, canvas.font("regular", 16), _content_width() - 48, 2)
        for line_index, line in enumerate(lines):
            canvas.text(canvas.pad + 40, canvas.y + line_index * 24, line, canvas.font("regular", 16), TEXT)
        canvas.y += max(28, len(lines) * 24) + 10
    canvas.gap(8)


def _footer(canvas: Canvas, brief: Brief) -> None:
    canvas.gap(6)
    canvas.rule(AMBER)
    canvas.gap(14)
    canvas.text(canvas.pad, canvas.y, "数据  新浪财经  ·  东方财富  ·  华尔街见闻", canvas.font("regular", 12), DIM)
    canvas.text_right(
        canvas.w - canvas.pad,
        canvas.y,
        brief.generated_at.astimezone(brief.generated_at.tzinfo).strftime("%m-%d %H:%M CST"),
        canvas.font("regular", 12),
        DIM,
    )
    canvas.gap(20)
    canvas.text(canvas.pad, canvas.y, "公开行情可能延迟。只做信息整理，不构成投资建议。", canvas.font("regular", 12), DIM)
    if brief.notes:
        canvas.gap(18)
        canvas.text(canvas.pad, canvas.y, "  ".join(brief.notes), canvas.font("regular", 12), DIM)
    canvas.gap(28)


def _draw(brief: Brief) -> Image.Image:
    canvas = Canvas()
    _masthead(canvas, brief)
    _direction(canvas, brief)
    if brief.kind == "close":
        _tape(canvas, brief)
        _sentiment(canvas, brief)
        _sectors(canvas, brief)
        _capital(canvas, brief)
        _overseas(canvas, brief)
    else:
        label = brief.session_label()
        _tape(canvas, brief, f"{label}指数")
        _sectors(canvas, brief, f"{label}板块")
        _morning_abroad(canvas, brief)
        _news(canvas, brief, "隔夜要闻")
        _watch(canvas, brief)
    _footer(canvas, brief)
    return canvas.finish()


def render_png(brief: Brief, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _draw(brief).save(destination, format="PNG", optimize=True)
    return destination
