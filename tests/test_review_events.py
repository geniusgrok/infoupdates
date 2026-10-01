from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta

from common.archive import Archive
from common.events import CalendarEvent
from common.news import NewsItem
from review.events import comparisons, reactions, record_metric, track_events
from usstock.models import NY, MarketData, Quote


class EventReviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = Archive(self.folder.name)
        self.at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
        self.event = CalendarEvent("美国非农就业与失业率", self.at, "美国劳工统计局")
        self.key = self.archive.add_event(self.event, self.at - timedelta(days=1))

    def metric(self, base_value, **changes):
        return {"metric": "非农新增就业", "period": "2026-09", "unit": "万人", "value": base_value,
                "previous": None, "source": "美国劳工统计局", "url": "https://www.bls.gov/news.release/empsit.nr0.htm"} | changes

    def test_auto_tracks_pre_release_expectation_and_matching_release_idempotently(self):
        before, after = self.at - timedelta(hours=1), self.at + timedelta(minutes=1)
        preview = NewsItem(before, "美国9月非农就业人数预期增加17万人", "见闻")
        released = NewsItem(after, "美国9月非农就业人数增加15万人，预期17万人，前值10万人", "东财")
        for _ in range(2):
            track_events(self.archive, [preview], [self.event], before)
            track_events(self.archive, [released], [], after)
        event = self.archive.events()[0]
        self.assertEqual(len(self.archive.events()), 1)
        self.assertEqual(len(event["evidence"]), 4)
        self.assertIn("低于已留存预期 17万人", comparisons(event)[0])

    def test_forecast_captured_after_release_is_not_a_prior_expectation(self):
        record_metric(self.archive, self.key, "expectation", self.metric(17), self.at - timedelta(hours=1), self.at + timedelta(hours=1))
        record_metric(self.archive, self.key, "result", self.metric(15), self.at, self.at + timedelta(hours=1))
        self.assertIn("未留存可比较的事前预期", comparisons(self.archive.events()[0])[0])

    def test_period_metric_unit_and_source_are_validated(self):
        for changes in ({"period": "bad"}, {"metric": "CPI同比"}, {"unit": "%"}, {"value": float("nan")}, {"url": "javascript:alert(1)"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                record_metric(self.archive, self.key, "result", self.metric(15, **changes), self.at, self.at)
        self.assertEqual(self.archive.events()[0]["evidence"], [])

    def test_different_period_is_not_compared_and_prior_value_remains_attributed(self):
        record_metric(self.archive, self.key, "expectation", self.metric(17, period="2026-08"), self.at - timedelta(hours=1), self.at - timedelta(hours=1))
        record_metric(self.archive, self.key, "result", self.metric(15, previous=10), self.at, self.at)
        self.assertIn("报道所列前值 10万人", comparisons(self.archive.events()[0])[0])
        self.assertNotIn("已留存预期", comparisons(self.archive.events()[0])[0])

    def test_unconfirmed_conditional_stale_or_post_release_forecast_is_not_promoted(self):
        titles = ["如果美国9月非农就业人数增加15万人，股市可能上涨", "美国8月非农就业人数增加15万人",
                  "机构预计美国9月非农就业人数增加15万人", "美国9月非农就业人数可能增加15万人",
                  "美国9月非农就业人数增加15-20万人"]
        now = self.at + timedelta(hours=1)
        track_events(self.archive, [NewsItem(now, title, "见闻") for title in titles], [], now)
        self.assertEqual(comparisons(self.archive.events()[0]), [])
        self.assertTrue(all(item["kind"] == "report" for item in self.archive.events()[0]["evidence"]))

    def test_result_before_release_and_expectation_after_release_fail(self):
        for kind, published in (("result", self.at - timedelta(minutes=1)), ("expectation", self.at)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                record_metric(self.archive, self.key, kind, self.metric(15), published, self.at)

    def test_conflicting_reports_stay_pending_instead_of_selecting_a_number(self):
        record_metric(self.archive, self.key, "result", self.metric(15), self.at, self.at)
        record_metric(self.archive, self.key, "result", self.metric(16, source="见闻", url="https://wallstreetcn.com/"), self.at, self.at)
        line = comparisons(self.archive.events()[0])[0]
        self.assertIn("待核实", line)
        self.assertIn("15万人 / 16万人", line)
        record_metric(self.archive, self.key, "result", self.metric(15, verified=True), self.at, self.at)
        reviewed = comparisons(self.archive.events()[0])[0]
        self.assertIn("采用最新人工核验记录", reviewed)
        self.assertNotIn("待核实", reviewed)
        self.assertEqual(len(self.archive.events()[0]["evidence"]), 3)

    def test_reaction_requires_same_source_and_uses_yield_basis_points(self):
        before, after = self.at - timedelta(hours=16, minutes=30), self.at + timedelta(hours=7, minutes=30)
        for stamp, yield_value, source in ((before, 4, "Yahoo Finance"), (after, 4.03, "Cboe")):
            data = MarketData(quotes={
                "^GSPC": Quote("^GSPC", "标普500", 100, source=source, asof=stamp, unit="点"),
                "^TNX": Quote("^TNX", "10年美债收益率", yield_value, source="Cboe", asof=stamp, unit="%", session="reference"),
            })
            self.archive.capture("usstock", data, stamp)
        lines = reactions(self.archive, self.archive.events()[0], after)
        self.assertEqual(len(lines), 1)
        self.assertIn("+3.00基点", lines[0])
        self.assertIn("ET，Cboe", lines[0])


if __name__ == "__main__":
    unittest.main()
