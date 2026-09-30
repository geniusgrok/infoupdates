from __future__ import annotations

from .format import fmt_pct, fmt_px, fmt_yi
from .models import Brief, Narrative, Quote, SectorFlow


def _find_flow(rows: list[SectorFlow], name: str) -> SectorFlow | None:
    for row in rows:
        if row.name == name:
            return row
    return None


def _style(hero: Quote, sz50: Quote | None, kc: Quote | None, cyb: Quote | None, adv: float | None) -> str:
    growth = [quote.pct for quote in (kc, cyb) if quote and quote.pct is not None]
    growth_pct = min(growth) if growth else None
    if hero.pct is not None and hero.pct <= -1 and adv is not None and adv < 0.4:
        return "普跌"
    if hero.pct is not None and hero.pct >= 1 and adv is not None and adv > 0.62:
        return "普涨"
    if sz50 and sz50.pct is not None and growth_pct is not None and sz50.pct - growth_pct >= 1.2 and hero.pct is not None and hero.pct >= -0.3:
        return "权重护盘"
    if growth_pct is not None and hero.pct is not None and growth_pct - hero.pct >= 0.8 and growth_pct > 0:
        return "成长占优"
    return "结构分化"


def _sentiment(hero: Quote, kc: Quote | None, adv: float | None, limit_up: int, limit_down: int, main_net: float | None) -> str:
    score = 50.0
    if hero.pct is not None:
        score += max(-3.0, min(3.0, hero.pct)) * 6
    if adv is not None:
        score += (adv - 0.5) * 80
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


def _style_clause(style: str, sz50: Quote | None, kc: Quote | None) -> str:
    if style == "权重护盘" and sz50 and kc and sz50.pct is not None and kc.pct is not None:
        return f"上证50 {fmt_pct(sz50.pct)}、科创50 {fmt_pct(kc.pct)}，权重强于成长"
    if style == "成长占优" and kc and kc.pct is not None:
        return f"科创50 {fmt_pct(kc.pct)}，成长强于权重"
    if style == "普涨":
        return "指数与个股一起走强"
    if style == "普跌":
        return "指数与个股一起走弱"
    return "板块之间分化，指数涨跌代表不了全市场"


def build_narrative(brief: Brief) -> Narrative:
    hero = brief.hero
    if hero.last <= 0:
        return Narrative("数据暂缺", "—", "指数行情没有取到，其余内容按已经返回的数据展示。", _watch(brief, "结构分化"))
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
    style_clause = _style_clause(style, sz50, kc)
    line1 = f"上证收于{fmt_px(hero.last)}（{fmt_pct(hero.pct)}），{style_clause}。"
    if breadth:
        line2 = (
            f"上涨{breadth.up}家、下跌{breadth.down}家，涨停{breadth.limit_up}、跌停{breadth.limit_down}，"
            f"情绪{sentiment}。"
        )
    else:
        line2 = f"情绪{sentiment}。"
    flow_bits: list[str] = []
    if brief.main_net is not None:
        flow_bits.append(f"沪深主力{fmt_yi(brief.main_net, signed=True)}")
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
    kc = brief.index("科创50")
    if kc and kc.pct is not None and kc.pct <= -1:
        items.append(f"科创50收于{fmt_pct(kc.pct)}，开盘先看成长方向有没有承接。")
    elif style == "成长占优" and kc and kc.pct is not None:
        items.append(f"科创50收于{fmt_pct(kc.pct)}，留意强势是否延续到开盘。")
    if brief.sector_out:
        worst = brief.sector_out[0]
        items.append(f"{worst.name}主力{fmt_yi(worst.net, signed=True)}，观察流出是否放缓。")
    if brief.sector_in:
        best = brief.sector_in[0]
        items.append(f"{best.name}主力{fmt_yi(best.net, signed=True)}，看强势板块能否继续吸引资金。")
    titles = " ".join(item.title for item in brief.news)
    if any(word in titles for word in ("PCE", "ADP", "美联储", "GDP")):
        items.append("美国就业、通胀或增长数据仍在影响风险偏好，开盘前先看外盘期货。")
    if brief.cross and brief.cross.south_net is not None and brief.cross.south_net > 0:
        items.append(f"南向净买入{fmt_yi(brief.cross.south_net, unit='亿港元')}，港股风险偏好偏积极。")
    if len(items) < 3 and brief.breadth:
        items.append(
            f"涨停{brief.breadth.limit_up}家、跌停{brief.breadth.limit_down}家，短线情绪看涨停家数会不会明显回落。"
        )
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]
