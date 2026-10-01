from __future__ import annotations

from datetime import date, datetime, time, timedelta

# 上交所年度休市通知；调休的周末仍不开市。
# 2025: https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml
# 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
HOLIDAY_RANGES = {
    2025: (("01-01", "01-01"), ("01-28", "02-04"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("05-31", "06-02"), ("10-01", "10-08")),
    2026: (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07")),
}


def is_trading_day(day: date) -> bool:
    if day.year not in HOLIDAY_RANGES:
        raise ValueError(f"交易日历尚未覆盖{day.year}年，请按交易所公告更新休市安排")
    month_day = day.strftime("%m-%d")
    return day.weekday() < 5 and not any(start <= month_day <= end for start, end in HOLIDAY_RANGES[day.year])


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


def edition_date(kind: str, now: datetime, trade_date: date) -> date:
    """Use the quote day for close editions and the upcoming A-share session for mornings."""
    if kind != "morning":
        return trade_date
    if is_trading_day(now.date()) and now.time() < time(15, 0):
        return now.date()
    return next_trading_day(now.date())


def latest_quote_date(now: datetime) -> date:
    """运行前预计的行情日期；生成版面仍以实际报价日期为准。"""
    if is_trading_day(now.date()) and now.time() >= time(9, 30):
        return now.date()
    return previous_trading_day(now.date())
