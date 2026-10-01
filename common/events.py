from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .http import fetch_text

BLS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FED_URL = "https://www.federalreserve.gov/json/calendar.json"
NY = ZoneInfo("America/New_York")
TITLES = {
    "Employment Situation": "美国非农就业与失业率",
    "Consumer Price Index": "美国 CPI 通胀数据",
    "Producer Price Index": "美国 PPI 通胀数据",
    "Job Openings and Labor Turnover Survey": "美国 JOLTS 职位空缺",
    "Employment Cost Index": "美国就业成本指数",
}
FOMC_TITLES = {
    "fomc meeting": "美联储 FOMC 利率决议",
    "fomc press conference": "美联储 FOMC 新闻发布会",
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


def fomc_events(calendar: str, now: datetime, *, days: int = 3) -> list[CalendarEvent]:
    """采用美联储日历的发布日期与美东时钟，不推算会议或发布会时间。"""
    if now.utcoffset() is None:
        raise ValueError("event selection requires an aware clock")
    now = now.astimezone(timezone.utc)
    payload = json.loads(calendar.lstrip("\ufeff"))
    rows = payload.get("events", []) if isinstance(payload, dict) else []
    events: list[CalendarEvent] = []
    if not isinstance(rows, list):
        return events
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str):
            continue
        title = FOMC_TITLES.get(row["title"].strip().lower())
        if not title or not all(isinstance(row.get(key), str) for key in ("month", "days", "time")):
            continue
        clock = row["time"].strip().lower().replace("a.m.", "AM").replace("p.m.", "PM")
        try:
            at = datetime.strptime(f'{row["month"]}-{row["days"]} {clock}', "%Y-%m-%d %I:%M %p").replace(tzinfo=NY)
        except ValueError:
            continue
        if now < at <= now + timedelta(days=days):
            events.append(CalendarEvent(title, at, "美联储"))
    return sorted(set(events), key=lambda event: event.at)


def load_events(now: datetime, *, days: int = 3) -> list[CalendarEvent]:
    def load(source):
        url, referer, parse = source
        try:
            return parse(fetch_text(url, referer, timeout=4, retries=0), now, days=days)
        except (OSError, TimeoutError, ValueError):
            return []

    sources = ((BLS_URL, "https://www.bls.gov/schedule/", upcoming_events),
               (FED_URL, "https://www.federalreserve.gov/newsevents/calendar.htm", fomc_events))
    # 两个官方日历独立请求，一方失败仍保留另一方已确认的日程。
    with ThreadPoolExecutor(max_workers=2) as pool:
        events = [event for batch in pool.map(load, sources) for event in batch]
    return sorted(set(events), key=lambda event: event.at)


def load_next_event(now: datetime) -> CalendarEvent | None:
    return next(iter(load_events(now)), None)
