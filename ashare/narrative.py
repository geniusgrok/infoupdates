from __future__ import annotations

from dataclasses import replace
from math import isfinite

from common.editorial import FocusItem, build_focus
from common.format import fmt_pct, fmt_px, fmt_yi
from .models import Brief, Narrative, Quote, SectorFlow, china_time

MORNING_ABROAD = ("道琼斯", "纳斯达克", "标普500", "日经225")


def headline(brief: Brief) -> str:
    """图片和摘要共用同一判断，避免两份内容各说一套。"""
    if brief.kind == "morning":
        quotes = {quote.name: quote for quote in brief.overseas if isfinite(quote.last) and quote.last > 0}
        values = [quotes[name].pct if name in quotes else None for name in MORNING_ABROAD[:3]]
        if any(value is None or not isfinite(value) for value in values):
            return "美股行情待确认"
        dow, nasdaq, sp = values
        if nasdaq > 0 > dow:
            return "纳指上涨，道指回落"
        if dow > 0 > nasdaq:
            return "道指上涨，纳指回落"
        if nasdaq > 0 and nasdaq - max(dow, sp) >= 0.8:
            return "纳指领涨，其他指数相对滞后"
        if all(value > 0 for value in values):
            return "美股三大指数上涨"
        if all(value < 0 for value in values):
            return "美股三大指数下跌"
        if all(value == 0 for value in values):
            return "美股三大指数平收"
        return "美股三大指数涨跌互现"
    if brief.hero.pct is not None and isfinite(brief.hero.pct) and brief.breadth:
        if brief.hero.pct > 0 and brief.breadth.down > brief.breadth.up:
            return "指数上涨，下跌家数更多"
        if brief.hero.pct < 0 and brief.breadth.up > brief.breadth.down:
            return "指数回落，上涨家数更多"
    weight, star = brief.index("上证50"), brief.index("科创50")
    if weight and star and weight.pct is not None and star.pct is not None and weight.pct > 0 > star.pct:
        return "权重微涨，科创回落" if weight.pct < 1 else "权重走强，科创回落"
    return {"普涨": "指数与个股走强", "普跌": "指数与个股走弱", "成长占优": "成长板块相对占优",
            "权重护盘": "权重强于成长", "数据暂缺": "市场数据待确认"}.get(brief.narrative.style, "指数与板块表现分化")


def focus_items(brief: Brief) -> list[FocusItem]:
    items = build_focus(brief.news, now=brief.generated_at, market="ashare", watch=watch_line(brief), event=brief.event)
    if (brief.kind == "morning" and brief.event is not None
            and china_time(brief.event.at).date() < brief.edition_date
            and items[1].label == "下一事件"):
        items[1] = replace(items[1], label="假期关注")
    return items


def watch_line(brief: Brief) -> str:
    """观察条件承接本版结构；早盘把外盘变化转为A股开盘后的验证。"""
    lead = headline(brief)
    if brief.kind == "morning":
        if lead.startswith(("纳指上涨", "纳指领涨")):
            return "观察A股成长板块是否跟进，上涨家数与沪市成交额能否配合。"
        if lead.startswith("道指上涨"):
            return "观察A股权重与成长是否分化，上涨家数能否增加。"
        if lead == "美股三大指数下跌":
            return "观察A股主要指数能否企稳，下跌家数是否减少。"
        return "观察A股开盘方向与外盘是否一致，上涨家数与沪市成交额能否配合。"
    if lead == "指数上涨，下跌家数更多":
        return "观察上涨家数能否超过下跌家数，指数强势能否扩散。"
    if lead == "指数回落，上涨家数更多":
        return "观察上涨家数能否维持优势，并带动主要指数企稳。"
    return {
        "权重护盘": "观察成长板块能否跟上权重，上涨家数是否增加。",
        "成长占优": "观察成长强势能否扩散至权重板块，沪市成交额是否配合。",
        "普涨": "观察上涨家数能否维持优势，沪市成交额是否配合。",
        "普跌": "观察下跌家数能否减少，成长指数能否企稳。",
    }.get(brief.narrative.style, "观察主要指数能否同向，上涨家数与沪市成交额是否配合。")


