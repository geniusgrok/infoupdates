from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from common import cache


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.now = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)

    def write(self, data, **options):
        return cache.write("quotes/SPY", data, now=self.now, cache_dir=self.path, **options)

    def read(self, **options):
        return cache.read("quotes/SPY", max_age=300, now=self.now, cache_dir=self.path, **options)

    def test_preserves_source_timestamps_and_unicode_without_refreshing_cache_age(self):
        data = {"source": "夜盘", "observed_at": "2026-10-01T03:59:00+00:00", "price": 12.5}
        self.assertTrue(self.write(data))
        self.assertEqual(self.read(), data)
        self.assertEqual(cache.read("quotes/SPY", max_age=300, now=self.now + timedelta(minutes=5), cache_dir=self.path), data)
        self.assertIsNone(cache.read("quotes/SPY", max_age=300, now=self.now + timedelta(seconds=301), cache_dir=self.path))

    def test_future_timestamp_or_bad_clock_is_a_miss(self):
        self.assertTrue(self.write({"price": 1}))
        self.assertIsNone(cache.read("quotes/SPY", max_age=300, now=self.now - timedelta(seconds=1), cache_dir=self.path))
        self.assertIsNone(cache.read("quotes/SPY", max_age=300, now=self.now.replace(tzinfo=None), cache_dir=self.path))
        self.assertFalse(cache.write("quotes/SPY", {}, now=self.now.replace(tzinfo=None), cache_dir=self.path))

    def test_corrupt_invalid_and_missing_entries_are_misses(self):
        self.assertIsNone(self.read())
        self.assertTrue(self.write({"price": 1}))
        file = next(self.path.glob("*.json"))
        for content in ("{", "[]", "{}", json.dumps({"saved_at": self.now.isoformat(), "data": []}),
                        json.dumps({"saved_at": "invalid", "data": {}}),
                        '{"saved_at":"2026-10-01T04:00:00+00:00","data":{"price":NaN}}'):
            with self.subTest(content=content):
                file.write_text(content, encoding="utf-8")
                self.assertIsNone(self.read())

    def test_invalid_age_and_non_json_values_do_not_break_the_caller(self):
        self.assertTrue(self.write({"price": 1}))
        for age in (-1, float("inf"), float("nan")):
            with self.subTest(age=age):
                self.assertIsNone(cache.read("quotes/SPY", max_age=age, now=self.now, cache_dir=self.path))
        for data in ([], {"price": float("nan")}, {"value": object()}):
            with self.subTest(data=data):
                self.assertFalse(self.write(data))
        self.assertEqual(self.read(), {"price": 1})

    def test_failed_replacement_preserves_the_last_valid_entry(self):
        self.assertTrue(self.write({"price": 1}))
        with patch("common.cache.os.replace", side_effect=OSError("disk unavailable")):
            self.assertFalse(self.write({"price": 2}))
        self.assertEqual(self.read(), {"price": 1})

    def test_disk_errors_are_best_effort_and_environment_selects_the_directory(self):
        blocked = self.path / "not-a-directory"
        blocked.write_text("file", encoding="utf-8")
        self.assertFalse(cache.write("quotes", {}, cache_dir=blocked, now=self.now))
        self.assertIsNone(cache.read("quotes", max_age=300, cache_dir=blocked, now=self.now))
        with patch.dict("os.environ", {"BRIEF_CACHE_DIR": str(self.path / "environment")}):
            self.assertTrue(cache.write("quotes", {"price": 1}, now=self.now))
            self.assertEqual(cache.read("quotes", max_age=300, now=self.now), {"price": 1})

    def test_parallel_readers_only_observe_complete_json_records(self):
        self.assertTrue(self.write({"id": -1, "message": "x" * 2000}))

        def replace_and_read(index):
            self.assertTrue(self.write({"id": index, "message": "x" * 2000}))
            data = self.read()
            self.assertIsNotNone(data)
            self.assertEqual(data["message"], "x" * 2000)
            self.assertIsInstance(data["id"], int)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(replace_and_read, range(40)))


if __name__ == "__main__":
    unittest.main()
