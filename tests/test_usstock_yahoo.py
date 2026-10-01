from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from usstock.sources.yahoo import _fetch_chart, load
from usstock.models import NY, Quote
from usstock.sources.yahoo import parse_completed, parse_extended, parse_history, parse_overnight, parse_quote


def stamp(day: str, clock: str = "09:30") -> int:
    return int(datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=NY).timestamp())


def chart(symbol="AAPL", *, timestamps=None, closes=None, volumes=None, asof=None, last=120, **meta):
    timestamps = timestamps if timestamps is not None else [stamp("2026-09-29"), stamp("2026-09-30")]
    closes = closes if closes is not None else [100, 120]
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "regularMarketTime": asof or stamp("2026-09-30", "16:00"),
                 "regularMarketPrice": last, "chartPreviousClose": 999, **meta},
        "timestamp": timestamps,
        "indicators": {"quote": [{"close": closes, "volume": volumes if volumes is not None else [1000] * len(closes)}]},
    }]}}


NOW = datetime(2026, 9, 30, 21, tzinfo=NY)


def night_price(**overrides):
    return {
        "symbol": "AAPL", "currency": "USD", "marketState": "OVERNIGHT",
        "overnightMarketPrice": {"raw": 110},
        "overnightMarketTime": stamp("2026-09-30", "23:00"),
        "overnightMarketSource": "BOATS Real Time Price",
        "regularMarketPrice": {"raw": 100},
        "regularMarketTime": stamp("2026-09-30", "16:00"),
        "overnightMarketChangePercent": {"raw": 0.1},
        **overrides,
    }


def night_html(*prices, status=200):
    body = json.dumps({"quoteSummary": {"result": [{"price": price} for price in prices]}})
    script = json.dumps({"status": status, "body": body})
    return f'<script type="application/json" data-sveltekit-fetched>{script}</script>'


