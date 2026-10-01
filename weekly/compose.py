from __future__ import annotations

from datetime import date, datetime, time, timedelta
from math import isfinite

from ashare.calendar import is_trading_day as a_trading, previous_trading_day as a_previous
from ashare.models import CST
from common.archive import Archive
from common.editorial import build_focus, event_context
from common.format import fmt_pct, weekday_cn
from common.news import NewsItem
from review.events import comparisons
from usstock.calendar import is_trading_day as u_trading, previous_trading_day as u_previous, session_close
from usstock.models import NY, SECTOR_NAMES

HERO = (("ashare", "sh000001", "上证指数"), ("ashare", "sz399006", "创业板指"),
        ("usstock", "^GSPC", "标普500"), ("usstock", "^IXIC", "纳斯达克"))


def week_start(now: datetime, requested: date | None = None) -> date:
    if now.tzinfo is None:
        raise ValueError("周报时间必须包含时区")
    if requested is not None:
        if requested.weekday() != 0:
            raise ValueError("--week 必须是周一日期")
        start = requested
    else:
        local = now.astimezone(NY)
        start = local.date() - timedelta(days=local.weekday())
        if local < datetime.combine(start + timedelta(days=4), time(20), NY):
            start -= timedelta(days=7)
    end = datetime.combine(start + timedelta(days=4), time(20), NY)
    if now < end:
        raise ValueError("该交易周尚未结束，请在美东周五20:00之后生成")
    return start


def _number(value, *, positive=False):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and (not positive or value > 0) else None


def observations(archive: Archive, now: datetime) -> list[dict]:
    rows = {}
    for capture in archive.snapshots(through=now):
        market, data = capture["market"], capture["data"]
        if market not in {"ashare", "usstock"}:
            continue
        bags = [data.get("indices", [])] if market == "ashare" else [data.get(bag, {}).values() for bag in ("quotes", "completed")]
        for bag in bags:
            for quote in bag:
                if _number(quote.get("last"), positive=True) is None or not quote.get("source"):
                    continue
                try:
                    if market == "ashare":
                        day = date.fromisoformat(quote["trade_day"])
                        stamp = datetime.combine(day, time.fromisoformat(quote["session"]), CST)
                        if not a_trading(day) or stamp.time() < time(15):
                            continue
                        unit = "指数点"
                    else:
                        stamp = datetime.fromisoformat(quote["asof"])
                        if stamp.tzinfo is None or quote["session"] != "regular" or quote.get("cached"):
                            continue
                        stamp = stamp.astimezone(NY)
                        day = stamp.date()
                        if not u_trading(day) or stamp < session_close(day):
                            continue
                        unit = quote["unit"]
                    collected = datetime.fromisoformat(capture["first_seen"])
                    if stamp > min(now, collected):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                row = dict(quote) | {"market": market, "day": day, "stamp": stamp,
                                    "unit": unit, "capture": capture, "origin": data.get("origin", "daily")}
                key = market, quote["symbol"], day, quote["source"]
                old = rows.get(key)
                if old is None or (stamp, collected, capture["id"]) > (old["stamp"], datetime.fromisoformat(old["capture"]["first_seen"]), old["capture"]["id"]):
                    rows[key] = row
    return list(rows.values())


def _metric(rows: list[dict], market: str, symbol: str, name: str, start: date, end: date) -> dict:
    trading, previous = (a_trading, a_previous) if market == "ashare" else (u_trading, u_previous)
    days = [start + timedelta(days=i) for i in range(5) if trading(start + timedelta(days=i))]
    result = {"market": market, "symbol": symbol, "name": name, "pct": None, "coverage": 0,
              "expected": len(days), "base_date": previous(start).isoformat(), "end_date": days[-1].isoformat() if days else None,
              "source": "", "notes": "本周休市" if not days else "历史不足，周涨跌待确认"}
    if not days:
        return result
    candidates = [row for row in rows if row["market"] == market and row["symbol"] == symbol and row["day"] in days]
    result["coverage"] = len({row["day"] for row in candidates})
    for finish in sorted([row for row in candidates if row["day"] == days[-1]], key=lambda row: (row["stamp"], row["capture"]["first_seen"]), reverse=True):
        bases = [row for row in rows if row["market"] == market and row["symbol"] == symbol
                 and row["day"].isoformat() == result["base_date"] and row["source"] == finish["source"] and row["unit"] == finish["unit"]]
        if not bases:
            continue
        base = max(bases, key=lambda row: row["stamp"])
        result.update(pct=(finish["last"] / base["last"] - 1) * 100, start=base["last"], end=finish["last"],
                      source=finish["source"], unit=finish["unit"], notes="按同源实际收盘计算")
        return result
    return result


