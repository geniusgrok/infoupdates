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
from usstock import __main__ as usstock
from usstock.models import NY, MarketData as UData, Quote as UQuote
from weekly import __main__ as weekly


class CliTests(unittest.TestCase):
    @staticmethod
    def render(brief, path):
        path.write_bytes(b'image')

    def test_skip_before_fetch_force_overwrite_and_failed_force(self):
        now = datetime(2026, 9, 30, 18, tzinfo=NY)
        a = AData([AQuote('sh000001', '上证指数', 3000, trade_day='2026-09-30', session='15:30:00')])
        u = UData(completed={'^GSPC': UQuote('^GSPC', '标普500', 100, asof=now.replace(hour=16))})
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
