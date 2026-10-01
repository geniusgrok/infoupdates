from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache

from .models import NY, new_york_time

# NYSE 官方日历： https://www.nyse.com/markets/hours-calendars
# 2024/25 NYSE原始公告转载： https://www.nasdaq.com/press-release/nyse-group-announces-2024-2025-and-2026-holiday-and-early-closings-calendar-2023-11
# 2025 悼念卡特停市： https://ir.theice.com/press/news-details/2024/NYSE-to-Close-Markets-on-January-9-Honoring-the-Passing-of-Former-President-Jimmy-Carter/default.aspx
# 常规场按美东时区计算；休市及半日安排仅对已核对的年度生效。
SUPPORTED_YEARS = frozenset(range(2024, 2029))
EARLY_CLOSE_DAYS = {
    2024: frozenset({date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)}),
    2025: frozenset({date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}),
    2026: frozenset({date(2026, 11, 27), date(2026, 12, 24)}),
    2027: frozenset({date(2027, 11, 26)}),
    2028: frozenset({date(2028, 7, 3), date(2028, 11, 24)}),
}


def _validate_year(year: int) -> None:
    if year not in SUPPORTED_YEARS:
        raise ValueError(f"美股交易日历尚未覆盖{year}年，请按 NYSE 公告更新休市和半日安排")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter(year: int) -> date:
    # Gregorian computus; Good Friday is two days before Easter Sunday.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=8)
def holidays(year: int) -> frozenset[date]:
    _validate_year(year)
    new_year = date(year, 1, 1)
    # NYSE 不因次年元旦落在周六而在12月31日补休。
    days = {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2),
        _nth_weekday(year, 5, 0, 5),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if new_year.weekday() != 5:
        days.add(new_year + timedelta(days=1) if new_year.weekday() == 6 else new_year)
    # 五月的第五个周一可能进入六月，改为最后一个周一。
    if (memorial := _nth_weekday(year, 5, 0, 5)).month != 5:
        days.remove(memorial)
        days.add(memorial - timedelta(days=7))
    if year == 2025:
        days.add(date(2025, 1, 9))
    return frozenset(days)


def is_trading_day(day: date) -> bool:
    _validate_year(day.year)
    return day.weekday() < 5 and day not in holidays(day.year)


def next_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day


def previous_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def session_open(day: date) -> datetime:
    if not is_trading_day(day):
        raise ValueError(f"{day.isoformat()}不是 NYSE 交易日")
    return datetime.combine(day, time(9, 30), tzinfo=NY)


def session_close(day: date) -> datetime:
    if not is_trading_day(day):
        raise ValueError(f"{day.isoformat()}不是 NYSE 交易日")
    hour = 13 if day in EARLY_CLOSE_DAYS[day.year] else 16
    return datetime.combine(day, time(hour), tzinfo=NY)


def extended_close(day: date) -> datetime:
    if not is_trading_day(day):
        raise ValueError(f"{day.isoformat()}不是 NYSE 交易日")
    hour = 17 if day in EARLY_CLOSE_DAYS[day.year] else 20
    return datetime.combine(day, time(hour), tzinfo=NY)


def last_completed_session(now: datetime) -> date:
    now = new_york_time(now)
    if is_trading_day(now.date()) and now >= session_close(now.date()):
        return now.date()
    return previous_trading_day(now.date())


def edition_date(kind: str, now: datetime) -> date:
    now = new_york_time(now)
    if kind == "premarket":
        if is_trading_day(now.date()) and now < session_open(now.date()):
            return now.date()
        return next_trading_day(now.date())
    if kind == "postmarket":
        return last_completed_session(now)
    raise ValueError("kind 只能是 premarket 或 postmarket")