def market_summary(brief: Brief) -> str:
    """统一使用沪市全日成交额描述量能，数值单位为亿元。"""
    labels = {
        "偏弱": "低落", "偏强": "高涨", "温和偏强": "平淡偏强",
        "中性": "平淡", "中性偏谨慎": "平淡偏谨慎",
    }
    sentiment = labels.get(brief.narrative.sentiment, "待确认")
    mood = f"市场情绪{sentiment}"
    comparison = brief.turnover_comparison
    if comparison is None or brief.is_intraday:
        reason = "盘中成交额尚未完整" if brief.is_intraday else "上日完整成交额暂缺"
        return f"{mood}，量能待确认（{reason}）。"
    delta = comparison.current - comparison.previous
    ratio = delta / comparison.previous
    volume = "放量" if ratio > 0.05 else "缩量" if ratio < -0.05 else "基本平量"
    if abs(delta) < 0.05e8:
        change = "成交额与上日基本持平"
    else:
        direction = "增加" if delta > 0 else "减少"
        change = f"成交额较上日{direction}{abs(delta) / 1e8:.1f}亿元"
    return f"{mood}，沪市{volume}（{change}）。"


def _find_flow(rows: list[SectorFlow], name: str) -> SectorFlow | None:
    for row in rows:
        if row.name == name:
            return row
    return None


def _growth_quote(kc: Quote | None, cyb: Quote | None) -> Quote | None:
    growth = [quote for quote in (kc, cyb) if quote and quote.pct is not None]
    return min(growth, key=lambda quote: quote.pct or 0.0, default=None)


def _style(hero: Quote, sz50: Quote | None, kc: Quote | None, cyb: Quote | None, adv: float | None) -> str:
    growth = _growth_quote(kc, cyb)
    growth_pct = growth.pct if growth else None
    if hero.pct is not None and hero.pct <= -1 and adv is not None and adv < 0.4:
        return "普跌"
    if hero.pct is not None and hero.pct >= 1 and adv is not None and adv > 0.62:
        return "普涨"
    if sz50 and sz50.pct is not None and growth_pct is not None and sz50.pct - growth_pct >= 1.2 and hero.pct is not None and hero.pct >= -0.3:
        return "权重护盘"
    if growth_pct is not None and hero.pct is not None and growth_pct - hero.pct >= 0.8 and growth_pct > 0:
        return "成长占优"
    return "结构分化"


def _sentiment(hero: Quote, kc: Quote | None, adv: float | None, limit_up: int | None, limit_down: int | None, main_net: float | None) -> str:
    if hero.pct is None or adv is None:
        return "待确认"
    score = 50.0
    if hero.pct is not None:
        score += max(-3.0, min(3.0, hero.pct)) * 6
    if adv is not None:
        score += (adv - 0.5) * 80
    if limit_up is not None and limit_down is not None:
        score += max(-1.0, min(1.0, (limit_up - limit_down) / 80)) * 8
    if main_net is not None and main_net < 0:
        score -= 5
    if kc and kc.pct is not None and kc.pct < -1:
        score -= min(8.0, abs(kc.pct) * 2)
    if score >= 62:
        return "偏强"
    if score >= 54:
        return "温和偏强"
    if score >= 46:
        return "中性"
    if score >= 38:
        return "中性偏谨慎"
    return "偏弱"


def _style_clause(style: str, sz50: Quote | None, kc: Quote | None, cyb: Quote | None) -> str:
    growth = _growth_quote(kc, cyb)
    if style == "权重护盘" and sz50 and growth and sz50.pct is not None:
        return f"上证50 {fmt_pct(sz50.pct)}、{growth.name} {fmt_pct(growth.pct)}，权重强于成长"
    if style == "成长占优" and growth:
        return f"{growth.name} {fmt_pct(growth.pct)}，成长强于权重"
    if style == "普涨":
        return "指数与个股一起走强"
    if style == "普跌":
        return "指数与个股一起走弱"
    return "板块之间分化，指数涨跌代表不了全市场"