class DailyParsingTests(unittest.TestCase):
    def test_history_uses_real_capture_time_and_excludes_unfinished_bar(self):
        payload = chart(timestamps=[stamp("2026-09-21"), stamp("2026-09-22")], closes=[100, 110],
                        asof=stamp("2026-09-30", "16:00"))
        history = parse_history(payload, "AAPL", "苹果", now=NOW)
        self.assertEqual(set(history), {date(2026, 9, 21), date(2026, 9, 22)})
        self.assertEqual(history[date(2026, 9, 22)].asof.hour, 16)
        self.assertAlmostEqual(history[date(2026, 9, 22)].pct, 10)
        intraday = parse_history(payload, "AAPL", "苹果", now=datetime(2026, 9, 22, 12, tzinfo=NY))
        self.assertNotIn(date(2026, 9, 22), intraday)

    def test_previous_close_is_previous_daily_bar_not_chart_range_start(self):
        value = parse_quote(chart(), "AAPL", "苹果", now=NOW)
        self.assertEqual(value.previous_close, 100)
        self.assertAlmostEqual(value.pct, 20)
        self.assertEqual(value.previous_date, date(2026, 9, 29))

    def test_intraday_quote_does_not_replace_previous_complete_day(self):
        now = datetime(2026, 9, 30, 12, tzinfo=NY)
        payload = chart(timestamps=[stamp("2026-09-28"), stamp("2026-09-29"), stamp("2026-09-30")],
                        closes=[90, 100, 111], volumes=[800, 1000, 400], asof=stamp("2026-09-30", "12:00"), last=111)
        latest = parse_quote(payload, "AAPL", "苹果", now=now)
        complete = parse_completed(payload, "AAPL", "苹果", now=now)
        self.assertEqual(latest.last, 111)
        self.assertEqual(complete.last, 100)
        self.assertEqual(complete.trade_date, date(2026, 9, 29))
        self.assertEqual(complete.asof.hour, 16)
        self.assertEqual((complete.volume, complete.previous_volume), (1000, 800))

    def test_completed_same_day_only_after_close(self):
        before = datetime(2026, 9, 30, 15, 59, tzinfo=NY)
        at_close = datetime(2026, 9, 30, 16, tzinfo=NY)
        self.assertEqual(parse_completed(chart(), "AAPL", "苹果", now=before).trade_date, date(2026, 9, 29))
        self.assertEqual(parse_completed(chart(), "AAPL", "苹果", now=at_close).trade_date, date(2026, 9, 30))

    def test_early_close_bar_is_completed_at_one_pm(self):
        payload = chart(timestamps=[stamp("2026-11-25"), stamp("2026-11-27")], closes=[100, 101],
                        asof=stamp("2026-11-27", "13:00"), last=101)
        now = datetime(2026, 11, 27, 13, tzinfo=NY)
        value = parse_completed(payload, "AAPL", "苹果", now=now)
        self.assertEqual(value.trade_date, date(2026, 11, 27))
        self.assertEqual(value.asof.hour, 13)
        self.assertAlmostEqual(value.pct, 1)

    def test_stale_intraday_source_after_wall_clock_close_is_not_completed(self):
        payload = chart(asof=stamp("2026-09-30", "15:55"))
        now = datetime(2026, 9, 30, 16, 5, tzinfo=NY)
        value = parse_completed(payload, "AAPL", "苹果", now=now)
        self.assertEqual(value.trade_date, date(2026, 9, 29))
        self.assertEqual(value.last, 100)

    def test_latest_stale_daily_source_on_next_day_is_still_not_completed(self):
        payload = chart(asof=stamp("2026-09-30", "15:55"))
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        self.assertEqual(parse_completed(payload, "AAPL", "苹果", now=now).trade_date, date(2026, 9, 29))

    def test_daily_close_disagreement_with_latest_regular_price_is_not_completed(self):
        payload = chart(closes=[100, 110], last=120)
        value = parse_completed(payload, "AAPL", "苹果", now=NOW)
        self.assertEqual(value.trade_date, date(2026, 9, 29))
        self.assertEqual(value.last, 100)

    def test_unsupported_old_history_does_not_discard_valid_latest_day(self):
        payload = chart(timestamps=[stamp("2023-12-29"), stamp("2024-01-02")],
                        asof=stamp("2024-01-02", "16:00"))
        now = datetime(2024, 1, 2, 17, tzinfo=NY)
        for parser in (parse_quote, parse_completed):
            value = parser(payload, "AAPL", "苹果", now=now)
            self.assertEqual(value.last, 120)
            self.assertIsNone(value.pct)

    def test_missing_immediate_previous_day_never_uses_older_day(self):
        payload = chart(timestamps=[stamp("2026-09-28"), stamp("2026-09-30")], closes=[50, 120])
        value = parse_quote(payload, "AAPL", "苹果", now=NOW)
        self.assertIsNone(value.pct)
        self.assertIsNone(value.previous_close)

    def test_invalid_quote_and_future_source_time_are_rejected(self):
        for last in (float("nan"), float("inf"), -1, 0, True):
            with self.subTest(last=last):
                self.assertIsNone(parse_quote(chart(last=last), "AAPL", "苹果", now=NOW))
        self.assertIsNone(parse_quote(chart(asof=stamp("2026-10-01", "10:00")), "AAPL", "苹果", now=NOW))

    def test_payload_symbol_and_bar_date_must_match_quote(self):
        self.assertIsNone(parse_quote(chart("MSFT"), "AAPL", "苹果", now=NOW))
        self.assertIsNone(parse_quote(chart(timestamps=[stamp("2026-09-29")], closes=[100]), "AAPL", "苹果", now=NOW))

    def test_nonfinite_previous_bar_yields_missing_change(self):
        value = parse_quote(chart(closes=[float("inf"), 120]), "AAPL", "苹果", now=NOW)
        self.assertEqual(value.last, 120)
        self.assertIsNone(value.pct)

    def test_volume_zero_is_real_but_negative_and_nonfinite_are_missing(self):
        value = parse_completed(chart(volumes=[200, 0]), "AAPL", "苹果", now=NOW)
        self.assertEqual(value.volume, 0)
        for volume in (-1, float("nan"), float("inf")):
            self.assertIsNone(parse_completed(chart(volumes=[200, volume]), "AAPL", "苹果", now=NOW).volume)

    def test_regular_asof_keeps_real_ny_tick_and_dst(self):
        payload = chart(timestamps=[stamp("2026-01-05"), stamp("2026-01-06")],
                        asof=stamp("2026-01-06", "16:00"))
        now = datetime(2026, 1, 6, 17, tzinfo=NY)
        value = parse_quote(payload, "AAPL", "苹果", now=now)
        self.assertEqual(value.asof.hour, 16)
        self.assertEqual(value.asof.utcoffset().total_seconds(), -5 * 3600)

    def test_dst_repeated_hour_uses_instant_for_future_quote_guard(self):
        first_hour_now = datetime(2026, 11, 1, 1, 45, tzinfo=NY, fold=0)
        future_second_hour = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        payload = chart("ES=F", asof=int(future_second_hour.timestamp()))
        self.assertIsNone(parse_quote(payload, "ES=F", "标普期货", now=first_hour_now))

        second_hour_now = datetime(2026, 11, 1, 1, 15, tzinfo=NY, fold=1)
        past_first_hour = datetime(2026, 11, 1, 1, 45, tzinfo=NY, fold=0)
        payload = chart("ES=F", asof=int(past_first_hour.timestamp()))
        value = parse_quote(payload, "ES=F", "标普期货", now=second_hour_now)
        self.assertIsNotNone(value)
        self.assertEqual(value.asof.timestamp(), past_first_hour.timestamp())

    def test_future_price_change_uses_disclosed_vendor_basis(self):
        payload = chart("ES=F", last=110, closes=[80, 90], regularMarketChangePercent=10,
                        fulldayPrice=110, fulldayChange=10)
        value = parse_quote(payload, "ES=F", "标普期货", now=NOW)
        self.assertAlmostEqual(value.pct, 10)
        self.assertEqual(value.previous_close, 100)
        self.assertEqual(value.session, "futures")
        self.assertIn("供应商参考基准", value.source)
        self.assertIsNone(value.previous_date)
        self.assertIsNone(parse_completed(payload, "ES=F", "标普期货", now=NOW))

    def test_yield_value_is_percentage_not_price_or_divided_again(self):
        value = parse_quote(chart("^TNX", last=5.293, closes=[5.255, 5.293]), "^TNX", "10年美债收益率", now=NOW)
        self.assertEqual(value.unit, "%")
        self.assertEqual(value.last, 5.293)


