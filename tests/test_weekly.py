from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ashare.calendar import is_trading_day as a_trading
from ashare.models import CST, MarketData as AData, Quote as AQuote
from common.archive import Archive
from common.events import CalendarEvent, upcoming_events
from common.news import NewsItem
from usstock.calendar import is_trading_day as u_trading, session_close
from usstock.models import MarketData as UData, Quote as UQuote, NY
from weekly import __main__ as cli, render
from weekly.backfill import parse_ashare
from weekly.compose import build_weekly, social_copy, week_start
from tests.test_ashare_render import AuditedCanvas
from tests import test_ashare_render as layout


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = Archive(Path(self.folder.name) / "archive")
        self.start = date(2026, 9, 21)
        self.now = datetime(2026, 9, 26, 10, tzinfo=CST)

    def a_capture(self, day, price, *, source="新浪", session="15:30:00", amount=500e8):
        quotes = [AQuote(symbol, name, price, pct=99, trade_day=day.isoformat(), session=session, source=source, amount=amount if i == 0 else None)
                  for i, (symbol, name) in enumerate((("sh000001", "上证指数"), ("sz399006", "创业板指")))]
        return self.archive.capture("ashare", AData(quotes), datetime.combine(day, datetime.min.time(), CST) + timedelta(hours=18))

    def u_capture(self, day, price, *, source="Yahoo Finance", session="regular", cached=False):
        quotes = {symbol: UQuote(symbol, name, price, pct=99, source=source, session=session, cached=cached,
                                 asof=session_close(day), volume=2e6)
                  for symbol, name in (("^GSPC", "标普500"), ("^IXIC", "纳斯达克"), ("SPY", "标普ETF"), ("XLK", "科技"), ("XLF", "金融"))}
        return self.archive.capture("usstock", UData(completed=quotes), session_close(day) + timedelta(hours=1))

    def populate(self):
        for i in range(-7, 5):
            day = self.start + timedelta(days=i)
            if a_trading(day):
                self.a_capture(day, 100 if day < self.start else 110)
            if u_trading(day):
                self.u_capture(day, 100 if day < self.start else 90)

    def test_completed_week_and_cross_timezone_selection(self):
        self.assertEqual(week_start(datetime(2026, 10, 1, 12, tzinfo=NY)), self.start)
        self.assertEqual(week_start(self.now), self.start)
        with self.assertRaises(ValueError):
            week_start(self.now, date(2026, 9, 28))
        with self.assertRaises(ValueError):
            week_start(self.now, date(2026, 9, 22))

    def test_returns_use_endpoints_not_daily_percent_sum_and_respect_holidays(self):
        self.populate()
        brief = build_weekly(self.archive, self.now)
        a, _, us, _ = brief["metrics"]
        self.assertAlmostEqual(a["pct"], 10)
        self.assertAlmostEqual(us["pct"], -10)
        self.assertEqual(a["expected"], 4)  # 9月25日中秋休市。
        self.assertEqual(a["end_date"], "2026-09-24")
        self.assertEqual(us["expected"], 5)
        self.assertEqual(us["end_date"], "2026-09-25")
        self.assertIn("方向分化", brief["headline"])
        self.assertIn("沪市日均成交额 500.0亿元", social_copy(brief))
        self.assertIn("SPY日均成交股数 200.0万股", social_copy(brief))

    def test_missing_friday_is_not_replaced_with_thursday(self):
        self.u_capture(date(2026, 9, 18), 100)
        self.u_capture(date(2026, 9, 24), 110)
        brief = build_weekly(self.archive, self.now)
        self.assertIsNone(brief["metrics"][2]["pct"])
        self.assertIn("量能待确认", brief["evidence"][1])

    def test_same_source_required_and_intraday_extended_cached_are_not_close(self):
        self.a_capture(date(2026, 9, 18), 100, source="腾讯")
        self.a_capture(date(2026, 9, 24), 110, source="新浪")
        self.u_capture(date(2026, 9, 18), 100)
        self.u_capture(date(2026, 9, 25), 110, cached=True)
        self.u_capture(date(2026, 9, 25), 110, session="postmarket")
        self.a_capture(date(2026, 9, 24), 110, source="腾讯", session="14:30:00")
        self.assertTrue(all(item["pct"] is None for item in build_weekly(self.archive, self.now)["metrics"]))

    def test_empty_history_and_full_holiday_week_are_honest(self):
        empty = build_weekly(self.archive, self.now)
        self.assertTrue(all(item["pct"] is None for item in empty["metrics"]))
        self.assertNotIn("+0.00%", social_copy(empty))
        holiday = build_weekly(self.archive, datetime(2025, 10, 4, 10, tzinfo=CST), week=date(2025, 9, 29))
        self.assertEqual(holiday["metrics"][0]["expected"], 2)
        closed = build_weekly(self.archive, datetime(2026, 2, 21, 10, tzinfo=CST), week=date(2026, 2, 16))
        self.assertEqual(closed["metrics"][0]["expected"], 0)
        self.assertIn("本周休市", closed["changes"][0])

    def test_next_week_event_can_be_more_than_three_days_away(self):
        at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
        self.archive.add_event(CalendarEvent("美国非农就业与失业率", at, "美国劳工统计局"), self.now)
        brief = build_weekly(self.archive, self.now)
        self.assertEqual(brief["future"]["title"], "美国非农就业与失业率")
        self.assertIn("20:30 CST", brief["future"]["stamp"])
        raw = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;TZID=US-Eastern:20261002T083000\nSUMMARY:Employment Situation\nEND:VEVENT\nEND:VCALENDAR"
        self.assertEqual(upcoming_events(raw, self.now), [])
        self.assertEqual(len(upcoming_events(raw, self.now, days=10)), 1)

    def test_backfill_rejects_wrong_symbol_and_keeps_amount_units(self):
        rows = [{"code": "zs_000001", "status": 0, "hq": [["2026-09-24", "100", "110", "10", "10%", "99", "111", "12345", "5000000", "-"]]}]
        quotes = parse_ashare(rows, "sh000001", "上证指数", self.now)
        self.assertEqual(quotes[0].amount, 500e8)
        self.assertEqual(quotes[0].source, "搜狐日线")
        with self.assertRaises(ValueError):
            parse_ashare(rows, "sz399006", "创业板指", self.now)

    def test_weekly_story_uses_daily_curated_news_instead_of_old_raw_feed(self):
        from ashare.compose import build_brief
        now = datetime(2026, 9, 24, 18, tzinfo=CST)
        data = AData([AQuote("sh000001", "上证指数", 100, trade_day="2026-09-24", session="15:30:00", source="新浪")], news=[
            NewsItem(datetime(2026, 9, 21, 10, tzinfo=CST), "央行开出大额罚单，多家银行受罚", "东财"),
            NewsItem(datetime(2026, 9, 24, 16, tzinfo=CST), "美国制造业PMI发布，投资者关注增长前景", "见闻"),
        ])
        capture = self.archive.capture("ashare", data, now)
        brief = build_brief("close", data, now)
        self.archive.publish("ashare", brief, capture, lambda b, p: p.write_bytes(b"preview"), "文字", Path(self.folder.name) / "close")
        story = build_weekly(self.archive, self.now)["story"]["title"]
        self.assertIn("PMI", story)
        self.assertNotIn("罚单", story)

    def test_layout_matches_daily_palette_and_has_no_overlapping_text(self):
        self.populate()
        brief = build_weekly(self.archive, self.now)
        canvas = AuditedCanvas()
        path = Path(self.folder.name) / "weekly.png"
        with patch.object(render, "Canvas", return_value=canvas):
            render.render_png(brief, path)
        with Image.open(path) as picture:
            self.assertEqual(picture.size, (1080, 1620))
            self.assertEqual(picture.getpixel((0, 1000)), (8, 10, 13))
        layout.RenderRegressionTests().assert_layout(canvas)

    def test_weekly_cli_retries_are_idempotent(self):
        import io
        from contextlib import redirect_stdout
        self.populate()
        output = Path(self.folder.name) / "output"
        with patch.object(cli, "datetime") as clock, patch.object(cli, "load_events", return_value=[]), redirect_stdout(io.StringIO()):
            clock.now.return_value = self.now
            cli.main(["--archive", str(self.archive.root), "--output", str(output)])
            first = (output / "weekly-2026-09-26.txt").read_bytes()
            clock.now.return_value = self.now + timedelta(hours=1)
            cli.main(["--archive", str(self.archive.root), "--output", str(output)])
            self.assertEqual((output / "weekly-2026-09-26.txt").read_bytes(), first)
        self.assertEqual(len(self.archive.reports()), 1)


if __name__ == "__main__":
    unittest.main()