def _activity(rows: list[dict], market: str, start: date) -> str:
    symbol = "sh000001" if market == "ashare" else "SPY"
    field = "amount" if market == "ashare" else "volume"
    trading = a_trading if market == "ashare" else u_trading
    current = [start + timedelta(days=i) for i in range(5) if trading(start + timedelta(days=i))]
    prior = [start - timedelta(days=7) + timedelta(days=i) for i in range(5) if trading(start - timedelta(days=7) + timedelta(days=i))]
    if not current:
        return "本周休市，量能不作比较。"
    selected = [row for row in rows if row["market"] == market and row["symbol"] == symbol and row["day"] in current + prior and _number(row.get(field)) is not None and row[field] >= 0]
    for source in sorted({row["source"] for row in selected}):
        values = {row["day"]: row[field] for row in selected if row["source"] == source}
        if all(day in values for day in current):
            mean = sum(values[day] for day in current) / len(current)
            scale, unit, label = (1e8, "亿元", "沪市日均成交额") if market == "ashare" else (1e4, "万股", "SPY日均成交股数")
            line = f"{label} {mean / scale:.1f}{unit}"
            if prior and all(day in values for day in prior):
                delta = mean - sum(values[day] for day in prior) / len(prior)
                line += f"，较上周{'增加' if delta >= 0 else '减少'}{abs(delta) / scale:.1f}{unit}"
            else:
                line += "；上周同源记录不足"
            return line + f" · {source}。"
    return "沪市全周成交额记录不足，量能待确认。" if market == "ashare" else "SPY全周同源股数记录不足，量能待确认。"


