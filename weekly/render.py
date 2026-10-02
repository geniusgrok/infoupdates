from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ashare.models import CST
from common.format import fmt_pct, weekday_cn
from common.render import AMBER, MUTED, TEXT, Canvas, change_color


def render_png(brief: dict, path: Path) -> Path:
    canvas = Canvas()
    canvas.draw.rectangle((0, 0, 1080, 6), fill=AMBER)
    day = date.fromisoformat(brief["edition_date"])
    now = datetime.fromisoformat(brief["generated_at"]).astimezone(CST)
    canvas.text(28, 24, "INFOUPDATES", 28, AMBER, "bold")
    canvas.text(1052, 27, f"生成{now:%m.%d %H:%M} CST", 24, MUTED, align="right")
    canvas.text(28, 75, f"{day:%Y.%m.%d} {weekday_cn(day).replace('周', '星期')} · 每周精选", 51, TEXT, "bold", max_width=1024, min_size=44)
    canvas.text(28, 145, brief["headline"], 42, AMBER, "bold", max_width=1024, min_size=34)
    canvas.text(28, 215, f"交易周 {brief['start']} ～ {brief['end']} · A股 / 美股", 28, MUTED, max_width=1024)
    for i, item in enumerate(brief["metrics"]):
        x, width = 28 + i * 259, 247
        canvas.card(x, 270, width, 180)
        canvas.text(x + width / 2, 289, item["name"], 30, MUTED, "medium", "center", max_width=width - 24)
        canvas.text(x + width / 2, 333, fmt_pct(item["pct"]), 46, change_color(item["pct"]), "medium", "center", max_width=width - 24)
        label = "本周休市" if not item["expected"] else "周涨跌待确认" if item["pct"] is None else f"{item['base_date'][5:]} 至 {item['end_date'][5:]}"
        canvas.text(x + width / 2, 395, label, 22, TEXT, align="center", max_width=width - 24)
        canvas.text(x + width / 2, 421, f"数据覆盖 {item['coverage']}/{item['expected']}日", 18, MUTED, align="center", max_width=width - 24)

    canvas.card(28, 468, 1024, 314)
    canvas.heading(50, 490, "01 本周发生了什么")
    for i, value in enumerate(brief["changes"]):
        canvas.text(50, 550 + i * 45, value, 30, TEXT, "medium", max_width=980, min_size=26)
    canvas.line(50, 638, 1030)
    canvas.paragraph(50, 655, brief["story"]["title"], 980, 28, 2, pitch=34)
    canvas.text(50, 748, brief["story"]["stamp"] or "本周重要消息未留存", 20, MUTED, max_width=980)

    canvas.card(28, 800, 1024, 320)
    canvas.heading(50, 824, "02 行情是否支持")
    for i, value in enumerate(brief["evidence"][:3]):
        canvas.paragraph(50, 889 + i * 70, value, 980, 28, 2, pitch=34)

    canvas.card(28, 1138, 1024, 420)
    canvas.heading(50, 1160, "03 下周如何验证")
    canvas.paragraph(50, 1226, brief["future"]["title"], 980, 34, 2, weight="medium", pitch=40)
    canvas.text(50, 1320, brief["future"]["stamp"], 21, MUTED, max_width=980)
    canvas.paragraph(50, 1364, brief["future"]["watch"], 980, 30, 2, pitch=36)
    canvas.line(50, 1451, 1030)
    canvas.paragraph(50, 1474, "用随后数据与价格反应验证；方向一致不等于由单一消息导致。", 980, 26, 2, MUTED, pitch=32)
    canvas.text(28, 1590, "统计基准、来源与历史完整度见文字版", 18, MUTED, max_width=590)
    canvas.text(1052, 1590, "公开行情可能延迟 · 不构成投资建议", 18, MUTED, align="right", max_width=420)
    canvas.save(path)
    return path
