import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ashare.compose import build_brief
from ashare.models import CST, MarketData, Quote
from ashare.overseas import align_overseas
from ashare.parse import parse_nikkei, parse_us_index
from ashare.rank import select_news
from common.archive import identity
from common.history import build_history
from common.news import NewsItem
from common.publication import publication_issue
from common.replay import dated_brief
from common.schedule import scheduled_jobs
from review.events import comparisons, record_metric
from usstock.cache import _current
from usstock.calendar import session_close
from usstock.models import NY, MarketData as UData, Quote as UQuote, clean_quote
from common.events import CalendarEvent
from common.archive import Archive


SINA_US = (
    "道琼斯,51850.5508,0.38,2026-10-13 04:44:03,195.6000,51765.3516,51928.1016,"
    "51718.6016,54744.3281,45057.2812,307464899,438551700,0,0.00,--,0.00,0.00,0.00,0.00,"
    "0,0,0.0000,0.00,0.0000,,Oct 12 04:43PM EDT,51654.9492,0,1,2026"
)


def _morning_quote(name, day, **extra):
    return {
        "name": name, "last": 100.0, "trade_day": day, "session": f"{day[5:]} 收盘", **extra,
    }


class OverseasDateTests(unittest.TestCase):
    def test_sina_us_close_uses_eastern_session_not_beijing_clock(self):
        quote = parse_us_index("gb_dji", SINA_US, "道琼斯")
        self.assertEqual(quote.trade_day, "2026-10-12")
        self.assertEqual(quote.session, "10-12 收盘")
        self.assertIn("-04:00", quote.quote_time)
        self.assertAlmostEqual(quote.last, 51850.5508)
        undated = parse_us_index("gb_dji", "道琼斯,100,0.2,2026-10-13 04:44:03,1", "道琼斯")
        self.assertEqual(undated.trade_day, "")
        self.assertEqual(undated.session, "美股收盘")
        garbled = parse_us_index("gb_dji", "道琼斯,100,0.2,???,1,NotADate", "道琼斯")
        self.assertEqual(garbled.trade_day, "")
        self.assertEqual(garbled.session, "美股收盘")

    def test_nikkei_keeps_its_own_market_date(self):
        quote = parse_nikkei("b_NKY", "日经225指数,38000,-10,-0.1,2:12 AM,14:12:00,2026-10-12,09:00:00")
        self.assertEqual(quote.trade_day, "2026-10-12")
        self.assertEqual(quote.session, "10-12")

    def test_us_core_accepts_only_the_last_completed_session(self):
        # 周一 08:00 北京 = 周日晚美东，最近完成的美股交易日是上周五。
        now = datetime(2026, 10, 12, 8, tzinfo=CST)
        friday = "2026-10-09"
        cases = (
            ("无日期", Quote("gb_dji", "道琼斯", 100, session="美股收盘"), False),
            ("过期", Quote("gb_dji", "道琼斯", 100, trade_day="2026-10-08", session="10-08 收盘"), False),
            ("未来", Quote("gb_dji", "道琼斯", 100, trade_day="2026-10-12", session="10-12 收盘"), False),
            ("周末前收", Quote("gb_dji", "道琼斯", 100, trade_day=friday, session="10-09 收盘"), True),
            ("只有月日", Quote("gb_dji", "道琼斯", 100, session="10-09 收盘"), True),
        )
        for label, quote, keep in cases:
            with self.subTest(label=label):
                kept, dropped = align_overseas([quote], now)
                self.assertEqual(bool(kept), keep)
                self.assertEqual(bool(dropped), not keep)
        payload = {
            "kind": "morning",
            "generated_at": now.isoformat(),
            "overseas": [
                _morning_quote("道琼斯", ""),
                _morning_quote("纳斯达克", "2026-10-08"),
                _morning_quote("标普500", "2026-10-12"),
                _morning_quote("道琼斯", friday),
            ],
        }
        payload["overseas"][0]["trade_day"] = ""
        payload["overseas"][0]["session"] = "美股收盘"
        self.assertEqual(publication_issue("ashare", payload), "")
        payload["overseas"] = payload["overseas"][:3]
        self.assertIn("核心行情不足", publication_issue("ashare", payload))

    def test_nikkei_is_not_forced_onto_the_us_session(self):
        # 2026-09-07 是美国劳动节。周二早晨，美股最近收盘仍是周五，日经按东京周一。
        now = datetime(2026, 9, 8, 8, tzinfo=CST)
        us = Quote("gb_dji", "道琼斯", 100, trade_day="2026-09-04", session="09-04 收盘")
        nikkei = Quote("b_NKY", "日经225", 38000, trade_day="2026-09-07", session="09-07")
        stale_nikkei = Quote("b_NKY", "日经225", 38000, trade_day="2026-09-04", session="09-04")
        kept, dropped = align_overseas([us, nikkei, stale_nikkei], now)
        self.assertEqual([quote.name for quote in kept], ["道琼斯", "日经225"])
        self.assertEqual(kept[1].trade_day, "2026-09-07")
        self.assertEqual(dropped, ["日经225"])
        brief = {
            "kind": "morning",
            "generated_at": now.isoformat(),
            "overseas": [_morning_quote("日经225", "2026-09-07")],
        }
        self.assertEqual(publication_issue("ashare", brief), "")
        brief["overseas"] = [_morning_quote("日经225", "2026-09-04")]
        self.assertIn("核心行情不足", publication_issue("ashare", brief))


