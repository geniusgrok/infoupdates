from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from common import cache
from usstock.cache import apply_cache
from usstock.models import NY, MarketData, Quote


class MarketCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        environment = patch.dict("os.environ", {"BRIEF_CACHE_DIR": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        self.now = datetime(2026, 9, 30, 17, tzinfo=NY)
        self.quote = Quote(
            "AAPL", "苹果", 333.02, 1.1, 329.4, datetime(2026, 9, 30, 16, tzinfo=NY),
            volume=60000, previous_volume=50000, previous_date=date(2026, 9, 29),
            source="Futu", observed_at=self.now,
        )

    def test_fallback_preserves_complete_same_source_quote_and_original_clocks(self):
        apply_cache(MarketData(completed={"AAPL": self.quote}), self.now)
        data = apply_cache(MarketData(), datetime(2026, 10, 1, 8, tzinfo=NY))
        recovered = data.completed["AAPL"]
        self.assertEqual(recovered, replace(self.quote, cached=True))
        self.assertTrue(any("缓存收盘" in note and "09-30 16:00" in note for note in data.notes))
        self.assertFalse(data.news)

    def test_live_quote_wins_without_cross_source_field_merge(self):
        apply_cache(MarketData(completed={"AAPL": self.quote}), self.now)
        live = replace(self.quote, last=334, previous_close=None, volume=None, source="Yahoo Finance")
        data = apply_cache(MarketData(completed={"AAPL": live}), self.now + timedelta(minutes=1))
        self.assertIs(data.completed["AAPL"], live)
        self.assertIsNone(live.previous_close)
        self.assertIsNone(live.volume)
        self.assertFalse(data.notes)

    def test_using_cache_does_not_extend_its_ttl(self):
        apply_cache(MarketData(quotes={"AAPL": self.quote}), self.now)
        data = apply_cache(MarketData(), self.now + timedelta(minutes=14))
        self.assertIn("AAPL", data.quotes)
        apply_cache(data, self.now + timedelta(minutes=14, seconds=30))
        self.assertFalse(apply_cache(MarketData(), self.now + timedelta(minutes=16)).quotes)

    def test_night_snapshot_is_short_lived_and_keeps_observed_time_distinct(self):
        now = datetime(2026, 10, 1, 0, 30, tzinfo=NY)
        quote = replace(
            self.quote, session="overnight", asof=None, observed_at=now,
            source="Webull", previous_date=date(2026, 9, 30),
        )
        apply_cache(MarketData(overnight={"AAPL": quote}), now)
        data = apply_cache(MarketData(), now + timedelta(minutes=4))
        self.assertIsNone(data.overnight["AAPL"].asof)
        self.assertEqual(data.overnight["AAPL"].observed_at, now)
        self.assertTrue(any("缓存夜盘" in note and "采集时间" in note for note in data.notes))
        self.assertFalse(apply_cache(MarketData(), now + timedelta(minutes=5, seconds=1)).overnight)

    def test_night_cache_is_rejected_after_the_night_session_ends(self):
        now = datetime(2026, 10, 1, 3, 59, tzinfo=NY)
        quote = replace(self.quote, session="overnight", asof=now, observed_at=now)
        apply_cache(MarketData(overnight={"AAPL": quote}), now)
        self.assertFalse(apply_cache(MarketData(), datetime(2026, 10, 1, 4, tzinfo=NY)).overnight)

    def test_delayed_cache_write_does_not_renew_a_night_snapshot_observation(self):
        observed = datetime(2026, 10, 1, 0, 20, tzinfo=NY)
        saved = observed + timedelta(minutes=2)
        quote = replace(self.quote, session="overnight", source="Webull", asof=None, observed_at=observed)
        apply_cache(MarketData(overnight={"AAPL": quote}), saved)
        # The cache file is four minutes old, but its night observation is six minutes old.
        data = apply_cache(MarketData(), observed + timedelta(minutes=6))
        self.assertFalse(data.overnight)
        self.assertFalse(data.notes)

    def test_premarket_cache_cannot_be_used_after_regular_open(self):
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        quote = replace(self.quote, session="premarket", asof=now, observed_at=now)
        apply_cache(MarketData(premarket={"AAPL": quote}), now)
        self.assertIn("AAPL", apply_cache(MarketData(), now + timedelta(minutes=1)).premarket)
        self.assertFalse(apply_cache(MarketData(), datetime(2026, 10, 1, 9, 30, tzinfo=NY)).premarket)

    def test_postmarket_actual_quote_age_is_not_hidden_by_recent_cache_write(self):
        stamp = datetime(2026, 9, 30, 19, 30, tzinfo=NY)
        saved = datetime(2026, 10, 1, 7, tzinfo=NY)
        quote = replace(self.quote, session="postmarket", asof=stamp, observed_at=saved)
        apply_cache(MarketData(postmarket={"AAPL": quote}), saved)
        self.assertIn("AAPL", apply_cache(MarketData(), saved + timedelta(minutes=1)).postmarket)
        self.assertFalse(apply_cache(MarketData(), datetime(2026, 10, 1, 12, tzinfo=NY)).postmarket)

    def test_completed_cache_crosses_weekend_but_never_replaces_a_newer_completed_session(self):
        friday = datetime(2026, 10, 2, 16, tzinfo=NY)
        quote = replace(self.quote, asof=friday, observed_at=friday)
        apply_cache(MarketData(completed={"AAPL": quote}), friday)
        self.assertIn("AAPL", apply_cache(MarketData(), datetime(2026, 10, 5, 8, tzinfo=NY)).completed)
        self.assertFalse(apply_cache(MarketData(), datetime(2026, 10, 5, 16, tzinfo=NY)).completed)

    def test_malformed_records_and_unknown_symbols_do_not_break_the_report(self):
        record = asdict(self.quote)
        for field in ("asof", "observed_at", "previous_date"):
            record[field] = record[field].isoformat()
        changes = (
            {"extra": 1}, {"symbol": "MSFT"}, {"volume": -1}, {"last": "333"},
            {"previous_date": "bad-date"}, {"asof": "2026-09-30T16:00:00"},
            {"observed_at": "2026-10-01T16:00:00-04:00"}, {"cached": "yes"},
            {"name": []}, {"delay_minutes": -1},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assertTrue(cache.write("usstock:completed:AAPL", record | change, now=self.now))
                self.assertFalse(apply_cache(MarketData(), self.now).completed)
        self.assertTrue(cache.write("usstock:completed:BTC-USD", record | {"symbol": "BTC-USD"}, now=self.now))
        self.assertNotIn("BTC-USD", apply_cache(MarketData(), self.now).completed)


if __name__ == "__main__":
    unittest.main()
