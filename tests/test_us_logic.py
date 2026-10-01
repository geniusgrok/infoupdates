from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from brief_common.news import NewsItem
from usstock.calendar import (
    EARLY_CLOSE_DAYS, extended_close, edition_date, holidays, is_trading_day, last_completed_session,
    next_trading_day, previous_trading_day, session_close,
)
from usstock.compose import build_brief
from usstock.models import (
    NY, INDEX_NAMES, FUTURE_NAMES, SECTOR_NAMES, MarketData, Quote, new_york_time,
)
from usstock.narrative import activity_summary, select_news
from usstock.social import social_copy


def stamp(day: date, hour: int = 16, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


def quote(symbol: str, day: date, *, pct: float | None = 0.3, hour: int = 16,
          session: str = "regular", name: str | None = None) -> Quote:
    return Quote(symbol, name or INDEX_NAMES.get(symbol, symbol), 100, pct,
                 previous_close=99.7, asof=stamp(day, hour), session=session)


def regular_market(day: date, *, hour: int = 16) -> MarketData:
    quotes = {symbol: quote(symbol, day, hour=hour) for symbol in INDEX_NAMES}
    quotes.update({symbol: quote(symbol, day, name=name, hour=hour) for symbol, name in SECTOR_NAMES.items()})
    quotes["AAPL"] = quote("AAPL", day, name="苹果", hour=hour)
    return MarketData(completed=quotes)


class CalendarTests(unittest.TestCase):
    def test_official_2025_holidays_include_carter(self):
        self.assertEqual(holidays(2025), frozenset(date(2025, m, d) for m, d in (
            (1, 1), (1, 9), (1, 20), (2, 17), (4, 18), (5, 26), (6, 19),
            (7, 4), (9, 1), (11, 27), (12, 25))))

    def test_official_2026_holidays(self):
        self.assertEqual(holidays(2026), frozenset(date(2026, m, d) for m, d in (
            (1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19),
            (7, 3), (9, 7), (11, 26), (12, 25))))

    def test_official_2027_holidays(self):
        self.assertEqual(holidays(2027), frozenset(date(2027, m, d) for m, d in (
            (1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18),
            (7, 5), (9, 6), (11, 25), (12, 24))))

    def test_official_2028_holidays(self):
        expected = frozenset(date(2028, m, d) for m, d in (
            (1, 17), (2, 21), (4, 14), (5, 29), (6, 19),
            (7, 4), (9, 4), (11, 23), (12, 25)))
        self.assertEqual(holidays(2028), expected)
        self.assertTrue(is_trading_day(date(2027, 12, 31)))

    def test_halfday_exact_schedule(self):
        for year, days in EARLY_CLOSE_DAYS.items():
            for day in days:
                self.assertEqual(session_close(day).hour, 13)
        self.assertEqual(session_close(date(2026, 7, 2)).hour, 16)
        self.assertEqual(session_close(date(2027, 7, 2)).hour, 16)
        self.assertEqual(session_close(date(2027, 12, 23)).hour, 16)
        with self.assertRaisesRegex(ValueError, "不是"):
            session_close(date(2026, 7, 3))

    def test_official_2024_holidays(self):
        self.assertEqual(holidays(2024), frozenset(date(2024, m, d) for m, d in (
            (1, 1), (1, 15), (2, 19), (3, 29), (5, 27), (6, 19),
            (7, 4), (9, 2), (11, 28), (12, 25))))

    def test_dst_changes_actual_close_utc(self):
        before = session_close(date(2026, 3, 6)).astimezone(timezone.utc)
        after = session_close(date(2026, 3, 9)).astimezone(timezone.utc)
        self.assertEqual((before.hour, after.hour), (21, 20))
        before = session_close(date(2026, 10, 30)).astimezone(timezone.utc)
        after = session_close(date(2026, 11, 2)).astimezone(timezone.utc)
        self.assertEqual((before.hour, after.hour), (20, 21))

    def test_premarket_switches_at_open_not_noon(self):
        day = date(2026, 10, 1)
        self.assertEqual(edition_date("premarket", stamp(day, 9, 29)), day)
        self.assertEqual(edition_date("premarket", stamp(day, 9, 30)), date(2026, 10, 2))
        self.assertEqual(edition_date("premarket", stamp(day, 12)), date(2026, 10, 2))

    def test_holiday_and_weekend_skip(self):
        self.assertEqual(edition_date("premarket", stamp(date(2026, 7, 3), 8)), date(2026, 7, 6))
        self.assertEqual(next_trading_day(date(2025, 1, 8)), date(2025, 1, 10))
        self.assertEqual(previous_trading_day(date(2026, 4, 6)), date(2026, 4, 2))

    def test_postmarket_uses_completed_session_including_halfday(self):
        day = date(2026, 11, 27)
        self.assertEqual(last_completed_session(stamp(day, 12, 59)), date(2026, 11, 25))
        self.assertEqual(last_completed_session(stamp(day, 13)), day)
        self.assertEqual(edition_date("postmarket", stamp(date(2026, 10, 1), 15, 59)), date(2026, 9, 30))

    def test_newyear_previous_session_boundary_supported(self):
        self.assertEqual(last_completed_session(stamp(date(2025, 1, 2), 8)), date(2024, 12, 31))

    def test_unknown_year_and_invalid_kind_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "尚未覆盖2029年"):
            is_trading_day(date(2029, 2, 1))
        with self.assertRaisesRegex(ValueError, "kind"):
            edition_date("morning", stamp(date(2026, 10, 1), 8))


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.previous = date(2026, 9, 30)
        self.today = date(2026, 10, 1)

    def test_intraday_regular_cannot_replace_completed_previous(self):
        data = regular_market(self.previous)
        data.quotes = {symbol: quote(symbol, self.today, hour=12) for symbol in INDEX_NAMES}
        brief = build_brief("postmarket", data, stamp(self.today, 12))
        self.assertEqual(brief.edition_date, self.previous)
        self.assertEqual(brief.reference_date, self.previous)
        self.assertTrue(all(item.trade_date == self.previous for item in brief.indices))
        self.assertTrue(brief.complete)

    def test_intraday_with_no_history_does_not_claim_close(self):
        data = MarketData(quotes={symbol: quote(symbol, self.today, hour=12) for symbol in INDEX_NAMES})
        brief = build_brief("postmarket", data, stamp(self.today, 12))
        self.assertEqual(brief.indices, [])
        self.assertEqual(brief.sentiment, "待确认")
        self.assertFalse(brief.complete)

    def test_old_daily_bar_does_not_hide_newer_verified_regular_close(self):
        data = regular_market(self.previous)
        data.quotes = {symbol: quote(symbol, self.today) for symbol in INDEX_NAMES}
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertEqual(brief.reference_date, self.today)
        self.assertTrue(all(item.trade_date == self.today for item in brief.indices))

    def test_historical_partial_snapshot_is_not_complete(self):
        data = MarketData(completed={symbol: quote(symbol, self.previous, hour=12) for symbol in INDEX_NAMES})
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertEqual(brief.indices, [])

    def test_regular_meta_after_close_is_eligible(self):
        data = MarketData(quotes={symbol: quote(symbol, self.today, hour=17) for symbol in INDEX_NAMES})
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertEqual(len(brief.indices), 4)
        self.assertTrue(brief.complete)

    def test_quote_dates_align_all_regular_groups(self):
        data = regular_market(self.previous)
        data.completed["^GSPC"] = quote("^GSPC", self.today)
        data.completed["AAPL"] = quote("AAPL", self.today)
        data.completed["XLK"] = quote("XLK", self.today)
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertEqual(brief.reference_date, self.today)
        self.assertEqual([item.symbol for item in brief.indices], ["^GSPC"])
        self.assertEqual([item.symbol for item in brief.stocks], ["AAPL"])
        self.assertEqual([item.symbol for item in brief.sectors], ["XLK"])
        self.assertFalse(brief.complete)
        self.assertTrue(any("日期不一致" in note for note in brief.notes))

    def test_future_quote_and_holiday_quote_rejected(self):
        data = regular_market(self.previous)
        data.completed["^GSPC"] = quote("^GSPC", date(2026, 10, 2))
        data.completed["AAPL"] = quote("AAPL", date(2026, 7, 3))
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertNotIn("^GSPC", [item.symbol for item in brief.indices])
        self.assertNotIn("AAPL", [item.symbol for item in brief.stocks])

    def test_postmarket_extended_never_mixes_into_close(self):
        data = regular_market(self.today)
        data.postmarket["AAPL"] = quote("AAPL", self.today, pct=14, hour=18, session="postmarket")
        data.completed["MSFT"] = quote("MSFT", self.today, pct=11, hour=18, session="postmarket")
        brief = build_brief("postmarket", data, stamp(self.today, 19))
        self.assertEqual([item.symbol for item in brief.stocks], ["AAPL"])
        self.assertEqual(brief.stocks[0].pct, 0.3)
        self.assertEqual(brief.stocks_label, "常规收盘 / 盘后")
        self.assertEqual([item.symbol for item in brief.extended_stocks], ["AAPL"])
        self.assertIsNone(brief.extended_stocks[0].pct)

    def test_extended_matches_regular_day_base_and_actual_time(self):
        data = regular_market(self.today)
        extended = quote("AAPL", self.today, pct=2, hour=18, session="postmarket")
        extended.previous_date = self.today
        extended.previous_close = 100
        data.postmarket["AAPL"] = extended
        brief = build_brief("postmarket", data, stamp(self.today, 19))
        self.assertEqual(brief.stocks[0].pct, 0.3)
        self.assertEqual(brief.extended_stocks[0].pct, 2)
        self.assertIn("盘后延长交易（相对当日常规收盘）", social_copy(brief))

    def test_extended_wrong_date_future_or_outside_window_excluded(self):
        data = regular_market(self.today)
        for extended in (quote("AAPL", self.previous, hour=18, session="postmarket"),
                         quote("AAPL", self.today, hour=15, session="postmarket"),
                         quote("AAPL", self.today, hour=20, session="postmarket")):
            with self.subTest(extended=extended):
                data.postmarket["AAPL"] = extended
                brief = build_brief("postmarket", data, stamp(self.today, 19))
                self.assertEqual(brief.extended_stocks, [])

    def test_halfday_extended_ends_at_17(self):
        day = date(2026, 11, 27)
        self.assertEqual(extended_close(day).hour, 17)
        self.assertEqual(extended_close(self.today).hour, 20)
        data = regular_market(day, hour=13)
        data.postmarket["AAPL"] = quote("AAPL", day, hour=17, session="postmarket")
        brief = build_brief("postmarket", data, stamp(day, 18))
        self.assertEqual(brief.extended_stocks, [])

    def test_halfday_extended_starts_at_13(self):
        day = date(2026, 11, 27)
        data = regular_market(day, hour=13)
        extended = quote("AAPL", day, pct=2, hour=14, session="postmarket")
        extended.previous_date = day
        data.postmarket["AAPL"] = extended
        brief = build_brief("postmarket", data, stamp(day, 15))
        self.assertEqual(len(brief.extended_stocks), 1)

    def test_macro_yield_preserves_its_own_actual_session(self):
        data = regular_market(self.today)
        data.quotes["^TNX"] = quote("^TNX", self.today, hour=14, session="regular")
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertEqual(brief.references[0].asof.hour, 14)
        self.assertEqual(brief.references[0].session, "reference")

    def test_macro_completed_date_does_not_trigger_regular_alignment_warning(self):
        data = regular_market(self.today)
        data.completed["^TNX"] = quote("^TNX", self.previous)
        data.quotes["^TNX"] = quote("^TNX", self.today, hour=14)
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertFalse(any("日期不一致" in note for note in brief.notes))
        treasury = next(item for item in brief.references if item.symbol == "^TNX")
        self.assertEqual(treasury.trade_date, self.today)
        self.assertEqual(treasury.asof.hour, 14)
        for symbol in ("^DJI", "AAPL", "XLK", "SPY"):
            with self.subTest(symbol=symbol):
                stale = regular_market(self.today)
                stale.completed["^TNX"] = quote("^TNX", self.previous)
                stale.quotes["^TNX"] = quote("^TNX", self.today, hour=14)
                stale.completed[symbol] = quote(symbol, self.previous)
                brief = build_brief("postmarket", stale, stamp(self.today, 18))
                self.assertTrue(any("日期不一致" in note for note in brief.notes))

    def test_current_premarket_overrides_previous_regular(self):
        data = regular_market(self.previous)
        data.premarket["AAPL"] = quote("AAPL", self.today, pct=2, hour=8, session="premarket")
        data.premarket["AAPL"].previous_date = self.previous
        brief = build_brief("premarket", data, stamp(self.today, 8, 30))
        self.assertEqual(brief.stocks_label, "盘前行情")
        self.assertEqual(brief.stocks[0].pct, 2)
        self.assertEqual(brief.stocks[0].session, "premarket")
        self.assertEqual(brief.reference_date, self.previous)

    def test_partial_premarket_does_not_fill_missing_stock_with_regular(self):
        data = regular_market(self.previous)
        data.completed["MSFT"] = quote("MSFT", self.previous)
        data.premarket["AAPL"] = quote("AAPL", self.today, pct=2, hour=8, session="premarket")
        data.premarket["AAPL"].previous_date = self.previous
        brief = build_brief("premarket", data, stamp(self.today, 8, 30))
        self.assertEqual(brief.stocks_label, "盘前行情")
        self.assertEqual([item.symbol for item in brief.stocks], ["AAPL"])
        self.assertTrue(any("MSFT" in note and "暂缺" in note for note in brief.notes))

    def test_premarket_stale_future_or_outside_hours_stays_missing(self):
        for raw in (quote("AAPL", self.previous, hour=8, session="premarket"),
                    quote("AAPL", self.today, hour=9, session="premarket"),
                    quote("AAPL", self.today, hour=3, session="premarket")):
            with self.subTest(raw=raw):
                data = regular_market(self.previous)
                data.premarket["AAPL"] = raw
                brief = build_brief("premarket", data, stamp(self.today, 8))
                self.assertEqual(brief.stocks, [])
                self.assertEqual(brief.stocks_label, "行情暂缺")

    def test_next_edition_cannot_relabel_today_premarket(self):
        data = regular_market(self.previous)
        data.premarket["AAPL"] = quote("AAPL", self.today, hour=8, session="premarket")
        brief = build_brief("premarket", data, stamp(self.today, 12))
        self.assertEqual(brief.edition_date, date(2026, 10, 2))
        self.assertEqual(brief.stocks_label, "行情暂缺")
        self.assertEqual(brief.stocks, [])

    def test_futures_retain_actual_asof_and_basis(self):
        data = regular_market(self.previous)
        data.quotes["ES=F"] = quote("ES=F", self.today, hour=8, session="futures")
        brief = build_brief("premarket", data, stamp(self.today, 8, 30))
        self.assertEqual(brief.futures[0].asof, stamp(self.today, 8))
        self.assertEqual(brief.futures[0].session, "futures")

    def test_nan_last_dropped_nan_pct_unknown(self):
        data = regular_market(self.today)
        data.completed["^GSPC"].last = float("nan")
        data.completed["^IXIC"].pct = float("inf")
        brief = build_brief("postmarket", data, stamp(self.today, 18))
        self.assertNotIn("^GSPC", [item.symbol for item in brief.indices])
        self.assertIsNone(next(item for item in brief.indices if item.symbol == "^IXIC").pct)
        self.assertEqual(brief.sentiment, "待确认")

    def test_stale_complete_indices_mark_incomplete(self):
        brief = build_brief("postmarket", regular_market(self.previous), stamp(self.today, 18))
        self.assertFalse(brief.complete)
        self.assertEqual(brief.reference_date, self.previous)
        self.assertIn("参考收盘情绪", brief.market_summary)
        self.assertTrue(any("行情仅到" in note for note in brief.notes))

    def test_utc_generation_converted_to_ny_date(self):
        now = datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc)
        brief = build_brief("postmarket", regular_market(self.today), now)
        self.assertEqual(brief.generated_at, stamp(self.today, 20, 30))
        self.assertEqual(brief.edition_date, self.today)
        self.assertEqual(new_york_time(datetime(2026, 10, 1, 8)).hour, 8)

    def test_empty_data_has_no_fake_zero_or_breadth(self):
        brief = build_brief("postmarket", MarketData(), stamp(self.today, 18))
        self.assertEqual(brief.sentiment, "待确认")
        self.assertEqual(brief.activity, None)
        copy = social_copy(brief)
        self.assertNotIn("0家", copy)
        self.assertNotIn("主力", copy)
        self.assertNotIn("成交额 0", copy)


