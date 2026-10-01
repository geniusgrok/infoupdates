from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from common.archive import Archive
from common.events import CalendarEvent
from review.bls import calculate, collect_releases, values
from review.events import comparisons
from usstock.models import NY


def response(series, rows):
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": series, "data": [
        {"year": period[:4], "period": "M" + period[-2:], "value": str(value)} for period, value in rows]}]}}


class BlsResultsTests(unittest.TestCase):
    def test_nonfarm_change_uses_thousands_to_ten_thousands(self):
        points = {"2026-08": 159075, "2026-09": 159225}
        self.assertEqual(calculate(points, "2026-09", "change"), 15)
        self.assertIsNone(calculate(points, "2026-07", "change"))

    def test_inflation_uses_correct_year_or_month_base_and_rounding(self):
        points = {"2025-09": 100, "2026-08": 102.4, "2026-09": 102.5}
        self.assertEqual(calculate(points, "2026-09", "year"), 2.5)
        self.assertEqual(calculate(points, "2026-09", "month"), .1)
        self.assertIsNone(calculate({"2026-09": 102.5}, "2026-09", "year"))

    def test_series_identity_annual_period_and_failure_are_validated(self):
        payload = response("CES0000000001", [("2026-09", 159225)])
        payload["Results"]["series"][0]["data"].append({"year": "2026", "period": "M13", "value": "1"})
        self.assertEqual(values(payload, "CES0000000001"), {"2026-09": 159225})
        self.assertEqual(values(payload, "LNS14000000"), {})
        self.assertEqual(values({"status": "REQUEST_NOT_PROCESSED"}, "CES0000000001"), {})

    def test_collect_after_release_uses_observed_clock_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
            archive.add_event(CalendarEvent("美国非农就业与失业率", at, "美国劳工统计局"), at - timedelta(days=1))
            def fetch(url, *args, **kwargs):
                series = url.split("data/", 1)[1].split("?", 1)[0]
                rows = [("2026-08", 159075), ("2026-09", 159225)] if series == "CES0000000001" else [("2026-09", 4.2)]
                return json.dumps(response(series, rows))
            with patch("review.bls.fetch_text", side_effect=fetch) as request:
                collect_releases(archive, at - timedelta(minutes=1))
                request.assert_not_called()
                observed = at + timedelta(hours=2)
                collect_releases(archive, observed)
                collect_releases(archive, observed + timedelta(hours=1))
                self.assertEqual(request.call_count, 2)
            event = archive.events()[0]
            self.assertEqual(len(event["evidence"]), 2)
            self.assertTrue(all(item["data"]["source_published_at"] is None for item in event["evidence"]))
            self.assertTrue(any("15万人" in line and "官方API采集版本" in line for line in comparisons(event)))

    def test_network_failure_or_not_updated_leaves_result_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
            archive.add_event(CalendarEvent("美国非农就业与失业率", at, "美国劳工统计局"), at - timedelta(days=1))
            for payload in (OSError("offline"), json.dumps(response("CES0000000001", [("2026-08", 159075)]))):
                with patch("review.bls.fetch_text", side_effect=payload if isinstance(payload, Exception) else None, return_value=payload):
                    collect_releases(archive, at + timedelta(hours=2))
                self.assertEqual(archive.events()[0]["evidence"], [])


if __name__ == "__main__":
    unittest.main()
