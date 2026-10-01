from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from math import isfinite
from urllib.parse import urlencode

from common.archive import Archive
from common.http import fetch_text
from usstock.models import NY
from .events import record_metric

BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES = {
    "非农新增就业": ("CES0000000001", "change"), "失业率": ("LNS14000000", "level"),
    "CPI同比": ("CUUR0000SA0", "year"), "CPI环比": ("CUSR0000SA0", "month"),
    "核心CPI同比": ("CUUR0000SA0L1E", "year"), "核心CPI环比": ("CUSR0000SA0L1E", "month"),
    "PPI同比": ("WPUFD4", "year"), "PPI环比": ("WPSFD4", "month"),
}


def values(payload: object, series_id: str) -> dict[str, float]:
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        return {}
    result = payload.get("Results")
    series = result.get("series", []) if isinstance(result, dict) else []
    result = {}
    for item in series:
        if not isinstance(item, dict) or item.get("seriesID") != series_id:
            continue
        for row in item.get("data", []):
            try:
                month = int(row["period"][1:])
                if not row["period"].startswith("M") or not 1 <= month <= 12:
                    continue
                key = f"{int(row['year']):04d}-{month:02d}"
                value = float(row["value"])
                if isfinite(value):
                    if key in result and result[key] != value:
                        raise ValueError("官方时间序列月份重复且数值不一致")
                    result[key] = value
            except (TypeError, KeyError, IndexError):
                continue
    return result


def calculate(points: dict[str, float], period: str, method: str) -> float | None:
    current = points.get(period)
    if current is None:
        return None
    month = datetime.strptime(period, "%Y-%m")
    if method == "level":
        return current
    base = month.replace(year=month.year - 1).strftime("%Y-%m") if method == "year" else (month - timedelta(days=1)).strftime("%Y-%m")
    previous = points.get(base)
    if previous is None:
        return None
    if method == "change":
        return round((current - previous) / 10, 1)  # CES 单位千人，转换为万人。
    if previous <= 0 or method not in {"month", "year"}:
        return None
    return round((current / previous - 1) * 100, 1)


def collect_releases(archive: Archive, now: datetime) -> None:
    """发布后补取官方月度序列；采集版本不冒充首发版本或真实发布时间。"""
    for event in archive.events(through=now):
        at = datetime.fromisoformat(event["at"]).astimezone(NY)
        if not at <= now <= at + timedelta(days=7):
            continue
        names = ("非农新增就业", "失业率") if "就业与失业率" in event["title"] else (
            "CPI同比", "CPI环比", "核心CPI同比", "核心CPI环比") if "CPI" in event["title"] else (
            "PPI同比", "PPI环比") if "PPI" in event["title"] else ()
        recorded = {item["data"].get("metric") for item in event["evidence"] if item["kind"] == "result" and item["data"].get("official")}
        names = [name for name in names if name not in recorded]
        period = (at.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        year = int(period[:4])

        def load(name):
            series_id, method = SERIES[name]
            url = BASE + series_id + "?" + urlencode({"startyear": year - 1, "endyear": year})
            try:
                payload = json.loads(fetch_text(url, "https://www.bls.gov/", timeout=4, retries=0))
                points = values(payload, series_id)
                value = calculate(points, period, method)
                previous_period = (datetime.strptime(period, "%Y-%m") - timedelta(days=1)).strftime("%Y-%m")
                previous = calculate(points, previous_period, method)
                return value, previous, series_id, method, url, payload
            except (OSError, ValueError, TypeError):
                return None, None, series_id, method, url, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(zip(names, pool.map(load, names)))
        for name, (value, previous, series_id, method, url, payload) in results:
            if value is None:
                continue
            data = {"metric": name, "period": period, "unit": "万人" if name == "非农新增就业" else "%",
                    "value": value, "previous": previous, "source": "美国劳工统计局API", "url": url,
                    "official": True, "series_id": series_id, "method": method,
                    "series_payload": payload,
                    "time_basis": "采集时间；接口未披露该数值发布时间", "source_published_at": None}
            record_metric(archive, event["id"], "result", data, now, now)
