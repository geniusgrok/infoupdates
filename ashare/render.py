from __future__ import annotations

import re
from math import isfinite
from pathlib import Path

from common.render import (
    AMBER, BG, GREEN, HAIR, LINE, MUTED, RED, TEXT, WIDTH, Canvas, change_color,
)

from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi, weekday_cn
from .models import Brief, NewsItem, Quote, china_time
from .narrative import market_summary

MORNING_ABROAD = ("道琼斯", "纳斯达克", "标普500", "日经225", "韩国KOSPI", "韩国KOSDAQ")


def _quotes(brief: Brief) -> dict[str, Quote]:
    quotes = {quote.name: quote for quote in brief.indices + brief.overseas + brief.fx if isfinite(quote.last) and quote.last > 0}
    if brief.hero.last > 0:
        quotes[brief.hero.name] = brief.hero
    return quotes


def _pct(quotes: dict[str, Quote], name: str) -> str:
    return fmt_pct(quotes[name].pct) if name in quotes else "—"


def _change(quotes: dict[str, Quote], name: str) -> float | None:
    return quotes[name].pct if name in quotes else None


def _close_headline(brief: Brief) -> str:
    weight, star = brief.index("上证50"), brief.index("科创50")
    if weight and star and weight.pct is not None and star.pct is not None and weight.pct > 0 > star.pct:
        return "权重微涨，科创回落" if weight.pct < 1 else "权重走强，科创回落"
    return {"普涨": "指数与个股走强", "普跌": "指数与个股走弱", "成长占优": "成长板块相对占优",
            "权重护盘": "权重强于成长", "数据暂缺": "市场数据待确认"}.get(brief.narrative.style, "指数与板块表现分化")


def _funds(brief: Brief) -> tuple[str, float | None]:
    if brief.main_net is not None:
        return "沪深主力净额", brief.main_net
    if len(brief.capital) == 1:
        return f"{brief.capital[0].market}主力净额", brief.capital[0].main
    return "沪深主力净额", None


def _observations(brief: Brief) -> list[tuple[str, str]]:
    main = [brief.index(name) for name in ("上证50", "沪深300", "科创50")]
    available = [quote for quote in main if quote and quote.pct is not None]
    index_fact = "，".join(f"{quote.name}{fmt_pct(quote.pct)}" for quote in available) + "。" if available else "主要股指数据暂缺。"
    breadth = brief.breadth
    if breadth is None or breadth.total == 0:
        breadth_fact = "涨跌家数暂缺，市场情绪待确认。"
    elif breadth.up == breadth.down:
        breadth_fact = f"上涨与下跌各{breadth.up}家。"
    else:
        side = "上涨" if breadth.up > breadth.down else "下跌"
        other = "下跌" if side == "上涨" else "上涨"
        breadth_fact = f"{side}比{other}多{abs(breadth.up - breadth.down)}家。"
    label, value = _funds(brief)
    fund_title = "资金待确认" if value is None else "资金流入" if value > 0 else "资金承压" if value < 0 else "资金平衡"
    fund_fact = "主力资金数据暂缺。" if value is None else f"{label.replace('净额', '')}净{'流入' if value >= 0 else '流出'}{fmt_yi(abs(value), unit='亿元')}。"
    return [("股指表现", index_fact), ("个股分布", breadth_fact), (fund_title, fund_fact)]