def build_weekly(archive: Archive, now: datetime, *, week: date | None = None) -> dict:
    start = week_start(now, week)
    end = start + timedelta(days=4)
    rows = observations(archive, now)
    metrics = [_metric(rows, market, symbol, name, start, end) for market, symbol, name in HERO]
    a, growth, us, tech = metrics
    if a["pct"] is not None and us["pct"] is not None:
        headline = "A股与美股周度方向分化" if a["pct"] * us["pct"] < 0 else "A股与美股本周共同走高" if min(a["pct"], us["pct"]) > 0 else "A股与美股本周共同回落" if max(a["pct"], us["pct"]) < 0 else "跨市场周度表现平淡"
    else:
        headline = "历史数据积累中，先看可核实的变化"
    changes = []
    for group, label in ((metrics[:2], "A股"), (metrics[2:], "美股")):
        known = [f"{item['name']}{fmt_pct(item['pct'])}" for item in group if item["pct"] is not None]
        changes.append(f"{label}：" + ("、".join(known) + "。" if known else "本周休市。" if not group[0]["expected"] else "缺少周末收盘或同源上周基准，暂不计算周涨跌。"))
    evidence = [_activity(rows, "ashare", start), _activity(rows, "usstock", start)]
    if us["pct"] is not None and tech["pct"] is not None:
        difference = tech["pct"] - us["pct"]
        evidence.append(f"纳指较标普500{'领先' if difference >= 0 else '落后'}{abs(difference):.2f}个百分点；指数差异不代表全市场上涨家数。")
    sectors = [_metric(rows, "usstock", symbol, name, start, end) for symbol, name in SECTOR_NAMES.items()]
    available = [item for item in sectors if item["pct"] is not None]
    if len(available) >= 2:
        strong, weak = max(available, key=lambda item: item["pct"]), min(available, key=lambda item: item["pct"])
        evidence = evidence[:2] + [f"可核验的{len(available)}个板块ETF中，{strong['name']}{fmt_pct(strong['pct'])}较强，{weak['name']}{fmt_pct(weak['pct'])}较弱。"]
    news = []
    for report in archive.reports(through=now):
        if report["market"] not in {"ashare", "usstock"}:
            continue
        for item in report["data"].get("news", []):
            stamp = datetime.fromisoformat(item["published"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=CST)
            if start <= stamp.astimezone(NY).date() <= end and stamp <= now:
                news.append(NewsItem(stamp, item["title"], item["source"], item.get("source_score", 1), item.get("rank", 0)))
    focus = build_focus(news, now, market="ashare")[0]
    story = {"title": focus.title if news else "本周重点消息尚未留存", "stamp": focus.stamp}
    completed = [event for event in archive.events(through=now) if start <= datetime.fromisoformat(event["at"]).astimezone(NY).date() <= end
                 and comparisons(event) and not any("待核实" in line for line in comparisons(event))]
    if completed:
        event = completed[-1]
        story = {"title": comparisons(event)[0], "stamp": "本周发布结果 · " + event["source"]}
    next_start, next_end = start + timedelta(days=7), end + timedelta(days=7)
    scheduled = [event for event in archive.events(through=now) if next_start <= datetime.fromisoformat(event["at"]).astimezone(NY).date() <= next_end and datetime.fromisoformat(event["at"]) > now]
    if scheduled:
        event = min(scheduled, key=lambda item: item["at"])
        at = datetime.fromisoformat(event["at"])
        future = {"title": event["title"], "stamp": f"计划 {at.astimezone(CST):%m-%d %H:%M} CST / {at.astimezone(NY):%H:%M %Z} · {event['source']}",
                  "watch": event_context(event["title"])}
    else:
        future = {"title": "下一周重要日程待确认", "stamp": "未留存可核实的下一周未来日程", "watch": "观察主要指数能否延续方向，成交活跃度是否配合。"}
    notes = [f"{item['name']}：留存{item['coverage']}/{item['expected']}个交易日；{item['notes']}" for item in metrics]
    notes += ["周涨跌比较本周最后交易日与上周最后交易日的同源收盘；缺失日不补零、不累加日涨跌。",
              "A股行业接口没有可核验日期，不据此生成行业周涨跌。", "SPY股数为供应商日线代理，不代表美股全市场成交额。"]
    if any(row["origin"] == "backfill" and start - timedelta(days=10) <= row["day"] <= end for row in rows):
        notes.append("部分日线为生成时从公开历史接口补录；不代表当时已留存的新闻、预期或资金快照。")
    return {"kind": "weekly", "generated_at": now.isoformat(), "edition_date": (end + timedelta(days=1)).isoformat(),
            "start": start.isoformat(), "end": end.isoformat(), "headline": headline, "metrics": metrics,
            "changes": changes, "evidence": evidence, "story": story, "future": future, "notes": notes}


def social_copy(brief: dict) -> str:
    day = date.fromisoformat(brief["edition_date"])
    lines = [f"每周精选｜{day.year}年{day.month}月{day.day}日 {weekday_cn(day).replace('周', '星期')}",
             f"交易周：{brief['start']}～{brief['end']}", "", "【本周的变化】", brief["headline"]]
    lines += ["  • " + line for line in brief["changes"]]
    if brief["story"]["stamp"]:
        lines += ["", brief["story"]["title"], "  " + brief["story"]["stamp"]]
    lines += ["", "【行情的证据】"] + ["  • " + line for line in brief["evidence"][:3]]
    lines += ["", "【下周如何验证】", brief["future"]["title"], "  " + brief["future"]["stamp"], brief["future"]["watch"],
              "", "【数据说明】", f"生成 {brief['generated_at']}。"] + brief["notes"] + ["公开行情可能延迟，不构成投资建议。"]
    return "\n".join(lines)
