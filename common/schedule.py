"""按触发这次运行的 cron 选择版面，而不是按任务真正开始的钟点。

GitHub 可能把 08:00 的任务推迟到 09:01 才启动。启动时刻的小时已经不是 8，
但这次 cron 仍然应该生成它预定的那一版。美股各有两条 cron，分别对准夏令时和
标准时的当地 08:00 / 16:30；另一条的定时点不是这个当地时间，直接跳过。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from ashare.calendar import is_trading_day as a_trading
from ashare.models import CST
from usstock.calendar import is_trading_day as u_trading
from usstock.models import NY

DELAY = timedelta(hours=3)
EARLY = timedelta(minutes=2)

# cron、命令、当地时区、这条 cron 必须对准的当地时间、交易日历。
SLOTS = (
    ("0 0 * * 1-5", ("ashare", ["morning"]), CST, time(8, 0), "ashare"),
    ("30 7 * * 1-5", ("ashare", ["close"]), CST, time(15, 30), "ashare"),
    ("0 12 * * 1-5", ("usstock", ["premarket"]), NY, time(8, 0), "usstock"),
    ("0 13 * * 1-5", ("usstock", ["premarket"]), NY, time(8, 0), "usstock"),
    ("30 20 * * 1-5", ("usstock", ["postmarket"]), NY, time(16, 30), "usstock"),
    ("30 21 * * 1-5", ("usstock", ["postmarket"]), NY, time(16, 30), "usstock"),
    ("15 2 * * 6", ("weekly", ["--backfill"]), CST, time(10, 15), ""),
)


def _normalize(schedule: str) -> str:
    return " ".join(schedule.split())


def _weekdays(cron: str) -> set[int]:
    """GitHub cron 的星期（0/7 为周日）换成 Python 的周一=0。"""
    raw = cron.split()[-1]
    days: set[int] = set()
    for piece in raw.split(","):
        if "-" in piece:
            start, end = (int(item) for item in piece.split("-", 1))
            days.update(range(start, end + 1))
        else:
            days.add(int(piece))
    mapped: set[int] = set()
    for day in days:
        if day == 7:
            day = 0
        mapped.add((day - 1) % 7)
    return mapped


def _recent_slot(now: datetime, cron: str) -> datetime | None:
    minute, hour = (int(part) for part in cron.split()[:2])
    weekdays = _weekdays(cron)
    now = now.astimezone(timezone.utc)
    found = None
    for delta in range(3):
        day = now.date() - timedelta(days=delta)
        if day.weekday() not in weekdays:
            continue
        slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
        delay = now - slot
        if -EARLY <= delay <= DELAY and (found is None or slot > found):
            found = slot
    return found


def scheduled_jobs(now: datetime, schedule: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("定时判断需要带时区的时钟")
    schedule = _normalize(schedule)
    match = next((slot for slot in SLOTS if slot[0] == schedule), None)
    if match is None:
        return [], [f"跳过：无法识别的定时 {schedule or '（空）'}"]
    cron, job, zone, clock, calendar = match
    slot = _recent_slot(now, cron)
    if slot is None:
        return [], [f"跳过：{cron} 不在定时后{int(DELAY.total_seconds() // 3600)}小时内，避免用启动时刻猜版面"]
    local = slot.astimezone(zone)
    if local.time().replace(tzinfo=None) != clock:
        return [], [f"跳过：{cron} 在当地是 {local:%H:%M %Z}，不是 {clock:%H:%M}（另一套夏令时定时）"]
    if calendar == "ashare" and not a_trading(local.date()):
        return [], [f"跳过：{local.date().isoformat()} 不是A股交易日"]
    if calendar == "usstock" and not u_trading(local.date()):
        return [], [f"跳过：{local.date().isoformat()} 不是NYSE交易日"]
    market, arguments = job
    return [job], [f"生成：{market} {' '.join(arguments)}（定时 {cron}，当地 {local:%Y-%m-%d %H:%M %Z}）"]