class LatestPremarketTests(unittest.TestCase):
    def make_extended(self, symbol, moment, session, basis, *, pct=2):
        return Quote(symbol, symbol, 102, pct, previous_close=100, asof=moment,
                     session=session, previous_date=basis, source="真实延长交易源")

    def test_regular_stocks_and_etfs_are_never_premarket_fallback(self):
        today = date(2026, 10, 1)
        brief = build_brief("premarket", regular_market(date(2026, 9, 30)), stamp(today, 8))
        self.assertEqual(brief.stocks, [])
        self.assertEqual(brief.sectors, [])
        self.assertEqual(brief.sentiment, "待确认")
        self.assertFalse(brief.complete)
        copy = social_copy(brief)
        self.assertNotIn("主指数（完成常规场）", copy)
        self.assertNotIn("前收三大指数", copy)
        self.assertNotIn("常规场参考：", copy)
        self.assertIn("比较基准日期：2026-09-30", copy)

    def test_overnight_before_and_after_midnight_same_edition(self):
        target = date(2026, 10, 1)
        basis = date(2026, 9, 30)
        for now, at in ((stamp(basis, 23, 30), stamp(basis, 23)),
                        (stamp(target, 1), stamp(target, 0))):
            with self.subTest(now=now):
                data = regular_market(basis)
                data.overnight["AAPL"] = self.make_extended("AAPL", at, "overnight", basis)
                brief = build_brief("premarket", data, now)
                self.assertEqual(brief.edition_date, target)
                self.assertEqual(brief.stocks[0].asof, at)
                self.assertEqual(brief.stocks[0].session, "overnight")
                self.assertEqual(brief.stocks[0].pct, 2)
                self.assertEqual(brief.stocks_label, "夜盘行情")
                copy = social_copy(brief)
                self.assertIn("夜盘", copy)
                self.assertIn("真实延长交易源", copy)

    def test_latest_asof_selected_across_pre_night_and_post(self):
        target = date(2026, 10, 1)
        basis = date(2026, 9, 30)
        data = regular_market(basis)
        data.postmarket["AAPL"] = self.make_extended("AAPL", stamp(basis, 19), "postmarket", basis)
        data.overnight["AAPL"] = self.make_extended("AAPL", stamp(target, 2), "overnight", basis)
        data.premarket["AAPL"] = self.make_extended("AAPL", stamp(target, 7), "premarket", basis)
        data.overnight["XLK"] = self.make_extended("XLK", stamp(target, 3), "overnight", basis)
        brief = build_brief("premarket", data, stamp(target, 8))
        self.assertEqual(brief.stocks[0].asof, stamp(target, 7))
        self.assertEqual(brief.stocks[0].session, "premarket")
        self.assertEqual([item.symbol for item in brief.sectors], ["XLK"])
        self.assertEqual(brief.sectors[0].session, "overnight")

    def test_recent_post_reference_labeled_and_missing_night_disclosed(self):
        basis = date(2026, 9, 30)
        target = date(2026, 10, 1)
        data = regular_market(basis)
        data.postmarket["AAPL"] = self.make_extended("AAPL", stamp(basis, 19), "postmarket", basis)
        brief = build_brief("premarket", data, stamp(target, 1))
        self.assertEqual(brief.stocks_label, "盘后参考")
        self.assertEqual(brief.stocks[0].session, "postmarket")
        self.assertTrue(any("夜盘暂缺" in note and "AAPL" in note for note in brief.notes))

    def test_sunday_night_is_monday_but_friday_post_cannot_fill(self):
        friday = date(2026, 10, 2)
        sunday = date(2026, 10, 4)
        monday = date(2026, 10, 5)
        data = regular_market(friday)
        data.postmarket["MSFT"] = self.make_extended("MSFT", stamp(friday, 19), "postmarket", friday)
        data.overnight["AAPL"] = self.make_extended("AAPL", stamp(sunday, 23), "overnight", friday)
        brief = build_brief("premarket", data, stamp(monday, 1))
        self.assertEqual(brief.edition_date, monday)
        self.assertEqual([item.symbol for item in brief.stocks], ["AAPL"])
        self.assertEqual(brief.stocks[0].previous_date, friday)

    def test_current_friday_post_can_reference_monday_edition(self):
        friday = date(2026, 10, 2)
        data = regular_market(friday)
        data.postmarket["AAPL"] = self.make_extended("AAPL", stamp(friday, 19), "postmarket", friday)
        brief = build_brief("premarket", data, stamp(friday, 19, 30))
        self.assertEqual(brief.edition_date, date(2026, 10, 5))
        self.assertEqual(brief.stocks[0].session, "postmarket")
        self.assertEqual(brief.stocks_label, "盘后参考")
        self.assertTrue(any("夜盘暂缺" in note for note in brief.notes))

    def test_holiday_stale_overnight_rejected(self):
        thursday = date(2026, 7, 2)
        monday = date(2026, 7, 6)
        data = regular_market(thursday)
        data.overnight["AAPL"] = self.make_extended("AAPL", stamp(thursday, 23), "overnight", thursday)
        data.postmarket["MSFT"] = self.make_extended("MSFT", stamp(thursday, 19), "postmarket", thursday)
        brief = build_brief("premarket", data, stamp(monday, 1))
        self.assertEqual(brief.stocks, [])

    def test_wrong_base_keeps_actual_price_but_disables_pct(self):
        target = date(2026, 10, 1)
        data = MarketData()
        data.overnight["AAPL"] = self.make_extended("AAPL", stamp(target, 1), "overnight", date(2026, 9, 29))
        brief = build_brief("premarket", data, stamp(target, 2))
        self.assertEqual(brief.stocks[0].last, 102)
        self.assertIsNone(brief.stocks[0].pct)
        self.assertEqual(brief.reference_date, date(2026, 9, 30))

    def test_symbol_mismatch_and_future_night_rejected(self):
        target = date(2026, 10, 1)
        basis = date(2026, 9, 30)
        data = MarketData()
        data.overnight["AAPL"] = self.make_extended("MSFT", stamp(target, 1), "overnight", basis)
        data.overnight["NVDA"] = self.make_extended("NVDA", stamp(target, 3), "overnight", basis)
        brief = build_brief("premarket", data, stamp(target, 2))
        self.assertEqual(brief.stocks, [])

    def test_cross_midnight_recent_futures_drive_current_premarket(self):
        basis = date(2026, 9, 30)
        target = date(2026, 10, 1)
        data = regular_market(basis)
        for symbol in FUTURE_NAMES:
            data.quotes[symbol] = quote(symbol, basis, hour=23, pct=1.2, session="futures")
        brief = build_brief("premarket", data, stamp(target, 1))
        self.assertEqual(brief.sentiment, "高涨")
        self.assertEqual(brief.headline, "股指期货集体走高")

    def test_stale_or_before_completed_close_futures_cannot_drive_mood(self):
        basis = date(2026, 9, 30)
        target = date(2026, 10, 1)
        for hour, now in ((17, stamp(target, 1)), (15, stamp(basis, 18))):
            with self.subTest(hour=hour):
                data = regular_market(basis)
                for symbol in FUTURE_NAMES:
                    data.quotes[symbol] = quote(symbol, basis, hour=hour, pct=2, session="futures")
                brief = build_brief("premarket", data, now)
                self.assertEqual(brief.sentiment, "待确认")
                self.assertNotIn("前收", brief.market_summary)

    def test_post_only_reference_cannot_drive_current_premarket_mood(self):
        basis = date(2026, 9, 30)
        target = date(2026, 10, 1)
        data = regular_market(basis)
        for symbol in ("AAPL", "MSFT", "NVDA"):
            data.postmarket[symbol] = self.make_extended(symbol, stamp(basis, 19), "postmarket", basis, pct=2)
        brief = build_brief("premarket", data, stamp(target, 2))
        self.assertEqual(len(brief.stocks), 3)
        self.assertEqual(brief.sentiment, "待确认")
        self.assertEqual(brief.stocks_label, "盘后参考")

    def test_dst_fold_future_macro_quote_is_not_accepted(self):
        now = datetime(2026, 11, 1, 1, 45, tzinfo=NY, fold=0)
        future = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        data = regular_market(date(2026, 10, 30))
        data.quotes["CL=F"] = Quote("CL=F", "原油", 80, 1.2, asof=future, session="futures")
        brief = build_brief("premarket", data, now)
        self.assertNotIn("CL=F", [item.symbol for item in brief.references])

    def test_latest_extended_stocks_can_drive_mood_without_futures(self):
        basis = date(2026, 9, 30)
        target = date(2026, 10, 1)
        data = regular_market(basis)
        for symbol in ("AAPL", "MSFT", "NVDA"):
            data.overnight[symbol] = self.make_extended(symbol, stamp(target, 1), "overnight", basis, pct=1.2)
        brief = build_brief("premarket", data, stamp(target, 2))
        self.assertEqual(brief.sentiment, "高涨")
        self.assertIn("大型科技股", brief.market_summary)
        self.assertNotIn("前收", brief.headline)


class ActivityAndNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 1)
        data = regular_market(self.today)
        data.completed["SPY"] = Quote("SPY", "标普ETF", 600, 0.3, 598,
                                      stamp(self.today), volume=70_000_000,
                                      previous_volume=60_000_000, previous_date=date(2026, 9, 30))
        self.data = data

    def test_spy_whole_day_volume_has_share_units(self):
        brief = build_brief("postmarket", self.data, stamp(self.today, 18))
        self.assertIn("SPY放量", brief.market_summary)
        self.assertIn("增加1000.0万股", brief.market_summary)
        self.assertNotIn("亿元", brief.market_summary)
        self.assertIn("指数与板块ETF参考", brief.market_summary)

    def test_small_volume_change_preserves_nonzero_share_count(self):
        self.data.completed["SPY"].volume = 60_000_100
        brief = build_brief("postmarket", self.data, stamp(self.today, 18))
        self.assertIn("增加100股", brief.market_summary)
        self.assertNotIn("0.0万股", brief.market_summary)

    def test_flat_indices_do_not_claim_divergence(self):
        for item in self.data.completed.values():
            item.pct = 0
        brief = build_brief("postmarket", self.data, stamp(self.today, 18))
        self.assertEqual(brief.headline, "三大指数基本持平")

    def test_bad_previous_date_disables_volume_comparison(self):
        self.data.completed["SPY"].previous_date = date(2026, 9, 29)
        brief = build_brief("postmarket", self.data, stamp(self.today, 18))
        self.assertEqual(activity_summary(brief), "SPY量能待确认")

    def test_zero_missing_and_nonfinite_previous_volume_unknown(self):
        for previous in (0, None, float("nan"), -1):
            with self.subTest(previous=previous):
                self.data.completed["SPY"].previous_volume = previous
                brief = build_brief("postmarket", self.data, stamp(self.today, 18))
                self.assertEqual(activity_summary(brief), "SPY量能待确认")

    def test_partial_day_premarket_never_claims_shrinkage(self):
        brief = build_brief("premarket", self.data, stamp(date(2026, 10, 2), 8))
        self.assertIn("量能待开盘确认", brief.market_summary)
        self.assertNotIn("SPY放量", brief.market_summary)
        self.assertNotIn("缩量", brief.market_summary)

    def test_premarket_current_futures_mood_is_labeled_proxy(self):
        now = stamp(date(2026, 10, 2), 8, 30)
        for symbol in FUTURE_NAMES:
            self.data.quotes[symbol] = quote(symbol, now.date(), hour=8, pct=1.1, session="futures")
        brief = build_brief("premarket", self.data, now)
        self.assertEqual(brief.sentiment, "高涨")
        self.assertIn("股指期货参考", brief.market_summary)
        self.assertEqual(brief.headline, "股指期货集体走高")

    def test_positive_negative_and_flat_sentiments(self):
        for pct, expected in ((1.2, "高涨"), (-1.2, "低落"), (0, "平淡")):
            with self.subTest(pct=pct):
                for item in self.data.completed.values():
                    item.pct = pct
                brief = build_brief("postmarket", self.data, stamp(self.today, 18))
                self.assertEqual(brief.sentiment, expected)


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.now = stamp(date(2026, 10, 1), 8)
        self.reference = date(2026, 9, 30)

    def select(self, items, limit=6):
        return select_news(items, kind="premarket", reference_date=self.reference, now=self.now, limit=limit)

    def test_future_stale_and_a_share_news_excluded(self):
        items = [
            NewsItem(self.now, "美国最新PCE数据即将发布，美股期货小涨", "a"),
            NewsItem(self.now + timedelta(seconds=1), "美联储未来新闻，美股将创新高", "b"),
            NewsItem(self.now - timedelta(days=8), "美国CPI数据显示通胀重新走高", "c"),
            NewsItem(self.now, "沪深两市成交额超过万亿元，A股普涨", "d"),
            NewsItem(self.now, "中国CPI数据即将发布，国内市场关注", "e"),
        ]
        self.assertEqual([item.source for item in self.select(items)], ["a"])

    def test_related_chinese_earnings_and_ticker_supported(self):
        items = [NewsItem(self.now, "NVDA公布最新财报，营收超过预期", "a"),
                 NewsItem(self.now, "英伟达盘前上涨，市场等待美国就业数据", "b"),
                 NewsItem(self.now, "US stocks rose in premarket trading", "c")]
        self.assertEqual(len(self.select(items)), 2)

    def test_precise_dedup_keeps_different_numeric_events(self):
        items = [NewsItem(self.now, "美国CPI同比上涨0.5%，美元指数走强", "a"),
                 NewsItem(self.now, "美国CPI同比上涨0.6%，美元指数走强", "b"),
                 NewsItem(self.now, "美国 CPI 同比上涨0.5%，美元指数走强。", "c", source_score=2)]
        chosen = self.select(items)
        self.assertEqual(len(chosen), 2)
        self.assertEqual(chosen[0].source, "c")
        self.assertIn("b", [item.source for item in chosen])

    def test_premarket_latest_news_precedes_older_high_rank(self):
        items = [NewsItem(self.now - timedelta(hours=2), "美联储最新PCE数据与非农就业报告公布", "old", source_score=3),
                 NewsItem(self.now, "微软发布最新产品，美国市场关注", "new")]
        self.assertEqual([item.source for item in self.select(items)], ["new", "old"])
        chosen = select_news(items, kind="postmarket", reference_date=self.reference, now=self.now)
        self.assertEqual([item.source for item in chosen], ["old", "new"])

    def test_premarket_duplicate_prefers_latest_then_same_time_source_rank(self):
        title = "美国科技公司公布财报，市场关注盈利变化"
        items = [NewsItem(self.now - timedelta(hours=1), title, "old", source_score=5),
                 NewsItem(self.now, title, "new"),
                 NewsItem(self.now, title, "best", source_score=2)]
        self.assertEqual([item.source for item in self.select(items)], ["best"])

    def test_premarket_roundup_without_event_does_not_occupy_news_slot(self):
        event = "华尔街见闻早餐：美国CPI回落，微软最新财报超预期"
        pure = "华尔街见闻早餐 | 2026年10月1日"
        items = [NewsItem(self.now, pure, "roundup"),
                 NewsItem(self.now, "美股早报 | 2026-10-01", "morning"),
                 NewsItem(self.now, "华尔街见闻晚报｜10月1日", "evening"),
                 NewsItem(self.now, event, "event")]
        self.assertEqual([item.source for item in self.select(items)], ["event"])
        post = select_news(items, kind="postmarket", reference_date=self.reference, now=self.now)
        self.assertIn("roundup", [item.source for item in post])

    def test_dst_fold_future_news_is_excluded_by_actual_time(self):
        now = datetime(2026, 11, 1, 1, 45, tzinfo=NY, fold=0)
        future = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        item = NewsItem(future, "美国科技公司公布最新财报与经济预期", "future")
        chosen = select_news([item], kind="premarket", reference_date=date(2026, 10, 30), now=now)
        self.assertEqual(chosen, [])

    def test_dst_fold_past_news_is_retained_by_actual_time(self):
        now = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        past = datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0)
        item = NewsItem(past, "美国科技公司公布最新财报与经济预期", "past")
        chosen = select_news([item], kind="premarket", reference_date=date(2026, 10, 30), now=now)
        self.assertEqual([item.source for item in chosen], ["past"])

    def test_latest_duplicate_uses_elapsed_time_during_dst_fall_back(self):
        day = date(2026, 11, 1)
        title = "美国科技公司公布财报，市场关注盈利变化"
        old = datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0)
        latest = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        items = [NewsItem(old, title, "old", source_score=5), NewsItem(latest, title, "new")]
        chosen = select_news(items, kind="premarket", reference_date=date(2026, 10, 30), now=stamp(day, 3))
        self.assertEqual([item.source for item in chosen], ["new"])

    def test_postmarket_same_rank_duplicate_uses_latest_elapsed_time(self):
        now = stamp(date(2026, 11, 1), 3)
        title = "美国科技公司公布最新财报与经济预期"
        old = datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0)
        latest = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        items = [NewsItem(old, title, "old"), NewsItem(latest, title, "new")]
        chosen = select_news(items, kind="postmarket", reference_date=date(2026, 10, 30), now=now)
        self.assertEqual([item.source for item in chosen], ["new"])

    def test_latest_news_remains_available_before_tomorrow_base_close(self):
        today = date(2026, 10, 1)
        now = stamp(today, 12)
        item = NewsItem(now, "美国就业数据公布，科技股与美股期货受到关注", "new")
        data = regular_market(date(2026, 9, 30))
        data.news = [item]
        brief = build_brief("premarket", data, now)
        self.assertEqual(brief.edition_date, date(2026, 10, 2))
        self.assertEqual([item.source for item in brief.news], ["new"])

    def test_news_naive_chinese_time_normalized(self):
        naive_china = datetime(2026, 10, 1, 20)
        chosen = self.select([NewsItem(naive_china, "美国ADP就业数据公布，市场关注美联储", "a")])
        self.assertEqual(chosen[0].published, self.now)

    def test_zero_limit_empty_and_invalid_kind_explicit(self):
        item = NewsItem(self.now, "美国就业数据公布，美股期货上涨", "a")
        self.assertEqual(self.select([item], limit=0), [])
        with self.assertRaisesRegex(ValueError, "kind"):
            select_news([item], kind="morning", reference_date=self.reference, now=self.now)

    def test_weekend_premarket_retains_friday_after_close(self):
        now = stamp(date(2026, 10, 5), 8)
        after_friday = stamp(date(2026, 10, 2), 17)
        items = [NewsItem(after_friday, "美国最新政策影响美股，科技公司回应", "a")]
        chosen = select_news(items, kind="premarket", reference_date=date(2026, 10, 2), now=now)
        self.assertEqual(len(chosen), 1)


if __name__ == "__main__":
    unittest.main()
