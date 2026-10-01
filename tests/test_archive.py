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
from common.editorial import select_focus_news
from common.history import build_history
from common.news import NewsItem


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.archive = Archive(self.root / 'archive')
        self.now = datetime(2026, 9, 30, 18, tzinfo=CST)
        self.data = MarketData([Quote('sh000001', '上证指数', 3000, trade_day='2026-09-30', session='15:30:00', source='新浪')])
        self.brief = build_brief('close', self.data, self.now)
        self.stem = self.root / 'output' / 'close-2026-09-30'

    @staticmethod
    def render(brief, path):
        path.write_bytes(f'{brief.hero.last}@{brief.generated_at.isoformat()}'.encode())

    def publish(self, *, data=None, text='正文', render=None, force=False):
        data = data or self.data
        brief = build_brief('close', data, self.now)
        capture = self.archive.capture('ashare', data, self.now)
        return self.archive.publish('ashare', brief, capture, render or self.render, text, self.stem, force=force)

    def test_default_reuses_success_even_when_input_changes(self):
        render = Mock(side_effect=self.render)
        paths = self.publish(render=render)
        original = [path.read_bytes() for path in paths]
        changed = MarketData([replace(self.data.indices[0], last=3010)])
        self.publish(data=changed, text='改变的输入', render=render)
        self.assertEqual([path.read_bytes() for path in paths], original)
        render.assert_called_once()
        self.assertEqual(len(self.archive.reports()), 1)
        saved = json.loads((self.archive.root / self.archive.reports()[0]['path'] / 'data.json').read_text())
        self.assertEqual(saved['snapshot']['indices'][0]['source'], '新浪')
        self.assertEqual(set(saved), {'capture_id', 'captured_at', 'market', 'snapshot', 'brief'})

    def test_force_replaces_result_and_keeps_market_snapshots(self):
        self.publish()
        old = self.archive.root / self.archive.reports()[0]['path']
        changed = MarketData([replace(self.data.indices[0], last=3010)])
        self.publish(data=changed, text='新结果', force=True)
        self.assertEqual(len(self.archive.reports()), 1)
        self.assertEqual(len(self.archive.snapshots()), 2)
        self.assertEqual(self.stem.with_suffix('.txt').read_text(), '新结果')
        self.assertFalse(old.exists())
        render = Mock(side_effect=self.render)
        self.publish(data=changed, render=render, force=True)
        render.assert_called_once()  # 强制运行即使输入相同也重新生成。

    def test_first_failure_keeps_capture_and_retry_succeeds(self):
        with self.assertRaises(OSError):
            self.publish(render=Mock(side_effect=OSError('绘图失败')))
        self.assertEqual(len(self.archive.snapshots()), 1)
        self.assertEqual(self.archive.reports(), [])
        self.assertFalse(self.stem.with_suffix('.png').exists())
        self.publish()
        self.assertEqual(len(self.archive.reports()), 1)

    def test_failed_force_preserves_success(self):
        paths = self.publish()
        old = self.archive.reports()[0]
        original = [path.read_bytes() for path in paths]
        with self.assertRaises(OSError):
            self.publish(render=Mock(side_effect=OSError('绘图失败')), force=True)
        self.assertEqual(self.archive.reports()[0], old)
        self.assertEqual([path.read_bytes() for path in paths], original)
        self.assertTrue((self.archive.root / old['path'] / 'data.json').is_file())
        self.assertEqual(list(self.archive.root.rglob('.pending-*')), [])

    def test_output_failure_recovers_from_saved_result(self):
        paths = self.publish()
        original = [path.read_bytes() for path in paths]
        old = self.archive.reports()[0]
        from common.archive import atomic_write
        def fail_text(path, data):
            if path.suffix == '.txt':
                raise OSError('输出失败')
            atomic_write(path, data)
        with patch('common.archive.atomic_write', side_effect=fail_text), self.assertRaises(OSError):
            self.publish(data=MarketData([replace(self.data.indices[0], last=3010)]), force=True)
        self.assertEqual(self.archive.reports()[0], old)
        self.assertTrue(self.archive.reuse('ashare', 'close', self.now.date(), self.stem))
        self.assertEqual([path.read_bytes() for path in paths], original)

    def test_concurrent_default_renders_once(self):
        capture = self.archive.capture('ashare', self.data, self.now)
        render = Mock(side_effect=self.render)
        def run(index):
            archive = Archive(self.archive.root)
            brief = replace(self.brief, generated_at=self.now + timedelta(seconds=index))
            return archive.publish('ashare', brief, capture, render, '正文', self.stem)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, range(4)))
        render.assert_called_once()
        self.assertEqual(len(self.archive.reports()), 1)

    def test_missing_artifact_is_regenerated(self):
        self.publish()
        old = self.archive.root / self.archive.reports()[0]['path']
        (old / 'summary.txt').unlink()
        self.assertFalse(self.archive.reuse('ashare', 'close', self.now.date(), self.stem))
        render = Mock(side_effect=self.render)
        self.publish(render=render)
        render.assert_called_once()
        self.assertFalse(old.exists())

    def test_previous_news_uses_actual_focus_and_excludes_self_future_and_missing(self):
        self.data.news = [NewsItem(self.now, '央行公布最新货币政策安排', '见闻', rank=40),
                          NewsItem(self.now, '上市公司发布最新业绩报告', '东财', rank=30)]
        title = select_focus_news(build_brief('close', self.data, self.now).news, self.now).title
        self.publish()
        lookup = self.archive.previous_key_news
        self.assertEqual(lookup('ashare', 'morning', self.now.date(), self.now), title)
        self.assertEqual(lookup('ashare', 'close', self.now.date(), self.now), '')
        self.assertEqual(lookup('usstock', 'premarket', self.now.date(), self.now), '')
        self.assertEqual(lookup('ashare', 'morning', self.now.date(), self.now - timedelta(seconds=1)), '')
        self.assertEqual(lookup('ashare', 'morning', self.now.date(), self.now + timedelta(hours=97)), '')
        folder = self.archive.root / self.archive.reports()[0]['path']
        (folder / 'image.png').unlink()
        self.assertEqual(lookup('ashare', 'morning', self.now.date(), self.now), '')

    def test_history_escapes_text_and_links_current_result(self):
        self.publish(text='<script>alert(1)</script> & 消息')
        page = build_history(self.archive).read_text()
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>alert', page)
        self.assertNotIn('id="versions"', page)
        self.assertIn(self.archive.reports()[0]['path'] + '/data.json', page)
