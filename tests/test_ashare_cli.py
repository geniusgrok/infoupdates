from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ashare import __main__ as cli
from ashare.models import CST, MarketData
from ashare.models import Quote


def market(day: str) -> MarketData:
    return MarketData(indices=[
        Quote("sh000001", "上证指数", 3842.19, pct=0.31,
              trade_day=day, session="15:30:00"),
    ])


class CliTests(unittest.TestCase):
    def run_cli(self, session: str, now: datetime, day: str, output: Path):
        def save_image(brief, path):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preview")
            return path

        stdout = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market", return_value=market(day)) as load, \
                patch.object(cli, "render_png", side_effect=save_image) as render, \
                redirect_stdout(stdout):
            clock.now.return_value = now
            cli.main([session, "--output", str(output)])
        return stdout.getvalue(), load, render

    def test_morning_filename_matches_holiday_edition(self) -> None:
        now = datetime(2026, 9, 30, 20, tzinfo=CST)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "posters"
            text, load, render = self.run_cli("morning", now, "2026-09-30", output)
            self.assertTrue((output / "morning-2026-10-08.png").exists())
            copy = (output / "morning-2026-10-08.txt").read_text(encoding="utf-8")
            self.assertIn("10月8日 周四", copy)
            self.assertIn("morning-2026-10-08.png", text)
            self.assertFalse((output / "morning-2026-09-30.txt").exists())
            self.assertEqual(render.call_args.args[0].edition_date.isoformat(), "2026-10-08")
            load.assert_called_once_with()

    def test_morning_uses_current_edition_before_close(self) -> None:
        now = datetime(2026, 9, 30, 12, 30, tzinfo=CST)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli("morning", now, "2026-09-29", output)
            copy = (output / "morning-2026-09-30.txt").read_text(encoding="utf-8")
            self.assertIn("9月30日 周三", copy)
            self.assertFalse((output / "morning-2026-09-29.txt").exists())

    def test_all_uses_each_edition_date_and_one_market_load(self) -> None:
        now = datetime(2026, 9, 30, 20, tzinfo=CST)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            _, load, render = self.run_cli("all", now, "2026-09-30", output)
            self.assertEqual({path.name for path in output.iterdir()}, {
                "close-2026-09-30.png", "close-2026-09-30.txt",
                "morning-2026-10-08.png", "morning-2026-10-08.txt",
            })
            self.assertEqual(render.call_count, 2)
            load.assert_called_once_with()

    def assert_calendar_error_before_fetch(self, session: str, now: datetime) -> None:
        stderr = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market") as load, \
                patch.object(cli, "render_png") as render, \
                redirect_stderr(stderr):
            clock.now.return_value = now
            with self.assertRaises(SystemExit) as error:
                cli.main([session])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("交易日历尚未覆盖2027年", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        load.assert_not_called()
        render.assert_not_called()

    def test_unknown_year_morning_and_all_fail_before_fetch(self) -> None:
        for session in ("morning", "all"):
            with self.subTest(session=session):
                self.assert_calendar_error_before_fetch(
                    session, datetime(2027, 1, 4, 8, tzinfo=CST))

    def test_cross_year_next_edition_fails_before_fetch(self) -> None:
        self.assert_calendar_error_before_fetch(
            "morning", datetime(2026, 12, 31, 20, tzinfo=CST))

    def test_known_year_morning_before_close_does_not_require_next_year(self) -> None:
        now = datetime(2026, 12, 31, 8, tzinfo=CST)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli("morning", now, "2026-12-30", output)
            self.assertTrue((output / "morning-2026-12-31.txt").exists())

    def test_unknown_year_close_still_runs(self) -> None:
        now = datetime(2027, 1, 4, 20, tzinfo=CST)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            _, load, _ = self.run_cli("close", now, "2027-01-04", output)
            copy = (output / "close-2027-01-04.txt").read_text(encoding="utf-8")
            self.assertIn("1月4日 周一", copy)
            self.assertIn("交易日历尚未覆盖2027年", copy)
            load.assert_called_once_with()

    def test_brief_validation_error_has_clear_cli_exit(self) -> None:
        stderr = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market", return_value=market("2026-09-30")), \
                patch.object(cli, "build_brief", side_effect=ValueError("交易日历需要更新")), \
                patch.object(cli, "render_png") as render, \
                redirect_stderr(stderr):
            clock.now.return_value = datetime(2026, 9, 30, 20, tzinfo=CST)
            with self.assertRaises(SystemExit) as error:
                cli.main(["all"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("交易日历需要更新", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