class ExtendedParsingTests(unittest.TestCase):
    def setUp(self):
        self.daily = chart()
        self.extended = chart(
            timestamps=[stamp("2026-09-29", "15:55"), stamp("2026-09-29", "19:00"),
                        stamp("2026-09-30", "04:00"), stamp("2026-09-30", "09:25"),
                        stamp("2026-09-30", "09:30"), stamp("2026-09-30", "15:55"),
                        stamp("2026-09-30", "16:00"), stamp("2026-09-30", "19:55"),
                        stamp("2026-09-30", "20:00")],
            closes=[100, 105, 109, 110, 115, 120, 123, 126, 300],
        )

    def test_pre_and_post_are_separate_real_sessions_with_correct_close_bases(self):
        pre = parse_extended(self.extended, "AAPL", "苹果", "premarket", now=NOW, daily_payload=self.daily)
        post = parse_extended(self.extended, "AAPL", "苹果", "postmarket", now=NOW, daily_payload=self.daily)
        self.assertEqual((pre.last, post.last), (110, 126))
        self.assertAlmostEqual(pre.pct, 10)
        self.assertAlmostEqual(post.pct, 5)
        self.assertEqual(pre.previous_date, date(2026, 9, 29))
        self.assertEqual(post.previous_date, date(2026, 9, 30))
        self.assertEqual((pre.asof.hour, pre.asof.minute), (9, 25))
        self.assertEqual((post.asof.hour, post.asof.minute), (19, 55))
        self.assertEqual((pre.volume, post.volume), (2000, 2000))

    def test_regular_only_payload_never_invents_extended_values(self):
        payload = chart(timestamps=[stamp("2026-09-30", "09:30"), stamp("2026-09-30", "15:55")], closes=[115, 120])
        for session in ("premarket", "postmarket"):
            self.assertIsNone(parse_extended(payload, "AAPL", "苹果", session, now=NOW, daily_payload=self.daily))

    def test_missing_session_cannot_borrow_other_session_price(self):
        payload = chart(timestamps=[stamp("2026-09-30", "19:00")], closes=[126])
        self.assertIsNone(parse_extended(payload, "AAPL", "苹果", "premarket", now=NOW, daily_payload=self.daily))

    def test_future_extended_bars_do_not_enter_capture(self):
        now = datetime(2026, 9, 30, 17, tzinfo=NY)
        value = parse_extended(self.extended, "AAPL", "苹果", "postmarket", now=now, daily_payload=self.daily)
        self.assertEqual(value.last, 123)
        self.assertEqual(value.asof.hour, 16)

    def test_premarket_during_day_uses_previous_day_not_future_same_day_close(self):
        now = datetime(2026, 9, 30, 8, tzinfo=NY)
        daily = chart(asof=stamp("2026-09-29", "16:00"), last=100)
        value = parse_extended(self.extended, "AAPL", "苹果", "premarket", now=now, daily_payload=daily)
        self.assertEqual(value.last, 109)
        self.assertEqual(value.previous_close, 100)

    def test_missing_baseline_preserves_price_without_fabricated_percent(self):
        missing = chart(timestamps=[stamp("2026-09-30")], closes=[120])
        value = parse_extended(self.extended, "AAPL", "苹果", "premarket", now=NOW, daily_payload=missing)
        self.assertEqual(value.last, 110)
        self.assertIsNone(value.pct)
        self.assertIsNone(value.previous_close)

    def test_stale_same_day_daily_bar_cannot_be_postmarket_close_basis(self):
        stale = chart(asof=stamp("2026-09-30", "15:55"))
        value = parse_extended(self.extended, "AAPL", "苹果", "postmarket", now=NOW, daily_payload=stale)
        self.assertEqual(value.last, 126)
        self.assertIsNone(value.pct)
        self.assertIsNone(value.previous_close)

    def test_missing_or_nonfinite_extended_bar_skips_only_that_bar(self):
        payload = chart(timestamps=[stamp("2026-09-30", "16:05"), stamp("2026-09-30", "19:00")], closes=[123, float("nan")])
        value = parse_extended(payload, "AAPL", "苹果", "postmarket", now=NOW, daily_payload=self.daily)
        self.assertEqual(value.last, 123)

    def test_weekend_extended_bar_is_not_a_us_session(self):
        payload = chart(timestamps=[stamp("2026-10-03", "18:00")], closes=[123])
        now = datetime(2026, 10, 3, 21, tzinfo=NY)
        self.assertIsNone(parse_extended(payload, "AAPL", "苹果", "postmarket", now=now))

    def test_half_day_extended_trading_starts_after_one_pm(self):
        payload = chart(timestamps=[stamp("2026-11-27", "12:55"), stamp("2026-11-27", "13:05")], closes=[100, 101])
        daily = chart(timestamps=[stamp("2026-11-25"), stamp("2026-11-27")], closes=[90, 100],
                      asof=stamp("2026-11-27", "13:00"), last=100)
        now = datetime(2026, 11, 27, 14, tzinfo=NY)
        value = parse_extended(payload, "AAPL", "苹果", "postmarket", now=now, daily_payload=daily)
        self.assertEqual(value.last, 101)
        self.assertAlmostEqual(value.pct, 1)

    def test_half_day_extended_trading_ends_at_five_pm(self):
        payload = chart(timestamps=[stamp("2026-11-27", "16:55"), stamp("2026-11-27", "17:00"),
                                    stamp("2026-11-27", "18:00")], closes=[101, 102, 103])
        daily = chart(timestamps=[stamp("2026-11-25"), stamp("2026-11-27")], closes=[90, 100],
                      asof=stamp("2026-11-27", "13:00"), last=100)
        now = datetime(2026, 11, 27, 19, tzinfo=NY)
        value = parse_extended(payload, "AAPL", "苹果", "postmarket", now=now, daily_payload=daily)
        self.assertEqual(value.last, 101)
        self.assertEqual(value.asof.hour, 16)
        only_after_end = chart(timestamps=[stamp("2026-11-27", "18:00")], closes=[103])
        self.assertIsNone(parse_extended(only_after_end, "AAPL", "苹果", "postmarket", now=now, daily_payload=daily))


class OvernightParsingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 30, 23, 30, tzinfo=NY)

    def parse(self, price, now=None):
        return parse_overnight(night_html(price), "AAPL", "苹果", now=now or self.now)

    def test_real_night_quote_uses_source_time_and_own_regular_close(self):
        value = self.parse(night_price())
        self.assertEqual(value.last, 110)
        self.assertAlmostEqual(value.pct, 10)
        self.assertEqual(value.session, "overnight")
        self.assertEqual(value.source, "BOATS Real Time Price")
        self.assertEqual(value.asof, datetime(2026, 9, 30, 23, tzinfo=NY))
        self.assertEqual(value.previous_date, date(2026, 9, 30))

    def test_quote_summary_change_fraction_is_not_mistaken_for_percentage_points(self):
        value = self.parse(night_price(overnightMarketPrice={"raw": 333.9}, regularMarketPrice={"raw": 333.02},
                                      overnightMarketChangePercent={"raw": 0.0026424986}))
        self.assertAlmostEqual(value.pct, 0.264248393489873, places=8)

    def test_same_edition_night_survives_midnight_and_four_am_transition(self):
        price = night_price()
        for now in (datetime(2026, 10, 1, 2, tzinfo=NY), datetime(2026, 10, 1, 8, tzinfo=NY)):
            with self.subTest(now=now):
                value = self.parse(price, now=now)
                self.assertEqual(value.asof.date(), date(2026, 9, 30))
                self.assertEqual(value.last, 110)
        self.assertIsNone(self.parse(price, now=datetime(2026, 10, 1, 10, tzinfo=NY)))

    def test_sunday_night_is_valid_for_monday_and_uses_friday_regular_close(self):
        price = night_price(overnightMarketTime=stamp("2026-10-04", "22:00"),
                            regularMarketTime=stamp("2026-10-02", "16:00"))
        value = self.parse(price, now=datetime(2026, 10, 4, 23, tzinfo=NY))
        self.assertEqual(value.asof.weekday(), 6)
        self.assertEqual(value.previous_date, date(2026, 10, 2))
        self.assertAlmostEqual(value.pct, 10)

    def test_twenty_to_four_window_boundaries_are_strict(self):
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        for day, clock, accepted in (("2026-09-30", "19:59", False), ("2026-09-30", "20:00", True),
                                     ("2026-10-01", "03:59", True), ("2026-10-01", "04:00", False)):
            with self.subTest(clock=clock):
                value = self.parse(night_price(overnightMarketTime=stamp(day, clock)), now=now)
                self.assertEqual(value is not None, accepted)

    def test_future_and_previous_night_quotes_are_rejected(self):
        for timestamp in (stamp("2026-10-01", "01:00"), stamp("2026-09-29", "23:00")):
            self.assertIsNone(self.parse(night_price(overnightMarketTime=timestamp)))

    def test_market_state_cannot_create_or_remove_real_quote(self):
        self.assertIsNotNone(self.parse(night_price(marketState="PRE"), now=datetime(2026, 10, 1, 8, tzinfo=NY)))
        for field in ("overnightMarketPrice", "overnightMarketTime", "overnightMarketSource"):
            price = night_price()
            del price[field]
            with self.subTest(field=field):
                self.assertIsNone(self.parse(price))

    def test_invalid_night_prices_times_and_empty_sources_are_rejected(self):
        for raw in (0, -1, float("nan"), float("inf"), True):
            self.assertIsNone(self.parse(night_price(overnightMarketPrice={"raw": raw})))
        for raw in (None, float("nan"), float("inf"), True, {"raw": stamp("2026-09-30", "23:00")}):
            self.assertIsNone(self.parse(night_price(overnightMarketTime=raw)))
        self.assertIsNone(self.parse(night_price(overnightMarketSource=" ")))

    def test_missing_stale_intraday_and_future_regular_bases_preserve_only_night_price(self):
        for overrides in (
            {"regularMarketPrice": {}}, {"regularMarketPrice": {"raw": float("inf")}},
            {"regularMarketTime": stamp("2026-09-30", "15:55")},
            {"regularMarketTime": stamp("2026-09-29", "16:00")},
            {"regularMarketTime": stamp("2026-10-01", "16:00")},
        ):
            with self.subTest(overrides=overrides):
                value = self.parse(night_price(**overrides))
                self.assertEqual(value.last, 110)
                self.assertIsNone(value.pct)
                self.assertIsNone(value.previous_close)
                self.assertIsNone(value.previous_date)

    def test_symbol_and_currency_must_match_expected_quote(self):
        self.assertIsNone(self.parse(night_price(symbol="MSFT")))
        self.assertIsNone(self.parse(night_price(currency="EUR")))
        html = night_html(night_price(symbol="MSFT", regularMarketPrice={"raw": 500}), night_price())
        value = parse_overnight(html, "AAPL", "苹果", now=self.now)
        self.assertEqual(value.previous_close, 100)

    def test_malformed_siblings_do_not_hide_valid_structured_quote(self):
        broken = '<script type="application/json" data-sveltekit-fetched>{invalid}</script>'
        body_invalid = '<script type="application/json" data-sveltekit-fetched>{"status":200,"body":"bad"}</script>'
        html = broken + body_invalid + night_html(night_price(symbol="MSFT")) + night_html(night_price())
        self.assertEqual(parse_overnight(html, "AAPL", "苹果", now=self.now).last, 110)
        self.assertIsNone(parse_overnight(broken + body_invalid, "AAPL", "苹果", now=self.now))

    def test_nonfetched_script_and_failed_http_payload_are_ignored(self):
        valid = night_html(night_price())
        self.assertIsNone(parse_overnight(valid.replace("data-sveltekit-fetched", "data-unrelated"), "AAPL", "苹果", now=self.now))
        self.assertIsNone(parse_overnight(night_html(night_price(), status=500), "AAPL", "苹果", now=self.now))

    def test_multiple_valid_snapshots_choose_latest_real_trade_time(self):
        old = night_price(overnightMarketPrice={"raw": 105}, overnightMarketTime=stamp("2026-09-30", "21:00"))
        value = parse_overnight(night_html(night_price(), old), "AAPL", "苹果", now=self.now)
        self.assertEqual(value.last, 110)
        self.assertEqual(value.asof.hour, 23)

    def test_nonfinite_computed_change_is_not_published(self):
        value = self.parse(night_price(overnightMarketPrice={"raw": 1e300}, regularMarketPrice={"raw": 1e-300}))
        self.assertEqual(value.last, 1e300)
        self.assertIsNone(value.pct)