def build_narrative(brief: Brief) -> Narrative:
    hero = brief.hero
    if hero.last <= 0:
        return Narrative("数据暂缺", "待确认", "指数行情没有取到，其余内容按已经返回的数据展示。", _watch(brief, ""))
    sz50 = brief.index("上证50")
    kc = brief.index("科创50")
    cyb = brief.index("创业板指")
    breadth = brief.breadth
    adv = breadth.adv_ratio if breadth else None
    style = _style(hero, sz50, kc, cyb, adv)
    sentiment = _sentiment(
        hero,
        kc,
        adv,
        breadth.limit_up if breadth else 0,
        breadth.limit_down if breadth else 0,
        brief.main_net,
    )
    style_clause = _style_clause(style, sz50, kc, cyb)
    price_label = "盘中上证现报" if brief.is_intraday else "上证收于"
    line1 = f"{price_label}{fmt_px(hero.last)}（{fmt_pct(hero.pct)}），{style_clause}。"
    if breadth:
        line2 = (
            f"上涨{breadth.up}家、下跌{breadth.down}家，涨停{breadth.limit_up if breadth.limit_up is not None else '—'}、跌停{breadth.limit_down if breadth.limit_down is not None else '—'}，"
            f"情绪{sentiment}。"
        )
    else:
        line2 = f"情绪{sentiment}。"
    flow_bits: list[str] = []
    if brief.main_net is not None:
        flow_bits.append(f"沪深主力{fmt_yi(brief.main_net, signed=True)}")
    else:
        for item in brief.capital:
            flow_bits.append(f"{item.market}主力{fmt_yi(item.main, signed=True)}")
    semi = _find_flow(brief.sector_out, "半导体") or _find_flow(brief.sector_in, "半导体")
    if semi:
        flow_bits.append(f"半导体{fmt_yi(semi.net, signed=True)}")
    if brief.sector_in:
        top = brief.sector_in[0]
        if top.name != "半导体":
            flow_bits.append(f"{top.name}{fmt_yi(top.net, signed=True)}")
    if brief.cross and brief.cross.south_net is not None:
        flow_bits.append(f"南向净买入{fmt_yi(brief.cross.south_net, signed=True, unit='亿港元')}")
    line3 = ("，".join(flow_bits) + "。") if flow_bits else ""
    summary = line1 + line2 + line3
    return Narrative(style=style, sentiment=sentiment, summary=summary, watch=_watch(brief, style))


def _watch(brief: Brief, style: str) -> list[str]:
    items: list[str] = []
    if brief.sectors_up:
        top = brief.sectors_up[0]
        leader = f"，领涨{top.leader}" if top.leader else ""
        items.append(f"{top.name}{brief.session_label()}{fmt_pct(top.pct)}{leader}。")
    if brief.sectors_down:
        worst = brief.sectors_down[0]
        leader = f"，领跌{worst.leader}" if worst.leader else ""
        items.append(f"{worst.name}{brief.session_label()}{fmt_pct(worst.pct)}{leader}。")
    for flow in brief.sector_in:
        if any(flow.name in text for text in items):
            continue
        items.append(f"{flow.name}{brief.session_label()}主力净流入{fmt_yi(flow.net, signed=True)}。")
        if len(items) >= 3:
            break
    if len(items) < 3 and brief.sector_out:
        for flow in brief.sector_out:
            if any(flow.name in text for text in items):
                continue
            items.append(f"{flow.name}{brief.session_label()}主力净流出{fmt_yi(abs(flow.net))}。")
            break
    if not items and style:
        items.append("板块行情暂缺，强弱方向待确认。")
    return items[:3]