def _close(canvas: Canvas, brief: Brief) -> None:
    pad, right, width = 28, 1052, 1024
    quotes = _quotes(brief)
    day = brief.edition_date
    title = "A股盘中快照" if brief.is_intraday else "A股收盘精选"
    canvas.text(pad, 24, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(right, 27, china_time(brief.generated_at).strftime("生成%m.%d %H:%M CST"), 24, MUTED, align="right")
    canvas.text(pad, 75, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · {title}", 51, TEXT, "bold", max_width=width, min_size=44)
    canvas.text(pad, 143, _close_headline(brief), 44, AMBER, "bold", max_width=760, min_size=36)
    canvas.text(right, 155, brief.narrative.style, 29, MUTED, "medium", "right", max_width=230)
    canvas.card(pad, 199, width, 54, radius=12)
    canvas.text(52, 214, market_summary(brief), 30, TEXT, "medium", max_width=976, min_size=25)

    col = (width - 36) / 4
    for i, name in enumerate(("上证指数", "深证成指", "创业板指", "科创50")):
        x = pad + i * (col + 12)
        canvas.card(x, 270, col, 148)
        canvas.text(x + col / 2, 288, name, 33, MUTED, "medium", "center", max_width=col - 24)
        canvas.text(x + col / 2, 336, _pct(quotes, name), 48 if i == 3 else 44, change_color(_change(quotes, name)), "bold" if i == 3 else "medium", "center", max_width=col - 24, min_size=35)
        price = fmt_px(quotes[name].last, 2) if name in quotes else "—"
        canvas.text(x + col / 2, 382, price, 29, TEXT, align="center", max_width=col - 24, min_size=24)
    canvas.card(pad, 432, width, 78)
    for i, name in enumerate(("沪深300", "上证50", "中证500", "中证1000")):
        center = pad + (i + .5) * width / 4
        canvas.text(center, 445, name, 28, MUTED, align="center")
        canvas.text(center, 478, _pct(quotes, name), 32, change_color(_change(quotes, name)), "medium", "center", max_width=232, min_size=26)
        if i:
            canvas.line(pad + i * width / 4, 446, pad + i * width / 4, 496)

    canvas.card(pad, 528, width, 144)
    canvas.heading(50, 546, "市场涨跌家数")
    b = brief.breadth
    stats = (("上涨", str(b.up) if b else "—", RED), ("下跌", str(b.down) if b else "—", GREEN), ("沪深成交额", fmt_amount(brief.turnover), TEXT))
    for i, (label, value, color) in enumerate(stats):
        x = 50 + i * 185
        canvas.text(x, 594, label, 27, MUTED, max_width=165, min_size=24)
        canvas.text(x, 626, value, 39, color, "medium", max_width=165, min_size=29)
        if i:
            canvas.line(x - 18, 595, x - 18, 653)
    canvas.line(620, 550, 620, 652, LINE)
    counts = [str(count) if count is not None else "—" for count in (b.limit_up if b else None, b.limit_down if b else None, b.flat if b else None)]
    canvas.text(644, 557, f"涨停 {counts[0]} · 跌停 {counts[1]} · 平盘 {counts[2]}", 28, max_width=384, min_size=22)
    cursor = 644.0
    if b and b.total > 0:
        for count, color in ((b.up, RED), (b.down, GREEN), (b.flat, MUTED)):
            length = 384 * count / b.total
            if length > 0:
                canvas.draw.rectangle((cursor, 603, cursor + length, 623), fill=color)
            cursor += length
    else:
        canvas.draw.rectangle((644, 603, 1028, 623), fill=HAIR)
    for i, (label, color) in enumerate((("上涨", RED), ("下跌", GREEN), ("平盘", MUTED))):
        x = 644 + i * 136
        canvas.draw.ellipse((x, 642, x + 7, 649), fill=color)
        canvas.text(x + 15, 636, label, 24, MUTED)

    canvas.card(pad, 690, 502, 394)
    canvas.card(550, 690, 502, 394)
    canvas.heading(50, 712, "板块表现")
    canvas.text(508, 722, brief.sector_source, 25, MUTED, align="right", max_width=150, min_size=21)
    canvas.heading(572, 712, "盘中看点" if brief.is_intraday else "收盘看点")
    for label, rows, y, color in (("领涨", brief.sectors_up, 774, RED), ("领跌", brief.sectors_down, 926, GREEN)):
        canvas.card(48, y, 462, 132, BG, 11)
        canvas.line(140, y + 10, 140, y + 122, LINE)
        canvas.text(94, y + 51, label, 28, color, "medium", "center")
        for i in range(3):
            row = rows[i] if i < len(rows) else None
            row_y = y + 9 + i * 40
            canvas.pair(160, row_y, 335, row.name if row else "待确认", fmt_pct(row.pct) if row else "—", 33, color=change_color(row.pct if row else None), weight="medium")
            if i < 2:
                canvas.line(151, row_y + 36, 497, color=LINE)
    for i, ((title, body), y) in enumerate(zip(_observations(brief), (774, 900, 994))):
        canvas.draw.ellipse((572, y, 612, y + 40), fill=AMBER)
        canvas.text(592, y + 7, str(i + 1), 28, BG, "bold", "center")
        canvas.text(630, y + 3, title, 33, TEXT, "bold")
        canvas.paragraph(630, y + 48, body, 398, 28, 2 if i == 0 else 1, pitch=33)
        if i < 2:
            canvas.line(630, y + (111 if i == 0 else 78), 1028)

    canvas.card(pad, 1102, width, 282)
    canvas.heading(50, 1123, "资金流向")
    canvas.text(1030, 1130, f"行业主力资金 · {brief.flow_source}", 25, MUTED, align="right", max_width=650, min_size=21)
    canvas.line(421, 1171, 421, 1366, LINE)
    label, net = _funds(brief)
    canvas.text(62, 1173, label, 30, MUTED)
    canvas.text(62, 1212, fmt_yi(net, signed=True), 61, change_color(net), "bold", max_width=337, min_size=42)
    detail = " / ".join(f"{item.market} {fmt_yi(item.main, signed=True)}" for item in brief.capital) or "沪深资金数据暂缺"
    canvas.text(62, 1281, detail, 24, MUTED, max_width=337, min_size=20)
    cross = brief.cross
    canvas.pair(62, 1317, 337, "南向净买入", fmt_yi(cross.south_net if cross else None, unit="亿港元", signed=True), 25, label_color=MUTED, weight="medium")
    canvas.pair(62, 1351, 337, "北向成交额", fmt_yi(cross.north_turnover if cross else None), 25, label_color=MUTED, weight="medium")
    for x, label, rows, color in ((442, "行业净流入", brief.sector_in, RED), (746, "行业净流出", brief.sector_out, GREEN)):
        canvas.card(x, 1171, 284, 197, radius=10)
        canvas.text(x + 142, 1180, label, 28, color, "medium", "center")
        canvas.line(x + 1, 1211, x + 283, color=LINE)
        for i in range(3):
            row = rows[i] if i < len(rows) else None
            y = 1228 + i * 45
            canvas.pair(x + 13, y, 259, row.name if row else "待确认", fmt_yi(row.net, signed=True) if row else "—", 30, color=change_color(row.net if row else None))
            if i < 2:
                canvas.line(x + 13, y + 37, x + 271)

    canvas.card(pad, 1403, width, 163)
    canvas.heading(50, 1420, "外围参考")
    canvas.text(1030, 1426, "涨跌幅 / 在岸汇率", 25, MUTED, align="right")
    for i, name in enumerate(("道琼斯", "恒生指数", "纳斯达克", "恒生科技", "标普500", "在岸人民币")):
        x, y = (50 if i % 2 == 0 else 574), 1469 + (i // 2) * 31
        value = fmt_px(quotes[name].last, 4) if name == "在岸人民币" and name in quotes else _pct(quotes, name)
        canvas.pair(x, y, 454, name, value, 28, color=TEXT if name == "在岸人民币" else change_color(_change(quotes, name)), label_color=MUTED)
        if i // 2 < 2:
            canvas.line(x, y + 27, x + 454)
    source = ("数据说明见文案" if brief.notes else "公开行情") + " · 新浪 / 腾讯 / 东财" + (" / 搜狐" if brief.turnover_comparison and brief.turnover_comparison.source == "搜狐" else "")
    canvas.text(pad, 1585, source, 18, MUTED, max_width=570)
    canvas.text(right, 1585, "北向仅列成交额 · 不构成投资建议", 18, MUTED, align="right", max_width=440)


def _us_headline(quotes: dict[str, Quote]) -> str:
    values = [_change(quotes, name) for name in MORNING_ABROAD[:3]]
    if any(value is None for value in values):
        return "美股行情待确认"
    if all(value > 0 for value in values):
        return "美股三大指数上涨"
    if all(value < 0 for value in values):
        return "美股三大指数下跌"
    if all(value == 0 for value in values):
        return "美股三大指数平收"
    return "美股三大指数涨跌互现"


def _macro(item: NewsItem) -> tuple[str, str, str] | None:
    # 只有标题明确包含实际值和预期时才突出宏观数字。
    patterns = (
        (r"(美国.*?核心PCE.*?)同比\s*([+-]?[\d.]+%)，?\s*预期\s*([+-]?[\d.]+%)", "同比"),
        (r"(美国.*?ADP就业.*?)变动\s*([+-]?[\d.]+万人)，?\s*预期\s*([+-]?[\d.]+万人)", "就业变动"),
        (r"(美国.*?GDP.*?)年化季环比终值\s*([+-]?[\d.]+%)，?\s*预期\s*([+-]?[\d.]+%)", "年化季环比终值"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, item.title)
        if match:
            title, actual, expected = match.groups()
            return title.removesuffix("物价指数").removesuffix("人数"), actual, f"{label} · 预期 {expected}"
    return None


def _morning(canvas: Canvas, brief: Brief) -> None:
    pad, right, width, gap = 56, 1024, 968, 20
    day = brief.edition_date
    quotes = _quotes(brief)
    canvas.text(pad, 34, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(right, 34, china_time(brief.generated_at).strftime("生成%m.%d %H:%M CST"), 24, MUTED, align="right")
    canvas.text(pad, 89, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · A股早盘精选", 51, TEXT, "bold", max_width=width, min_size=43)
    reference = "盘中行情" if brief.is_intraday else "收盘"
    canvas.text(pad, 177, f"参考{brief.trade_date:%m月%d日}{reference} · 美日韩 / 要闻 / 关注", 34, MUTED, max_width=width, min_size=29)
    canvas.card(pad, 236, width, 188)
    canvas.draw.rounded_rectangle((pad, 260, pad + 5, 382), radius=2, fill=AMBER)
    canvas.text(pad + 28, 259, "外盘概览", 30, AMBER)
    canvas.text(pad + 28, 300, _us_headline(quotes), 60, TEXT, "bold", max_width=width - 56, min_size=48)
    canvas.text(pad + 28, 372, f"{brief.session_label()}A股：{brief.narrative.style} · 情绪{brief.narrative.sentiment}", 40, MUTED, max_width=width - 56, min_size=29)
    canvas.text(pad, 443, "外盘一览", 42, TEXT, "bold")
    canvas.text(right, 451, "美日韩 · 涨跌幅", 28, MUTED, align="right")
    col = (width - gap) / 2
    for i, name in enumerate(MORNING_ABROAD):
        x, y = pad + (i % 2) * (col + gap), 505 + (i // 2) * 94
        canvas.card(x, y, col, 80)
        canvas.pair(x + 20, y + 20, col - 40, name, _pct(quotes, name), 40, 48, change_color(_change(quotes, name)), weight="bold")
    canvas.text(pad, 788, market_summary(brief), 24, MUTED, max_width=width, min_size=20)
    canvas.text(pad, 821, "隔夜要闻", 42, TEXT, "bold")
    canvas.text(right, 829, "宏观 / 市场", 28, MUTED, align="right")
    for i in range(3):
        y = 884 + i * 122
        item = brief.news[i] if i < len(brief.news) else None
        macro = _macro(item) if item else None
        if macro:
            title, value, context = macro
            canvas.pair(pad, y, width, title, value, 42, 54, AMBER, weight="bold")
            canvas.text(pad, y + 59, context, 40, MUTED, max_width=width - 260, min_size=30)
        else:
            canvas.paragraph(pad, y, item.title if item else "暂无可核实要闻", width, 37, 2, weight="medium", pitch=42)
        if item:
            stamp = f"{china_time(item.published):%m-%d %H:%M} · {item.source}"
            canvas.text(right, y + 86, stamp, 20, MUTED, align="right", max_width=width)
        canvas.line(pad, y + 112, right)
    canvas.text(pad, 1292, "下个交易日关注" if brief.preview else "今日关注", 42, TEXT, "bold")
    canvas.text(right, 1300, f"{brief.session_label()}板块 / 资金", 28, MUTED, align="right", max_width=360)
    watch = []
    for rows, label in ((brief.sectors_up, "领涨"), (brief.sectors_down, "承压")):
        item = rows[0] if rows else None
        watch.append((item.name if item else "待确认", fmt_pct(item.pct) if item else "—", label, item.pct if item else None))
    flow = brief.sector_in[0] if brief.sector_in else brief.sector_out[0] if brief.sector_out else None
    watch.append((flow.name if flow else "待确认", fmt_yi(flow.net, signed=True) if flow else "—", "净流入" if flow and flow.net > 0 else "净流出" if flow and flow.net < 0 else "主力净额", flow.net if flow else None))
    col = (width - gap * 2) / 3
    for i, (name, value, label, change) in enumerate(watch):
        x = pad + i * (col + gap)
        canvas.card(x, 1355, col, 128)
        canvas.text(x + 20, 1376, name, 40, max_width=col - 40, min_size=29)
        canvas.pair(x + 20, 1430, col - 40, value, label, 44, 26, MUTED, change_color(change), weight="medium", gap=10)
    canvas.line(pad, 1510, right, color=AMBER, width=2)
    canvas.text(pad, 1534, f"公开行情 · {brief.trade_date:%Y.%m.%d} A股参考 · 新浪 / 腾讯 / 东财 / 见闻", 26, MUTED, max_width=width, min_size=23)
    status = "数据缺失或备用来源详情见同名文案。" if brief.notes else "公开行情可能延迟。"
    canvas.text(pad, 1576, status + "不构成投资建议。", 26, MUTED, max_width=width, min_size=23)


def render_png(brief: Brief, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    if brief.kind == "morning":
        _morning(canvas, brief)
    else:
        _close(canvas, brief)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.image.save(path, optimize=True)
    return path
