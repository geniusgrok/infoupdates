from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from common.archive import Archive, plain

NY = ZoneInfo("America/New_York")
CST = timezone(timedelta(hours=8))
METRICS = {
    "非农新增就业": ("就业与失业率", "万人"), "失业率": ("就业与失业率", "%"),
    "CPI同比": ("CPI", "%"), "CPI环比": ("CPI", "%"),
    "核心CPI同比": ("CPI", "%"), "核心CPI环比": ("CPI", "%"),
    "PPI同比": ("PPI", "%"), "PPI环比": ("PPI", "%"),
    "核心PPI同比": ("PPI", "%"), "核心PPI环比": ("PPI", "%"),
    "JOLTS职位空缺": ("JOLTS", "万个"), "就业成本环比": ("就业成本", "%"),
}
SOURCE_URLS = {"见闻": "https://wallstreetcn.com/", "东财": "https://kuaixun.eastmoney.com/"}
HEADER = re.compile(
    r"美国(?P<year>\d{4}年)?(?P<month>\d{1,2})月(?:季调后)?"
    r"(?P<metric>非农(?:就业人数|就业人口|新增就业|就业)|失业率|"
    r"(?:核心)?(?:CPI|PPI)(?:年率|月率|同比|环比)|JOLTS职位空缺)"
    r"(?P<tail>[^，,；;。]*)"
)
NUMBER = re.compile(r"(?P<value>[+-]?\d+(?:\.\d+)?)(?P<unit>%|万人|千人|万个|万|人)")


def record_metric(archive: Archive, event_id: str, kind: str, data: dict, published_at: datetime, now: datetime) -> str:
    if kind not in {"expectation", "result"}:
        raise ValueError("数值证据必须是预期或结果")
    event = next((item for item in archive.events() if item["id"] == event_id), None)
    if event is None:
        raise ValueError("事件不存在")
    metric, unit = data.get("metric"), data.get("unit")
    if metric not in METRICS or METRICS[metric][0] not in event["title"] or unit != METRICS[metric][1]:
        raise ValueError("指标或单位与事件不匹配")
    if not isinstance(data.get("verified", False), bool):
        raise ValueError("人工核验标记必须是布尔值")
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", data.get("period", "") or ""):
        raise ValueError("必须提供明确统计月份 YYYY-MM，季度指标采用季度最后月份")
    period = datetime.strptime(data["period"], "%Y-%m").date()
    release_month = datetime.fromisoformat(event["at"]).astimezone(NY).date().replace(day=1)
    if period >= release_month:
        raise ValueError("统计月份必须早于发布月份")
    for field in ("value", "previous"):
        value = data.get(field)
        if field == "previous" and value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            raise ValueError("发布数值必须是有限数字")
    source = urlsplit(data.get("url", ""))
    if source.scheme not in {"https", "http"} or not source.hostname or not data.get("source"):
        raise ValueError("必须提供可核验的来源与网页地址")
    return archive.add_evidence(event_id, kind, published_at, data, now)