class NewsDedupeTests(unittest.TestCase):
    def test_sign_decimal_and_newer_prefix_do_not_collapse(self):
        now = datetime(2026, 10, 9, 16, tzinfo=CST)
        plus = NewsItem(now - timedelta(hours=5), "美国9月CPI同比上涨+0.5%", "见闻", 3, url="https://example.com/plus", external_id="1")
        minus = NewsItem(now - timedelta(hours=1), "美国9月CPI同比上涨-0.5%", "见闻", 2, url="https://example.com/minus", external_id="2")
        small = NewsItem(now - timedelta(hours=4), "美国核心CPI同比上涨1.25%", "东财", 3)
        large = NewsItem(now - timedelta(hours=2), "美国核心CPI同比上涨12.5%", "东财", 2)
        older = NewsItem(now - timedelta(hours=3), "美联储维持当前利率水平并观察后续数据", "见闻", 4)
        newer = NewsItem(now - timedelta(hours=1), "美联储维持当前利率水平并观察后续数据，声明措辞有调整", "见闻", 2)
        chosen = select_news([plus, minus, small, large, older, newer], kind="close", trade_date=date(2026, 10, 9), now=now)
        titles = [item.title for item in chosen]
        self.assertIn(plus.title, titles)
        self.assertIn(minus.title, titles)
        self.assertIn(small.title, titles)
        self.assertIn(large.title, titles)
        self.assertIn(newer.title, titles)
        self.assertNotIn(older.title, titles)
        self.assertEqual(next(item for item in chosen if item.title == plus.title).url, "https://example.com/plus")
        self.assertEqual(next(item for item in chosen if item.title == plus.title).external_id, "1")


