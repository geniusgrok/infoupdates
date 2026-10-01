from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from common import events

NY = ZoneInfo("America/New_York")


def calendar(*blocks: str) -> str:
    return "BEGIN:VCALENDAR\nVERSION:2.0\n" + "\n".join(
        "BEGIN:VEVENT\n" + block + "\nEND:VEVENT" for block in blocks
    ) + "\nEND:VCALENDAR"


class CalendarTests(unittest.TestCase):
    def test_official_bls_october_job_report_time(self):
        # Same fields and local time as the official BLS 2026 release calendar.
        raw = calendar("DTSTART;TZID=US-Eastern:20261002T083000\nSUMMARY:Employment Situation")
        item = events.next_event(raw, datetime(2026, 10, 1, 12, tzinfo=NY))
        self.assertIsNotNone(item)
        self.assertEqual(item.title, "美国非农就业与失业率")
        self.assertEqual(item.at.astimezone(timezone.utc), datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(item.source, "美国劳工统计局")

    def test_source_timezone_observes_standard_time(self):
        raw = calendar("DTSTART;TZID=America/New_York:20261106T083000\nSUMMARY:Employment Situation")
        item = events.next_event(raw, datetime(2026, 11, 5, 12, tzinfo=NY))
        self.assertEqual(item.at.astimezone(timezone.utc).hour, 13)

    def test_skips_unknown_clocks_cancelled_past_and_distant_events(self):
        now = datetime(2026, 10, 1, 12, tzinfo=NY)
        invalid = [
            "DTSTART:20261002T083000\nSUMMARY:Employment Situation",
            "DTSTART;VALUE=DATE:20261002\nSUMMARY:Employment Situation",
            "DTSTART;TZID=Unknown:20261002T083000\nSUMMARY:Employment Situation",
            "DTSTART;TZID=US-Eastern:bad\nSUMMARY:Employment Situation",
            "DTSTART;TZID=US-Eastern:20261002T083000\nSUMMARY:Employment Situation\nSTATUS:CANCELLED",
            "DTSTART;TZID=US-Eastern:20260930T083000\nSUMMARY:Employment Situation",
            "DTSTART;TZID=US-Eastern:20261014T083000\nSUMMARY:Consumer Price Index",
            "DTSTART;TZID=US-Eastern:20261002T083000\nSUMMARY:Real Earnings",
        ]
        for block in invalid:
            with self.subTest(block=block):
                self.assertIsNone(events.next_event(calendar(block), now))
        self.assertIsNone(events.next_event("<html>server error</html>", now))

    def test_unfolds_titles_and_selects_earliest_future_release(self):
        raw = calendar(
            "DTSTART:20261002T123000Z\nSUMMARY:Employment Situation",
            "DTSTART;TZID=US-Eastern:20261001T140000\nSUMMARY:Job Openings and Labor \n Turnover Survey",
        )
        item = events.next_event(raw, datetime(2026, 10, 1, 12, tzinfo=NY))
        self.assertEqual(item.title, "美国 JOLTS 职位空缺")
        self.assertEqual(item.at.day, 1)

    def test_release_at_the_current_time_is_not_upcoming(self):
        raw = calendar("DTSTART;TZID=US-Eastern:20261002T083000\nSUMMARY:Employment Situation")
        self.assertIsNone(events.next_event(raw, datetime(2026, 10, 2, 8, 30, tzinfo=NY)))

    def test_transport_failures_leave_event_slot_empty(self):
        for failure in (OSError("offline"), TimeoutError("timeout")):
            with self.subTest(failure=failure), patch.object(events, "fetch_text", side_effect=failure):
                self.assertIsNone(events.load_next_event(datetime(2026, 10, 1, 12, tzinfo=NY)))


if __name__ == "__main__":
    unittest.main()
