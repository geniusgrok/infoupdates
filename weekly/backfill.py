from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from urllib.parse import quote as url_quote, urlencode

from ashare.calendar import is_trading_day as a_trading
from ashare.models import CST, Quote as AQuote
from common.archive import Archive, plain
from common.http import fetch_text
from usstock.models import NY, SECTOR_NAMES
from usstock.sources import cboe, nasdaq, yahoo


def parse_ashare(payload, symbol: str, name: str, now: datetime, *, source="搜狐日线") -> list[AQuote]:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict) or payload[0].get("code") != "zs_" + symbol[2:]:
        raise ValueError("A股历史日线标的不匹配")
    rows = payload[0].get("hq")
    if payload[0].get("status") != 0 or not isinstance(rows, list):
        raise ValueError("A股历史日线不可用")
    result = []
    for row in rows:
        try:
            day = date.fromisoformat(row[0])
            if not a_trading(day) or datetime.combine(day, time(15), CST) > now:
                continue
            close = yahoo.finite_number(row[2], positive=True)
            amount = yahoo.finite_number(row[8])
            if close is not None:
                result.append(AQuote(symbol, name, close, trade_day=day.isoformat(), session="15:00:00", source=source,
                                     amount=amount * 1e4 if amount is not None and amount >= 0 else None))
        except (ValueError, TypeError, IndexError):
            continue
    return result


def _ashare(symbol: str, name: str, start: date, end: date, now: datetime) -> list:
    errors = []
    try:
        url = "https://q.stock.sohu.com/hisHq?" + urlencode({"code": "zs_" + symbol[2:], "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"), "stat": 1, "order": "D", "period": "d"})
        quotes = parse_ashare(json.loads(fetch_text(url, "https://q.stock.sohu.com/", encoding="gb18030", timeout=10, retries=0)), symbol, name, now)
        if quotes:
            return quotes
    except (OSError, ValueError) as exc:
        errors.append(type(exc).__name__)
    try:
        secid = ("1." if symbol.startswith("sh") else "0.") + symbol[2:]
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode({
            "secid": secid, "klt": 101, "fqt": 0, "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
        payload = json.loads(fetch_text(url, "https://quote.eastmoney.com/", timeout=10, retries=0))
        data = payload.get("data") or {}
        if data.get("code") != symbol[2:] or data.get("market") != (1 if symbol.startswith("sh") else 0):
            raise ValueError("东财历史标的不匹配")
        result = []
        for line in data.get("klines") or []:
            row = line.split(",")
            day, price, amount = date.fromisoformat(row[0]), yahoo.finite_number(row[2], positive=True), yahoo.finite_number(row[6])
            if a_trading(day) and datetime.combine(day, time(15), CST) <= now and price is not None:
                result.append(AQuote(symbol, name, price, trade_day=day.isoformat(), session="15:00:00", source="东财日线",
                                     amount=amount if amount is not None and amount >= 0 else None))
        if result:
            return result
    except (OSError, ValueError, TypeError, IndexError) as exc:
        errors.append(type(exc).__name__)
    raise ValueError(f"{name}历史来源不可用（{'、'.join(errors) or '空数据'}）")


def _usstock(symbol: str, name: str, start: date, end: date, now: datetime) -> list:
    params = urlencode({"period1": int(datetime.combine(start, time.min, NY).timestamp()),
                        "period2": int(datetime.combine(end + timedelta(days=1), time.min, NY).timestamp()), "interval": "1d"})
    for host in yahoo.CHART_HOSTS:
        try:
            url = f"https://{host}/v8/finance/chart/{url_quote(symbol, safe='')}?{params}"
            payload = json.loads(fetch_text(url, yahoo.YAHOO, timeout=10, retries=0))
            if yahoo.chart_result(payload, symbol) is None:
                continue
            result = [quote for day, quote in yahoo.parse_history(payload, symbol, name, now=now).items() if start <= day <= end]
            if result:
                return result
        except (OSError, ValueError, RuntimeError):
            continue
    if symbol in nasdaq.ASSET_CLASSES or symbol == "^IXIC":
        code, assetclass = ("COMP", "index") if symbol == "^IXIC" else (symbol, nasdaq.ASSET_CLASSES[symbol])
        params = urlencode({"assetclass": assetclass, "fromdate": start.isoformat(),
                            "todate": end.isoformat(), "limit": 100})
        raw = json.loads(fetch_text(f"{nasdaq.BASE_URL}/{code}/historical?{params}", nasdaq.REFERER, timeout=10, retries=0))
        return [replace(quote, symbol=symbol, unit="点", source="Nasdaq指数日线") if symbol == "^IXIC" else quote
                for day, quote in nasdaq.parse_history(raw, code, name, now).items() if start <= day <= end]
    if symbol in cboe.SYMBOLS:
        raw = json.loads(fetch_text(f"{cboe.BASE_URL}/charts/historical/_{cboe.SYMBOLS[symbol]}.json", cboe.REFERER, timeout=10, retries=0))
        return [quote for day, quote in cboe.parse_history(raw, symbol, name, now).items() if start <= day <= end]
    raise ValueError(f"{name}历史来源不可用")


def backfill(archive: Archive, week: date, now: datetime) -> list[str]:
    # 仅补日线：采集时间仍为现在，不能补造过去的新闻或事前预期。
    start, end = week - timedelta(days=14), week + timedelta(days=4)
    jobs = [("ashare", "sh000001", "上证指数"), ("ashare", "sz399006", "创业板指"),
            ("usstock", "^GSPC", "标普500"), ("usstock", "^IXIC", "纳斯达克"),
            ("usstock", "SPY", "标普500ETF")] + [("usstock", symbol, name) for symbol, name in SECTOR_NAMES.items()]
    messages = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [(market, symbol, name, pool.submit(_ashare if market == "ashare" else _usstock, symbol, name, start, end, now))
                   for market, symbol, name in jobs]
        for market, symbol, name, future in futures:
            try:
                quotes = future.result()
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                messages.append(f"{name}历史补录暂缺：{exc}")
                continue
            for quote in quotes:
                row = plain(quote)
                # observed_at 是请求时点，单独由归档记录；不参与历史日线的幂等键。
                if market == "usstock":
                    row["observed_at"] = None
                data = {"origin": "backfill", "indices": [row]} if market == "ashare" else {"origin": "backfill", "completed": {symbol: row}}
                archive.capture(market, data, now)
            messages.append(f"{name}：补录{len(quotes)}个实际收盘日")
    return messages