class ScheduleTests(unittest.TestCase):
    def test_delayed_cron_keeps_its_edition_and_dst_twin_does_not(self):
        morning = datetime(2026, 10, 9, 9, 1, tzinfo=CST)
        jobs, reasons = scheduled_jobs(morning, "0 0 * * 1-5")
        self.assertEqual(jobs, [("ashare", ["morning"])])
        self.assertTrue(reasons[0].startswith("生成："))
        late = datetime(2026, 10, 9, 12, tzinfo=CST)
        self.assertEqual(scheduled_jobs(late, "0 0 * * 1-5")[0], [])
        close = datetime(2026, 10, 9, 15, 31, tzinfo=CST)
        self.assertEqual(scheduled_jobs(close, "30 7 * * 1-5")[0], [("ashare", ["close"])])
        holiday = datetime(2026, 10, 1, 8, tzinfo=CST)
        skipped, why = scheduled_jobs(holiday, "0 0 * * 1-5")
        self.assertEqual(skipped, [])
        self.assertIn("不是A股交易日", why[0])

        summer = datetime(2026, 10, 9, 13, tzinfo=timezone.utc)
        self.assertEqual(scheduled_jobs(summer, "0 12 * * 1-5")[0], [("usstock", ["premarket"])])
        twin, twin_why = scheduled_jobs(summer, "0 13 * * 1-5")
        self.assertEqual(twin, [])
        self.assertIn("另一套夏令时", twin_why[0])
        winter = datetime(2026, 1, 15, 13, tzinfo=timezone.utc)
        self.assertEqual(scheduled_jobs(winter, "0 13 * * 1-5")[0], [("usstock", ["premarket"])])
        self.assertEqual(scheduled_jobs(winter, "0 12 * * 1-5")[0], [])
        self.assertEqual(scheduled_jobs(datetime(2026, 1, 15, 21, 40, tzinfo=timezone.utc), "30 21 * * 1-5")[0],
                         [("usstock", ["postmarket"])])
        self.assertEqual(scheduled_jobs(datetime(2026, 10, 9, 20, 40, tzinfo=timezone.utc), "30 20 * * 1-5")[0],
                         [("usstock", ["postmarket"])])
        weekly = datetime(2026, 10, 10, 11, 20, tzinfo=CST)
        self.assertEqual(scheduled_jobs(weekly, "15 2 * * 6")[0], [("weekly", ["--backfill"])])
        self.assertEqual(scheduled_jobs(datetime(2026, 10, 11, 10, tzinfo=CST), "15 2 * * 6")[0], [])