class DataLoadingTests(unittest.TestCase):
    def test_chart_query_falls_back_to_second_yahoo_host(self):
        payload = chart("^GSPC")
        with patch("usstock.sources.yahoo.fetch_text", side_effect=[OSError("timeout"), json.dumps(payload)]) as fetch:
            self.assertEqual(_fetch_chart("^GSPC"), payload)
        self.assertIn("query1.finance.yahoo.com", fetch.call_args_list[0].args[0])
        self.assertIn("query2.finance.yahoo.com", fetch.call_args_list[1].args[0])
        self.assertIn("%5EGSPC", fetch.call_args_list[0].args[0])

    def test_chart_error_or_wrong_symbol_falls_back(self):
        payload = chart()
        for invalid in ({"chart": {"result": None, "error": {"code": "Not Found"}}}, chart("MSFT")):
            with patch("usstock.sources.yahoo.fetch_text", side_effect=[json.dumps(invalid), json.dumps(payload)]):
                self.assertEqual(_fetch_chart("AAPL", extended=True), payload)

    def test_night_quote_survives_same_symbol_daily_and_extended_failures(self):
        now = datetime(2026, 9, 30, 23, 30, tzinfo=NY)
        night = Quote("AAPL", "苹果", 110, asof=datetime(2026, 9, 30, 23, tzinfo=NY), session="overnight")
        with patch("usstock.sources.yahoo._daily", side_effect=OSError("daily down")), \
             patch("usstock.sources.yahoo._extended", side_effect=OSError("extended down")), \
             patch("usstock.sources.yahoo._overnight", return_value=night):
            result = load("AAPL", "苹果", now)
        self.assertEqual(result.overnight, {"AAPL": night})
        self.assertEqual(result.completed, {})
        self.assertTrue(any("常规行情暂缺" in note for note in result.notes))
        self.assertTrue(any("延长时段行情暂缺" in note for note in result.notes))

    def test_provider_live_clock_is_taken_after_response(self):
        started = datetime(2026, 9, 30, 15, 59, 59, tzinfo=NY)
        finished = datetime(2026, 9, 30, 16, 0, 2, tzinfo=NY)
        with patch("usstock.sources.yahoo._fetch_chart", return_value=chart("^GSPC")), \
             patch("usstock.sources.yahoo.parse_quote", return_value=None) as parse:
            load("^GSPC", "标普500", started, clock=lambda: finished)
        self.assertEqual(parse.call_args.kwargs["now"], finished)


if __name__ == "__main__":
    unittest.main()
