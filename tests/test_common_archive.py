from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from ashare.compose import build_brief
from ashare.models import CST, MarketData, Quote
from common.archive import Archive
from common.history import build_history


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.archive = Archive(self.root / "archive")
        self.now = datetime(2026, 9, 30, 18, tzinfo=CST)
        self.data = MarketData([Quote("sh000001", "上证指数", 3000, trade_day="2026-09-30", session="15:30:00", source="新浪")])
        self.brief = build_brief("close", self.data, self.now)
        self.stem = self.root / "output" / "close-2026-09-30"

    @staticmethod
    def render(brief, path):
        path.write_bytes(f"{brief.hero.last}@{brief.generated_at.isoformat()}".encode())

    def publish(self, brief=None, data=None, text="正文", render=None):
        brief, data = brief or self.brief, data or self.data
        capture = self.archive.capture("ashare", data, brief.generated_at)
        return self.archive.publish("ashare", brief, capture, render or self.render, text, self.stem)

    def test_identical_retry_has_one_capture_revision_and_original_timestamp(self):
        render = Mock(side_effect=self.render)
        first = self.publish(render=render)
        contents = [path.read_bytes() for path in first]
        later = replace(self.brief, generated_at=self.now + timedelta(minutes=20))
        self.publish(later, text="重试生成时间", render=render)
        self.assertEqual(len(self.archive.snapshots()), 1)
        self.assertEqual(len(self.archive.reports()), 1)
        self.assertEqual([path.read_bytes() for path in first], contents)
        render.assert_called_once()
        record = self.archive.reports()[0]
        saved = json.loads((self.archive.root / record["path"] / "data.json").read_text())
        self.assertEqual(saved["snapshot"]["indices"][0]["source"], "新浪")
        self.assertIn("capture_id", saved)

    def test_changed_input_preserves_versions_and_old_run_cannot_downgrade_output(self):
        self.publish(text="原版")
        changed = MarketData([replace(self.data.indices[0], last=3010)])
        later = build_brief("close", changed, self.now + timedelta(hours=1))
        self.publish(later, changed, "新版")
        self.publish(text="原版重试")
        self.assertEqual(len(self.archive.snapshots()), 2)
        self.assertEqual(len(self.archive.reports()), 2)
        self.assertEqual(self.stem.with_suffix(".txt").read_text(), "新版")
        values = {(self.archive.root / row["path"] / "summary.txt").read_text() for row in self.archive.reports()}
        self.assertEqual(values, {"原版", "新版"})

    def test_same_timestamp_correction_uses_new_revision(self):
        self.publish(text="原版")
        changed = MarketData([replace(self.data.indices[0], last=3010)])
        self.publish(build_brief("close", changed, self.now), changed, "修订版")
        self.assertEqual(self.stem.with_suffix(".txt").read_text(), "修订版")

    def test_renderer_update_creates_version_without_duplicating_market_data(self):
        with patch("common.archive.code_version", return_value="first-layout"):
            self.publish(text="旧排版")
        with patch("common.archive.code_version", return_value="new-layout"):
            self.publish(text="新排版")
        self.assertEqual(len(self.archive.snapshots()), 1)
        self.assertEqual(len(self.archive.reports()), 2)
        self.assertEqual(self.stem.with_suffix(".txt").read_text(), "新排版")

    def test_render_failure_keeps_data_and_no_partial_report_then_retry_repairs(self):
        with self.assertRaises(OSError):
            self.publish(render=Mock(side_effect=OSError("disk failed")))
        self.assertEqual(len(self.archive.snapshots()), 1)
        self.assertEqual(self.archive.reports(), [])
        self.assertFalse(self.stem.with_suffix(".png").exists())
        self.assertEqual(list(self.archive.root.rglob(".pending-*")), [])
        self.publish()
        self.assertEqual(len(self.archive.reports()), 1)

    def test_concurrent_retry_renders_once_and_commits_one_revision(self):
        capture = self.archive.capture("ashare", self.data, self.now)
        render = Mock(side_effect=self.render)
        def job(index):
            archive = Archive(self.archive.root)
            brief = replace(self.brief, generated_at=self.now + timedelta(seconds=index))
            return archive.publish("ashare", brief, capture, render, "正文", self.stem)
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(len(list(pool.map(job, range(6)))), 6)
        self.assertEqual(len(self.archive.reports()), 1)
        render.assert_called_once()

    def test_invalid_numbers_and_naive_capture_time_fail_without_partial_data(self):
        for data, now in (({"last": float("nan")}, self.now), ({}, self.now.replace(tzinfo=None))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.archive.capture("ashare", data, now)
        self.assertEqual(self.archive.snapshots(), [])

    def test_history_links_versions_and_escapes_source_text(self):
        self.publish(text="<script>alert('x')</script> & 消息")
        changed = MarketData([replace(self.data.indices[0], last=3010)])
        self.publish(build_brief("close", changed, self.now + timedelta(hours=1)), changed)
        page = build_history(self.archive).read_text()
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn('data-current="0"', page)
        self.assertIn('data-current="1"', page)
        for row in self.archive.reports():
            self.assertIn(row["path"] + "/data.json", page)


if __name__ == "__main__":
    unittest.main()