class RestoreAndEvidenceTests(unittest.TestCase):
    def test_next_day_capture_keeps_regular_close_but_not_future_asof(self):
        day = date(2026, 9, 30)
        close = session_close(day)
        captured = datetime(2026, 10, 1, 12, tzinfo=NY)
        good = UQuote("^GSPC", "标普500", 5500, 0.4, asof=close, session="regular", observed_at=captured)
        future = UQuote("^DJI", "道琼斯", 40000, asof=captured, session="regular", observed_at=captured)
        data = UData(completed={"^GSPC": good, "^DJI": future},
                     news=[NewsItem(datetime(2026, 10, 1, 9, tzinfo=NY), "次日才发布的消息不进入昨日盘后", "见闻")])
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            archive.capture("usstock", data, captured)
            brief, _ = dated_brief(archive, "usstock", "postmarket", day, datetime(2026, 10, 5, 18, tzinfo=NY))
        self.assertEqual([quote.symbol for quote in brief.indices], ["^GSPC"])
        self.assertEqual(brief.indices[0].last, 5500)
        self.assertIsNone(brief.indices[0].observed_at)
        self.assertEqual(brief.news, [])
        self.assertIsNone(clean_quote(good, "^GSPC", datetime(2026, 10, 1, 10, tzinfo=NY)))

    def test_later_verified_value_wins_when_published_at_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
            event_id = archive.add_event(CalendarEvent("美国非农就业与失业率", at, "美国劳工统计局"), at - timedelta(days=1))
            published = at
            base = {"metric": "非农新增就业", "period": "2026-09", "unit": "万人", "verified": True,
                    "source": "美国劳工统计局", "url": "https://www.bls.gov/news.release/empsit.nr0.htm"}
            first = base | {"value": 10}
            first_id = identity([event_id, "result", published.astimezone(timezone.utc).isoformat(), first])
            later_value = next(value for value in range(11, 40)
                               if identity([event_id, "result", published.astimezone(timezone.utc).isoformat(), base | {"value": value}]) < first_id)
            record_metric(archive, event_id, "result", base | {"value": later_value}, published, at + timedelta(hours=3))
            record_metric(archive, event_id, "result", first, published, at + timedelta(hours=1))
            line = comparisons(archive.events()[0])[0]
        self.assertIn(f"{later_value:g}万人", line)
        self.assertNotIn("10万人", line)
        self.assertIn("最新人工核验", line)

    def test_missing_summary_does_not_abort_history_or_a_later_run(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as log:
            root = Path(folder)
            archive = Archive(root / "archive")
            earlier = datetime(2026, 9, 29, 18, tzinfo=CST)
            current = datetime(2026, 9, 30, 18, tzinfo=CST)
            for moment, price in ((earlier, 2900), (current, 3000)):
                data = MarketData([Quote("sh000001", "上证指数", price, trade_day=moment.date().isoformat(), session="15:30:00")])
                brief = build_brief("close", data, moment)
                capture = archive.capture("ashare", data, moment)
                archive.publish("ashare", brief, capture, lambda _brief, path: path.write_bytes(b"img"), "正文",
                                root / "output" / f"close-{moment.date()}")
            broken = next(row for row in archive.reports() if row["edition_date"] == "2026-09-29")
            (archive.root / broken["path"] / "summary.txt").unlink()
            page = build_history(archive).read_text(encoding="utf-8")
            self.assertIn("文字版缺失", page)
            self.assertIn("2026-09-30", page)
            self.assertIn("正文", page)
        self.assertIn("缺少文字版", log.getvalue())

        from ashare import __main__ as ashare
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            root = Path(folder)
            archive = Archive(root / "archive")
            moment = datetime(2026, 9, 29, 18, tzinfo=CST)
            data = MarketData([Quote("sh000001", "上证指数", 2900, trade_day="2026-09-29", session="15:30:00")])
            brief = build_brief("close", data, moment)
            capture = archive.capture("ashare", data, moment)
            archive.publish("ashare", brief, capture, lambda _brief, path: path.write_bytes(b"img"), "旧正文", root / "seed")
            (archive.root / archive.reports()[0]["path"] / "summary.txt").unlink()
            fresh = MarketData([Quote("sh000001", "上证指数", 3000, trade_day="2026-09-30", session="15:30:00")])
            with patch.object(ashare, "datetime", wraps=datetime) as clock, \
                 patch.object(ashare, "load_market", return_value=fresh), \
                 patch.object(ashare, "load_next_event", return_value=None), \
                 patch.object(ashare, "collect_releases"), \
                 patch.object(ashare, "render_png", side_effect=lambda brief, path, watermark=None: path.write_bytes(b"img")):
                clock.now.return_value = datetime(2026, 9, 30, 18, tzinfo=CST)
                ashare.main(["close", "--archive", str(archive.root), "--output", str(root / "output")])
            self.assertTrue(any(row["edition_date"] == "2026-09-30" for row in archive.reports()))


class OvernightCacheTests(unittest.TestCase):
    def test_real_overnight_trade_survives_0400_snapshot_cutoff(self):
        now = datetime(2026, 10, 1, 4, tzinfo=NY)
        traded_at = now.replace(minute=0) - timedelta(minutes=1)
        trade = UQuote("AAPL", "苹果", 102, 2, 100, asof=traded_at, session="overnight",
                       source="Yahoo Finance", observed_at=traded_at, previous_date=date(2026, 9, 30))
        snapshot = UQuote("AAPL", "苹果", 102, 2, 100, session="overnight", source="Webull",
                          observed_at=traded_at, previous_date=date(2026, 9, 30))
        self.assertTrue(_current(trade, "overnight", now))
        self.assertFalse(_current(snapshot, "overnight", now))
        self.assertTrue(_current(snapshot, "overnight", traded_at))


class CalendarReminderTests(unittest.TestCase):
    def test_november_reminds_that_next_year_is_unpublished(self):
        from ashare.calendar import coverage_note
        self.assertEqual(coverage_note(date(2026, 10, 13)), "")
        self.assertIn("2027", coverage_note(date(2026, 11, 2)))
        self.assertIn("交易所公告", coverage_note(date(2026, 12, 15)))
