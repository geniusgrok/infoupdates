from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from usstock import __main__ as cli
from usstock.calendar import edition_date
from usstock.models import Brief, MarketData, NY


class UsCliTests(unittest.TestCase):
    def run_cli(self, args: list[str], now: datetime, output: Path):
        data = MarketData()

        def build(kind, market, *, now):
            self.assertIs(market, data)
            return Brief(kind=kind, generated_at=now, edition_date=edition_date(kind, now))

        def save_image(brief, path):
            path = Path(path)
            path.write_bytes(b"preview")
            return path

        stdout = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market", return_value=data) as load, \
                patch.object(cli, "build_brief", side_effect=build) as compose, \
                patch.object(cli, "render_png", side_effect=save_image) as render, \
                patch.object(cli, "social_copy", side_effect=lambda b: f"{b.title} {b.edition_date}") as copy, \
                redirect_stdout(stdout):
            clock.now.return_value = now
            cli.main([*args, "--output", str(output)])
        return stdout.getvalue(), load, compose, render, copy

    def test_all_uses_each_edition_and_single_capture(self) -> None:
        now = datetime(2026, 10, 1, 17, tzinfo=NY)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new" / "posters"
            stdout, load, compose, render, copy = self.run_cli(["all"], now, output)
            self.assertEqual({path.name for path in output.iterdir()}, {
                "us-premarket-2026-10-02.png", "us-premarket-2026-10-02.txt",
                "us-postmarket-2026-10-01.png", "us-postmarket-2026-10-01.txt",
            })
            self.assertIn("美股盘前精选 2026-10-02", (output / "us-premarket-2026-10-02.txt").read_text())
            self.assertIn("美股盘后精选 2026-10-01", (output / "us-postmarket-2026-10-01.txt").read_text())
            self.assertIn("us-postmarket-2026-10-01.png", stdout)
            load.assert_called_once_with()
            self.assertEqual(compose.call_count, 2)
            self.assertEqual(render.call_count, 2)
            self.assertEqual(copy.call_count, 2)
            self.assertTrue(all(call.kwargs["now"] is now for call in compose.call_args_list))

    def test_default_session_generates_both(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            _, load, compose, render, _ = self.run_cli([], datetime(2026, 10, 1, 8, tzinfo=NY), Path(folder))
        load.assert_called_once()
        self.assertEqual([call.args[0] for call in compose.call_args_list], ["premarket", "postmarket"])
        self.assertEqual(render.call_count, 2)

    def test_premarket_keeps_current_date_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            _, load, compose, render, _ = self.run_cli(["premarket"], datetime(2026, 10, 1, 8, tzinfo=NY), output)
            self.assertEqual({path.name for path in output.iterdir()}, {
                "us-premarket-2026-10-01.png", "us-premarket-2026-10-01.txt",
            })
        load.assert_called_once()
        compose.assert_called_once()
        render.assert_called_once()

    def test_postmarket_uses_last_completed_session_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli(["postmarket"], datetime(2026, 10, 1, 8, tzinfo=NY), output)
            self.assertTrue((output / "us-postmarket-2026-09-30.txt").exists())

    def test_half_day_close_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli(["all"], datetime(2026, 11, 27, 13, 30, tzinfo=NY), output)
            self.assertTrue((output / "us-postmarket-2026-11-27.txt").exists())
            self.assertTrue((output / "us-premarket-2026-11-30.txt").exists())

    def test_utc_date_converts_to_new_york_edition(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli(["all"], datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc), output)
            self.assertTrue((output / "us-postmarket-2026-10-01.txt").exists())
            self.assertTrue((output / "us-premarket-2026-10-02.txt").exists())

    def assert_calendar_error_before_fetch(self, session: str, now: datetime, missing_year: int) -> None:
        stderr = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market") as load, \
                patch.object(cli, "build_brief") as compose, \
                patch.object(cli, "render_png") as render, \
                redirect_stderr(stderr):
            clock.now.return_value = now
            with self.assertRaises(SystemExit) as error:
                cli.main([session])
        self.assertEqual(error.exception.code, 2)
        self.assertIn(f"美股交易日历尚未覆盖{missing_year}年", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        load.assert_not_called()
        compose.assert_not_called()
        render.assert_not_called()

    def test_unsupported_current_year_fails_before_fetch(self) -> None:
        for session in ("all", "premarket", "postmarket"):
            with self.subTest(session=session):
                self.assert_calendar_error_before_fetch(session, datetime(2029, 1, 4, 8, tzinfo=NY), 2029)

    def test_required_next_year_fails_before_fetch(self) -> None:
        for session in ("all", "premarket"):
            with self.subTest(session=session):
                self.assert_calendar_error_before_fetch(session, datetime(2028, 12, 29, 17, tzinfo=NY), 2029)

    def test_required_reference_year_fails_before_fetch(self) -> None:
        for session in ("all", "premarket", "postmarket"):
            with self.subTest(session=session):
                self.assert_calendar_error_before_fetch(session, datetime(2024, 1, 2, 8, tzinfo=NY), 2023)

    def test_postmarket_end_of_supported_year_does_not_require_next_year(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.run_cli(["postmarket"], datetime(2028, 12, 29, 17, tzinfo=NY), output)
            self.assertTrue((output / "us-postmarket-2028-12-29.txt").exists())

    def test_build_error_has_clean_cli_exit(self) -> None:
        stderr = io.StringIO()
        with patch.object(cli, "datetime") as clock, \
                patch.object(cli, "load_market", return_value=MarketData()) as load, \
                patch.object(cli, "build_brief", side_effect=ValueError("不支持的行情交易日期")), \
                patch.object(cli, "render_png") as render, \
                redirect_stderr(stderr):
            clock.now.return_value = datetime(2026, 10, 1, 17, tzinfo=NY)
            with self.assertRaises(SystemExit) as error:
                cli.main(["all"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("不支持的行情交易日期", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        load.assert_called_once()
        render.assert_not_called()

    def test_help_and_invalid_session_do_not_fetch(self) -> None:
        for arg, expected in (("--help", 0), ("close", 2)):
            with self.subTest(arg=arg), patch.object(cli, "load_market") as load, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.main([arg])
            self.assertEqual(error.exception.code, expected)
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
