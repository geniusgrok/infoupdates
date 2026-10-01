from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .http import fetch_text

URL = "https://www.bls.gov/schedule/news_release/bls.ics"
NY = ZoneInfo("America/New_York")
TITLES = {
    "Employment Situation": "美国非农就业与失业率",
    "Consumer Price Index": "美国 CPI 通胀数据",
    "Producer Price Index": "美国 PPI 通胀数据",
    "Job Openings and Labor Turnover Survey": "美国 JOLTS 职位空缺",
    "Employment Cost Index": "美国就业成本指数",
}


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    at: datetime
    source: str


def upcoming_events(calendar: str, now: datetime, *, days: int = 3) -> list[CalendarEvent]:
    """保留官方日历的明确时区；日报看三天，周报看下一周。"""
    if now.utcoffset() is None:
        raise ValueError("event selection requires an aware clock")
    now = now.astimezone(timezone.utc)
    if "BEGIN:VCALENDAR" not in calendar or "END:VCALENDAR" not in calendar:
        return []
    text = re.sub(r"\r?\n[ \t]", "", calendar)
    events: list[CalendarEvent] = []
    for block in re.findall(r"BEGIN:VEVENT\s*\n(.*?)END:VEVENT", text, re.S):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        title = TITLES.get(fields.get("SUMMARY", ""))
        if not title or fields.get("STATUS") == "CANCELLED":
            continue
        for key, value in fields.items():
            if key == "DTSTART" and re.fullmatch(r"\d{8}T\d{6}Z", value):
                pattern, tz = "%Y%m%dT%H%M%SZ", timezone.utc
            elif key in ("DTSTART;TZID=US-Eastern", "DTSTART;TZID=America/New_York") and re.fullmatch(r"\d{8}T\d{6}", value):
                pattern, tz = "%Y%m%dT%H%M%S", NY
            else:
                continue
            try:
                at = datetime.strptime(value, pattern).replace(tzinfo=tz)
            except ValueError:
                continue
            if now < at <= now + timedelta(days=days):
                events.append(CalendarEvent(title, at, "美国劳工统计局"))
    return sorted(set(events), key=lambda event: event.at)


def next_event(calendar: str, now: datetime) -> CalendarEvent | None:
    return next(iter(upcoming_events(calendar, now)), None)


def load_events(now: datetime, *, days: int = 3) -> list[CalendarEvent]:
    try:
        calendar = fetch_text(URL, "https://www.bls.gov/schedule/", timeout=4, retries=0)
        return upcoming_events(calendar, now, days=days)
    except (OSError, TimeoutError, ValueError):
        return []


def load_next_event(now: datetime) -> CalendarEvent | None:
    return next(iter(load_events(now)), None)
