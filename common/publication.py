from __future__ import annotations

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


class InsufficientData(ValueError):
    """核心报价不足，采集保留，本版可重试。"""


def _priced(quote: dict) -> bool:
    value = quote["last"]
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def publication_issue(market: str, data: dict) -> str:
    """对组装后的版面设最低门槛；次要字段缺失不阻止发布。"""
    if market == "ashare":
        if data["kind"] == "morning":
            core = [quote for quote in data["overseas"]
                    if quote["name"] in {"道琼斯", "纳斯达克", "标普500", "日经225"}]
            label, needed = "A股早盘", "美股三大指数或日经指数"
        else:
            core = [quote for quote in data["indices"]
                    if quote["name"] in {"上证指数", "深证成指", "创业板指", "科创50"}
                    and quote["trade_day"] == data["trade_date"]]
            label, needed = "A股收盘", "同参考日的主要A股指数"
    elif market == "usstock":
        if data["kind"] == "premarket":
            core = [quote for quote in data["futures"] + data["stocks"]
                    if quote["session"] in {"futures", "premarket", "overnight", "postmarket"}
                    and (quote["asof"] is not None or quote["observed_at"] is not None)]
            label, needed = "美股盘前", "本版股指期货、最新延长交易个股或明确标注的近期盘后参考"
        else:
            core = data["indices"] if data["reference_date"] == data["edition_date"] else []
            core = [quote for quote in core if quote["session"] == "regular" and quote["asof"] is not None
                    and datetime.fromisoformat(quote["asof"]).astimezone(NY).date().isoformat() == data["edition_date"]]
            label, needed = "美股盘后", "本版已完成常规场的主指数"
    else:
        return ""
    return "" if any(_priced(quote) for quote in core) else f"{label}核心行情不足，需要至少一条{needed}报价"