def _numbers(title: str, event: dict, published: datetime) -> list[tuple[str, dict]]:
    result = []
    at = datetime.fromisoformat(event["at"]).astimezone(NY)
    for match in HEADER.finditer(title):
        name, tail = match["metric"], match["tail"]
        if name.startswith("非农"):
            metric = "非农新增就业"
        else:
            metric = name.replace("年率", "同比").replace("月率", "环比")
        if metric not in METRICS or METRICS[metric][0] not in event["title"]:
            continue
        number = NUMBER.search(tail)
        if number is None:
            continue
        prefix = tail[:number.start()]
        if re.search(r"\d", prefix):
            continue
        leading = title[:match.start()]
        forecast = any(word in prefix + leading for word in ("预期", "预计", "预测"))
        if any(word in leading for word in ("可能", "或将", "如果", "假设", "料将")):
            continue
        if not forecast and any(word in prefix for word in ("可能", "或", "料", "调查", "分析", "前值", "此前")):
            continue
        if not forecast and not any(word in prefix for word in ("为", "录得", "增加", "新增", "增长", "减少", "上涨", "下降", "上升", ":", "：")):
            # 常见快讯格式直接接数值，如“美国9月失业率4.2%”。
            if prefix.strip():
                continue
        month = int(match["month"])
        if not 1 <= month <= 12:
            continue
        year = int(match["year"][:-1]) if match["year"] else at.year - (month > at.month)
        period = f"{year:04d}-{month:02d}"
        prior = (at.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if metric != "JOLTS职位空缺" and period != prior:
            continue
        if metric == "JOLTS职位空缺" and period not in {prior, (at.replace(day=1) - timedelta(days=32)).strftime("%Y-%m")}:
            continue
        value, raw_unit = float(number["value"]), number["unit"]
        unit = METRICS[metric][1]
        if unit == "%" and raw_unit != "%":
            continue
        if unit != "%":
            scales = {"万人": 1, "万": 1, "万个": 1, "千人": .1, "人": .0001}
            if raw_unit not in scales:
                continue
            value *= scales[raw_unit]
            if "减少" in prefix:
                value = -abs(value)
        kind = "expectation" if forecast else "result"
        if (kind == "expectation" and published >= at) or (kind == "result" and published < at):
            continue
        result.append((kind, {"metric": metric, "period": period, "value": value, "unit": unit,
                              "previous": None, "title": title}))
    return result


def track_events(archive: Archive, news: list, events: list, now: datetime) -> None:
    for event in events:
        archive.add_event(event, now)
    for event in archive.events(through=now):
        at = datetime.fromisoformat(event["at"])
        if not at - timedelta(days=3) <= now <= at + timedelta(days=7):
            continue
        terms = ("非农", "失业率") if "就业与失业率" in event["title"] else (
            "CPI",) if "CPI" in event["title"] else ("PPI",) if "PPI" in event["title"] else (
            "JOLTS",) if "JOLTS" in event["title"] else ("就业成本",)
        for item in news:
            item = plain(item)
            published = datetime.fromisoformat(item["published"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=CST)
            if not at - timedelta(days=3) <= published <= min(now, at + timedelta(days=2)):
                continue
            if not any(term in item["title"] for term in terms):
                continue
            data = {"title": item["title"], "source": item["source"], "url": SOURCE_URLS.get(item["source"], "")}
            archive.add_evidence(event["id"], "report", published, data, now)
            if not data["url"]:
                continue
            for kind, numbers in _numbers(item["title"], event, published):
                record_metric(archive, event["id"], kind, numbers | data, published, now)


def comparisons(event: dict) -> list[str]:
    """仅比较同指标、同月份、同单位且发布前已留存的预期。"""
    results = [item for item in event["evidence"] if item["kind"] == "result"]
    forecasts = [item for item in event["evidence"] if item["kind"] == "expectation" and item["recorded_at"] < event["at"]]
    latest = {}
    for item in results:
        data = item["data"]
        latest[(data["metric"], data["period"], data["unit"])] = item
    lines = []
    for key, item in latest.items():
        same = [value["data"] for value in results if tuple(value["data"][field] for field in ("metric", "period", "unit")) == key]
        verified = [value for value in results if value["data"].get("verified") and tuple(value["data"][field] for field in ("metric", "period", "unit")) == key]
        official = [value for value in results if value["data"].get("official") and tuple(value["data"][field] for field in ("metric", "period", "unit")) == key]
        if verified:
            item = verified[-1]
        elif official:
            item = official[-1]
        data = item["data"]
        values = sorted({value["value"] for value in same})
        if len(values) > 1 and not (verified or official):
            alternatives = " / ".join(f"{value:g}{data['unit']}" for value in values)
            lines.append(f"{data['period']} {data['metric']}：发布报道数值不一致（{alternatives}），待核实")
            continue
        matched = [value for value in forecasts if tuple(value["data"][field] for field in ("metric", "period", "unit")) == key]
        line = f"{data['period']} {data['metric']}：{data['value']:g}{data['unit']}"
        if matched:
            expected = matched[-1]["data"]["value"]
            direction = "高于" if data["value"] > expected else "低于" if data["value"] < expected else "等于"
            line += f"，{direction}已留存预期 {expected:g}{data['unit']}（{matched[-1]['data']['source']}）"
        elif data.get("previous") is not None:
            label = "同版前期值" if data.get("official") else "报道所列前值"
            line += f"，{label} {data['previous']:g}{data['unit']}"
        else:
            line += "；未留存可比较的事前预期"
        if verified:
            line += "；采用最新人工核验记录"
        elif official:
            line += "；官方API采集版本"
        lines.append(line + f" · {data['source']}")
    return lines


def reactions(archive: Archive, event: dict, now: datetime) -> list[str]:
    at = datetime.fromisoformat(event["at"])
    quotes = {symbol: [] for symbol in ("^GSPC", "^TNX")}
    for capture in archive.snapshots(through=now):
        if capture["market"] != "usstock":
            continue
        for bag in ("quotes", "completed"):
            for symbol, quote in capture["data"].get(bag, {}).items():
                if symbol not in quotes or not quote.get("asof") or quote.get("cached"):
                    continue
                if quote.get("session") not in {"regular", "reference"} or (symbol == "^TNX" and quote.get("unit") != "%"):
                    continue
                stamp = datetime.fromisoformat(quote["asof"])
                if stamp.tzinfo is None or stamp > now or not at - timedelta(days=2) <= stamp <= at + timedelta(days=2):
                    continue
                if quote.get("last", 0) > 0:
                    quotes[symbol].append((stamp, quote))
    lines = []
    for symbol, rows in quotes.items():
        before = [(stamp, row) for stamp, row in rows if stamp < at]
        after = [(stamp, row) for stamp, row in rows if stamp >= at]
        if not before or not after:
            continue
        start, base = max(before, key=lambda item: item[0])
        same = [(stamp, row) for stamp, row in after if row["source"] == base["source"] and row["unit"] == base["unit"]]
        if not same:
            continue
        end, last = max(same, key=lambda item: item[0])
        change = (last["last"] - base["last"]) * 100 if symbol == "^TNX" and last["unit"] == "%" else (last["last"] / base["last"] - 1) * 100
        unit = "基点" if symbol == "^TNX" and last["unit"] == "%" else "%"
        lines.append(f"{last['name']} {change:+.2f}{unit}（{start.astimezone(NY):%m-%d %H:%M} → {end.astimezone(NY):%m-%d %H:%M} ET，{last['source']}）")
    return lines
