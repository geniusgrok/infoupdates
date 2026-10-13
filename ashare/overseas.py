"""外盘日期按各自市场的最近完成交易日核对，不用北京时间的更新时钟冒充美股收盘日。"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from usstock.calendar import last_completed_session

from .models import Quote

US_NAMES = frozenset({"道琼斯", "纳斯达克", "标普500"})
CORE_NAMES = US_NAMES | {"日经225"}
# 亚洲市场没有单独的休市表，只按当地收盘时刻和周末判断。当地假日可能把上一交易日标成过期。
ASIA = {
    "日经225": (ZoneInfo("Asia/Tokyo"), time(15, 0)),
    "韩国KOSPI": (ZoneInfo("Asia/Seoul"), time(15, 30)),
    "韩国KOSDAQ": (ZoneInfo("Asia/Seoul"), time(15, 30)),
    "恒生指数": (ZoneInfo("Asia/Hong_Kong"), time(16, 0)),
    "恒生科技": (ZoneInfo("Asia/Hong_Kong"), time(16, 0)),
}
_SESSION_DAY = re.compile(r"(\d{2})-(\d{2}) 收盘")


def last_weekday_session(now: datetime, zone: ZoneInfo, close: time) -> date:
    local = now.astimezone(zone)
    day = local.date()
    if day.weekday() >= 5 or local.time() < close:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def expected_session(name: str, now: datetime) -> date | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("外盘日期核对需要带时区的时钟")
    if name in US_NAMES:
        return last_completed_session(now)
    spec = ASIA.get(name)
    if spec is None:
        return None
    return last_weekday_session(now, spec[0], spec[1])


def _with_session_day(quote: Quote, expected: date) -> Quote:
    if quote.trade_day:
        return quote
    match = _SESSION_DAY.fullmatch(quote.session or "")
    if match and (expected.month, expected.day) == (int(match.group(1)), int(match.group(2))):
        return replace(quote, trade_day=expected.isoformat())
    return quote


def session_matches(name: str, trade_day: str, session: str, now: datetime) -> bool:
    expected = expected_session(name, now)
    if expected is None:
        return False
    if not trade_day:
        match = _SESSION_DAY.fullmatch(session or "")
        if match and (expected.month, expected.day) == (int(match.group(1)), int(match.group(2))):
            trade_day = expected.isoformat()
    return trade_day == expected.isoformat()


def align_overseas(quotes: list[Quote], now: datetime) -> tuple[list[Quote], list[str]]:
    """留下日期等于该市场最近收盘日的报价。没有日期、过期或未来的不进入版面。"""
    kept: list[Quote] = []
    dropped: list[str] = []
    for quote in quotes:
        expected = expected_session(quote.name, now)
        if expected is None:
            kept.append(quote)
            continue
        quote = _with_session_day(quote, expected)
        if quote.trade_day == expected.isoformat():
            kept.append(quote)
        elif quote.name not in dropped:
            dropped.append(quote.name)
    return kept, dropped
