from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ashare.models import CST
from common.format import fmt_pct, weekday_cn
from common.render import AMBER, MUTED, TEXT, WIDTH, Canvas, change_color


def render_png(brief: dict, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, WIDTH, 6), fill=AMBER)
    day = date.fromisoformat(brief["edition_date"])
    now = datetime.fromisoformat(brief["generated_at"]).astimezone(CST)
    canvas.text(32, 24, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(1588, 27, f"生成{now:%m.%d %H:%M} CST", 22, MUTED, align="right")
    canvas.text(32, 68, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · 每周精选", 34, TEXT, "bold", max_width=1556)
    canvas.text(32, 120, brief["headline"], 38, AMBER, "bold", max_width=1556, min_size=32)
    canvas.text(32, 182, f"交易周 {brief['start']} ～ {brief['end']} · A股 / 美股", 26, MUTED, max_width=1556)
    canvas.line(32, 230, 1588, color=AMBER)
    for i, item in enumerate(brief["metrics"]):
        x, width = 32 + i * 392, 380
        canvas.card(x, 248, width, 128)
        canvas.text(x + width / 2, 260, item["name"], 24, MUTED, "medium", "center", max_width=width - 32)
        canvas.text(x + width / 2, 291, fmt_pct(item["pct"]), 36, change_color(item["pct"]), "medium", "center", max_width=width - 32)
        label = "本周休市" if not item["expected"] else "周涨跌待确认" if item["pct"] is None else f"{item['base_date'][5:]} 至 {item['end_date'][5:]}"
        canvas.text(x + width / 2, 333, label, 18, TEXT, align="center", max_width=width - 32)
        canvas.text(x + width / 2, 355, f"数据覆盖 {item['coverage']}/{item['expected']}日", 18, MUTED, align="center", max_width=width - 32)

    for x, title in ((32, "01 本周发生什么"), (556, "02 行情是否支持"), (1080, "03 下周如何验证")):
        canvas.card(x, 392, 508, 636)
        canvas.text(x + 22, 416, title, 34, AMBER, "bold", max_width=464)

    for i, value in enumerate(brief["changes"]):
        canvas.paragraph(54, 480 + i * 100, value, 464, 28, 3, weight="medium", pitch=34)
    canvas.line(54, 708, 518)
    canvas.paragraph(54, 732, brief["story"]["title"], 464, 28, 6, pitch=34)
    canvas.paragraph(54, 980, brief["story"]["stamp"] or "本周重要消息未留存", 464, 20, 2, MUTED, pitch=24)

    for i, value in enumerate(brief["evidence"][:3]):
        canvas.paragraph(578, 480 + i * 156, value, 464, 28, 4, pitch=34)

    canvas.paragraph(1102, 480, brief["future"]["title"], 464, 30, 4, weight="medium", pitch=36)
    canvas.paragraph(1102, 652, brief["future"]["stamp"], 464, 20, 3, MUTED, pitch=26)
    canvas.paragraph(1102, 750, brief["future"]["watch"], 464, 28, 4, pitch=34)
    canvas.line(1102, 912, 1566)
    canvas.paragraph(1102, 934, "用随后数据与价格反应验证；方向一致不等于由单一消息导致。", 464, 24, 3, MUTED, pitch=30)
    canvas.text(32, 1048, "统计基准、来源与历史完整度见文字版", 18, MUTED, max_width=760)
    canvas.text(1588, 1048, "公开行情可能延迟 · 不构成投资建议", 18, MUTED, align="right", max_width=760)
    canvas.save(path)
    return path
