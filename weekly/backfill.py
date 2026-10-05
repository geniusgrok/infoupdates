from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from ashare.history import load_daily as ashare_daily
from common.archive import Archive, plain
from usstock.history import load_daily as usstock_daily
from usstock.models import SECTOR_NAMES


def backfill(archive: Archive, week: date, now: datetime) -> list[str]:
    # 仅补日线：采集时间仍为现在，不能补造过去的新闻或事前预期。
    start, end = week - timedelta(days=14), week + timedelta(days=4)
    jobs = [("ashare", "sh000001", "上证指数"), ("ashare", "sz399006", "创业板指"),
            ("usstock", "^GSPC", "标普500"), ("usstock", "^IXIC", "纳斯达克"),
            ("usstock", "SPY", "标普500ETF")] + [("usstock", symbol, name) for symbol, name in SECTOR_NAMES.items()]
    messages = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [(market, symbol, name, pool.submit(ashare_daily if market == "ashare" else usstock_daily, symbol, name, start, end, now))
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
