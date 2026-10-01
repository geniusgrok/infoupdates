import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ashare import __main__ as ashare
from ashare.models import CST, MarketData as AData, Quote as AQuote
from common.archive import Archive
from common.editorial import select_focus_news
from common.news import NewsItem
from usstock import __main__ as usstock
from usstock.models import NY, MarketData as UData, Quote as UQuote
from weekly import __main__ as weekly


class CliTests(unittest.TestCase):
    @staticmethod
    def render(brief, path):
        path.write_bytes(b'image')

    @staticmethod
    def valid_data(now):
        a = AData([AQuote('sh000001', '上证指数', 3000, trade_day='2026-09-30', session='15:30:00')],
                  overseas=[AQuote('gb_dji', '道琼斯', 40000, .5, session='09-30 收盘')])
        u = UData(completed={'^GSPC': UQuote('^GSPC', '标普500', 100, asof=now.replace(hour=16))},
                  postmarket={'AAPL': UQuote('AAPL', '苹果', 103, 3, 100, asof=now, session='postmarket', previous_date=now.date())})
        return a, u

    def test_skip_before_fetch_force_overwrite_and_failed_force(self):
        now = datetime(2026, 9, 30, 18, tzinfo=NY)
        a, u = self.valid_data(now)
        for cli, data, moment, count in ((ashare, a, now.astimezone(CST), 2), (usstock, u, now, 2),
                                        (weekly, None, datetime(2026, 9, 26, 10, tzinfo=CST), 1)):
            with self.subTest(module=cli.__package__), tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                root = Path(folder)
                args = ['--archive', str(root / 'archive'), '--output', str(root / 'output')]
                log = stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                clock = stack.enter_context(patch.object(cli, 'datetime', wraps=datetime))
                clock.now.return_value = moment
                render = stack.enter_context(patch.object(cli, 'render_png', side_effect=self.render))
                stack.enter_context(patch.object(cli, 'collect_releases'))
                if cli is weekly:
                    loader = stack.enter_context(patch.object(cli, 'load_events', return_value=[]))
                else:
                    loader = stack.enter_context(patch.object(cli, 'load_market', return_value=data))
                    stack.enter_context(patch.object(cli, 'load_next_event', return_value=None))
                cli.main(args)
                files = sorted((root / 'output').glob('*'))
                original = {path.name: path.read_bytes() for path in files}
                for path in files:
                    path.unlink()  # 重复运行还应恢复公开输出，不再请求数据。
                cli.main(args)
                self.assertIn('已有运行结果', log.getvalue())
                self.assertEqual(loader.call_count, 1)
                self.assertEqual(render.call_count, count)
                self.assertEqual({path.name: path.read_bytes() for path in files}, original)
                cli.main([*args, '--force'])
                self.assertEqual(loader.call_count, 2)
                self.assertEqual(render.call_count, count * 2)
                archive = Archive(root / 'archive')
                saved = archive.reports()
                render.side_effect = OSError('绘图失败')
                with self.assertRaises(SystemExit) as error:
                    cli.main([*args, '--force'])
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(archive.reports(), saved)
                cli.main(args)  # 失败释放运行锁，原结果仍可复用。
                self.assertEqual(loader.call_count, 3)
                self.assertEqual(len(archive.reports()), count)

    def test_concurrent_commands_only_fetch_once(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            args = ['close', '--archive', str(Path(folder) / 'archive'), '--output', str(Path(folder) / 'output')]
            stack.enter_context(redirect_stdout(io.StringIO()))
            clock = stack.enter_context(patch.object(ashare, 'datetime', wraps=datetime))
            clock.now.return_value = datetime(2026, 9, 30, 18, tzinfo=CST)
            data = AData([AQuote('sh000001', '上证指数', 3000, trade_day='2026-09-30', session='15:30:00')])
            loader = stack.enter_context(patch.object(ashare, 'load_market', return_value=data))
            stack.enter_context(patch.object(ashare, 'load_next_event', return_value=None))
            stack.enter_context(patch.object(ashare, 'collect_releases'))
            render = stack.enter_context(patch.object(ashare, 'render_png', side_effect=self.render))
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda _: ashare.main(args), range(3)))
            loader.assert_called_once()
            render.assert_called_once()

    def test_both_markets_deprioritize_previous_focus_without_changing_raw_capture(self):
        now = datetime(2026, 9, 30, 18, tzinfo=NY)
        news = [NewsItem(now, '美联储公布最新政策展望', '见闻'),
                NewsItem(now, '美国CPI通胀数据同比上涨2.5%', '见闻')]
        a, u = self.valid_data(now)
        a.news = u.news = news
        for cli, data, moment in ((ashare, a, now.astimezone(CST)), (usstock, u, now)):
            with self.subTest(module=cli.__package__), tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                clock = stack.enter_context(patch.object(cli, 'datetime', wraps=datetime))
                clock.now.return_value = moment
                stack.enter_context(patch.object(cli, 'load_market', return_value=data))
                stack.enter_context(patch.object(cli, 'load_next_event', return_value=None))
                stack.enter_context(patch.object(cli, 'collect_releases'))
                chosen = []
                def render(brief, path):
                    chosen.append(select_focus_news(brief.news, brief.generated_at).title)
                    self.render(brief, path)
                stack.enter_context(patch.object(cli, 'render_png', side_effect=render))
                cli.main(['--archive', str(Path(folder) / 'archive'), '--output', str(Path(folder) / 'output')])
                self.assertEqual(len(chosen), 2)
                self.assertNotEqual(chosen[0], chosen[1])
                archive = Archive(Path(folder) / 'archive')
                self.assertTrue(all(item['rank'] == 0 for item in archive.snapshots()[0]['data']['news']))
                for report in archive.reports():
                    items = [NewsItem(**(item | {'published': datetime.fromisoformat(item['published'])}))
                             for item in report['data']['news']]
                    title = select_focus_news(items, moment).title
                    text = (archive.root / report['path'] / 'summary.txt').read_text()
                    self.assertIn(title, text)

    def test_missing_core_keeps_capture_retries_and_preserves_successful_force_result(self):
        now = datetime(2026, 9, 30, 18, tzinfo=NY)
        a, u = self.valid_data(now)
        cases = ((ashare, 'close', AData([], overseas=a.overseas), a, now.astimezone(CST)),
                 (ashare, 'morning', AData(a.indices), a, now.astimezone(CST)),
                 (usstock, 'premarket', UData(completed=u.completed), u, now),
                 (usstock, 'postmarket', UData(postmarket=u.postmarket), u, now))
        for cli, kind, missing, valid, moment in cases:
            with self.subTest(market=cli.__package__, kind=kind), tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                errors = stack.enter_context(redirect_stderr(io.StringIO()))
                clock = stack.enter_context(patch.object(cli, 'datetime', wraps=datetime))
                clock.now.return_value = moment
                loader = stack.enter_context(patch.object(cli, 'load_market', side_effect=[missing, valid, missing]))
                render = stack.enter_context(patch.object(cli, 'render_png', side_effect=self.render))
                stack.enter_context(patch.object(cli, 'load_next_event', return_value=None))
                stack.enter_context(patch.object(cli, 'collect_releases'))
                args = [kind, '--archive', str(Path(folder) / 'archive'), '--output', str(Path(folder) / 'output')]
                with self.assertRaises(SystemExit) as failure:
                    cli.main(args)
                self.assertEqual(failure.exception.code, 2)
                self.assertIn('核心行情不足', errors.getvalue())
                archive = Archive(Path(folder) / 'archive')
                self.assertEqual(len(archive.snapshots()), 1)
                self.assertEqual(archive.reports(), [])
                render.assert_not_called()
                cli.main(args)  # 没有成功结果，正常重试即可；次要数据缺失仍可成稿。
                saved = archive.reports()
                self.assertEqual(len(saved), 1)
                self.assertEqual(len(archive.snapshots()), 2)
                with self.assertRaises(SystemExit):
                    cli.main([*args, '--force'])
                self.assertEqual(archive.reports(), saved)
                cli.main(args)
                self.assertEqual(loader.call_count, 3)
                render.assert_called_once()

    def test_all_retains_valid_edition_when_other_edition_lacks_core(self):
        now = datetime(2026, 9, 30, 18, tzinfo=NY)
        _, valid = self.valid_data(now)
        missing = UData(completed=valid.completed)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            clock = stack.enter_context(patch.object(usstock, 'datetime', wraps=datetime))
            clock.now.return_value = now
            loader = stack.enter_context(patch.object(usstock, 'load_market', side_effect=[missing, valid]))
            render = stack.enter_context(patch.object(usstock, 'render_png', side_effect=self.render))
            stack.enter_context(patch.object(usstock, 'load_next_event', return_value=None))
            stack.enter_context(patch.object(usstock, 'collect_releases'))
            args = ['--archive', str(Path(folder) / 'archive'), '--output', str(Path(folder) / 'output')]
            with self.assertRaises(SystemExit):
                usstock.main(args)
            archive = Archive(Path(folder) / 'archive')
            self.assertEqual([row['session'] for row in archive.reports()], ['postmarket'])
            usstock.main(args)
            self.assertEqual(len(archive.reports()), 2)
            self.assertEqual(loader.call_count, 2)
            self.assertEqual(render.call_count, 2)
