from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlencode

from brief_common.client import fetch_text
from brief_common.news import em_items, wscn_items

from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, SECTOR_NAMES,
    NY, MarketData, Quote, new_york_time,
)
from .parse import chart_result, parse_completed, parse_extended, parse_overnight, parse_quote

YAHOO = "https://finance.yahoo.com/"
CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def _fetch_chart(symbol: str, *, extended: bool = False) -> dict:
    params = {"range": "2d" if extended else "5d", "interval": "5m" if extended else "1d"}
    if extended:
        params["includePrePost"] = "true"
    error: Exception | None = None
    for host in CHART_HOSTS:
        url = f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}?{urlencode(params)}"
        try:
            payload = json.loads(fetch_text(url, YAHOO, timeout=10, retries=0))
            if chart_result(payload, symbol) is None:
                raise ValueError("行情为空、标的不匹配或接口返回错误")
            return payload
        except (OSError, ValueError, RuntimeError) as exc:
            error = exc
    raise RuntimeError(f"Yahoo 两个行情入口均不可用：{error}") from error


def _daily(symbol: str, name: str, now: datetime) -> tuple[dict, Quote | None, Quote | None]:
    payload = _fetch_chart(symbol)
    return (
        payload,
        parse_quote(payload, symbol, name, now=now),
        parse_completed(payload, symbol, name, now=now),
    )


def _extended(symbol: str, name: str, now: datetime, daily_payload: dict | None) -> tuple[Quote | None, Quote | None]:
    payload = _fetch_chart(symbol, extended=True)
    return (
        parse_extended(payload, symbol, name, "premarket", now=now, daily_payload=daily_payload),
        parse_extended(payload, symbol, name, "postmarket", now=now, daily_payload=daily_payload),
    )


def _overnight(symbol: str, name: str, now: datetime) -> Quote | None:
    html = fetch_text(f"{YAHOO}quote/{quote(symbol, safe='')}/", YAHOO, timeout=12, retries=0)
    return parse_overnight(html, symbol, name, now=now)


def load_market(*, now: datetime | None = None) -> MarketData:
    """Capture all instruments against one New York timestamp; failures stay local."""
    now = new_york_time(now or datetime.now(NY))
    result = MarketData()
    names = {**INDEX_NAMES, **FUTURE_NAMES, **MEGA_NAMES, **SECTOR_NAMES, **MACRO_NAMES, "SPY": "SPY成交量"}
    extended_names = {**MEGA_NAMES, **SECTOR_NAMES}
    payloads: dict[str, dict] = {}
    # Requests are parallel but accumulated in fixed symbol order for reproducible output.
    with ThreadPoolExecutor(max_workers=8) as pool:
        daily_tasks = {}
        overnight_tasks = {}
        for symbol, name in names.items():
            daily_tasks[symbol] = pool.submit(_daily, symbol, name, now)
            if symbol in extended_names:
                # Public night quotes do not depend on the chart endpoint succeeding.
                overnight_tasks[symbol] = pool.submit(_overnight, symbol, name, now)
        news_tasks = (
            ("见闻美股快讯", pool.submit(wscn_items, "us-stock-channel", 2)),
            ("见闻全球快讯", pool.submit(wscn_items, "global-channel", 2)),
            ("东财国际快讯", pool.submit(em_items, "111")),
        )
        for symbol, future in daily_tasks.items():
            try:
                payload, latest, completed = future.result()
                payloads[symbol] = payload
                if latest is not None:
                    result.quotes[symbol] = latest
                else:
                    result.notes.append(f"{names[symbol]}（{symbol}）最新行情暂缺或时间无效")
                if completed is not None:
                    result.completed[symbol] = completed
                if completed is not None and completed.pct is None:
                    result.notes.append(f"{names[symbol]}（{symbol}）上一交易日收盘基准暂缺")
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                result.notes.append(f"{names[symbol]}（{symbol}）行情暂缺：{exc}")
        for label, future in news_tasks:
            try:
                result.news.extend(future.result())
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                result.notes.append(f"{label}暂缺：{exc}")
        extended_tasks = {
            symbol: pool.submit(_extended, symbol, name, now, payloads.get(symbol))
            for symbol, name in extended_names.items()
        }
        for symbol, future in extended_tasks.items():
            try:
                premarket, postmarket = future.result()
                if premarket is not None:
                    result.premarket[symbol] = premarket
                if postmarket is not None:
                    result.postmarket[symbol] = postmarket
                if premarket is None and postmarket is None:
                    result.notes.append(f"{extended_names[symbol]}（{symbol}）盘前/盘后真实交易数据暂缺")
                elif any(item is not None and item.pct is None for item in (premarket, postmarket)):
                    result.notes.append(f"{extended_names[symbol]}（{symbol}）延长时段涨跌基准暂缺")
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                result.notes.append(f"{extended_names[symbol]}（{symbol}）盘前/盘后行情暂缺：{exc}")
        for symbol, future in overnight_tasks.items():
            try:
                overnight = future.result()
                if overnight is not None:
                    result.overnight[symbol] = overnight
                    if overnight.pct is None:
                        result.notes.append(f"{extended_names[symbol]}（{symbol}）夜盘常规收盘基准暂缺")
                else:
                    result.notes.append(f"{extended_names[symbol]}（{symbol}）本版真实夜盘行情暂缺")
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                result.notes.append(f"{extended_names[symbol]}（{symbol}）夜盘行情暂缺：{exc}")
    if not result.news:
        result.notes.append("美股快讯暂缺")
    if any(symbol in result.quotes for symbol in (*FUTURE_NAMES, "GC=F", "CL=F", "DX-Y.NYB")):
        result.notes.append("期货、商品和美元指数涨跌采用 Yahoo 供应商参考基准；不是美股常规场昨收基准")
    return result
