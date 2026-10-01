import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from usstock.models import NY
from usstock.sources import nasdaq


NOW = datetime(2026, 10, 1, 0, 10, tzinfo=NY)


def history(symbol="AAPL"):
    return {
        "data": {"symbol": symbol, "tradesTable": {"rows": [
            {"date": "09/30/2026", "close": "$333.02", "volume": "49,988,560"},
            {"date": "09/29/2026", "close": "$329.40", "volume": "38,478,040"},
            {"date": "09/28/2026", "close": "$338.40", "volume": "32,820,850"},
        ]}},
        "status": {"rCode": 200},
    }


def extended(day="Sep 30, 2026", clock="19:59:59", price="$334.25"):
    return {
        "data": {
            "previousInfo": " Market Close: $329.4",
            "lastUpdateInfo": [
                f"Data last updated {day} 08:00 PM ET.",
                "This page will resume updating on Oct 1, 2026 04:00 PM ET.",
            ],
            "infoTable": {"rows": [{"consolidated": "$334.25 +4.85 (+1.47%)"}]},
            "tradeDetailTable": {"rows": [{"time": clock, "price": price, "shareVolume": "40"}]},
        },
        "status": {"rCode": 200},
    }


class NasdaqTests(unittest.TestCase):
    def test_daily_close_previous_price_and_volume_stay_together(self):
        quotes = nasdaq.parse_history(history(), "AAPL", "苹果", NOW)
        quote = quotes[date(2026, 9, 30)]
        self.assertEqual((quote.last, quote.previous_close), (333.02, 329.4))
        self.assertAlmostEqual(quote.pct, (333.02 / 329.4 - 1) * 100)
        self.assertEqual((quote.volume, quote.previous_volume), (49988560, 38478040))
        self.assertEqual(quote.previous_date, date(2026, 9, 29))
        self.assertEqual(quote.asof, datetime(2026, 9, 30, 16, tzinfo=NY))
        self.assertEqual(quote.source, "Nasdaq日线")

    def test_spy_etf_daily_numbers_have_no_dollar_prefix(self):
        payload = history("SPY")
        rows = payload["data"]["tradesTable"]["rows"]
        rows[0].update(close="762.63", volume="62,110,040")
        rows[1].update(close="764.20", volume="36,910,550")
        quote = nasdaq.parse_history(payload, "SPY", "SPY", NOW)[date(2026, 9, 30)]
        self.assertEqual((quote.last, quote.previous_close), (762.63, 764.2))
        self.assertEqual((quote.volume, quote.previous_volume), (62110040, 36910550))

    def test_intraday_history_is_not_a_completed_close(self):
        now = datetime(2026, 9, 30, 15, 55, tzinfo=NY)
        quotes = nasdaq.parse_history(history(), "AAPL", "苹果", now)
        self.assertNotIn(date(2026, 9, 30), quotes)
        self.assertEqual(max(quotes), date(2026, 9, 29))

    def test_future_weekend_malformed_and_nonfinite_rows_are_skipped(self):
        payload = history()
        payload["data"]["tradesTable"]["rows"] += [
            {"date": "10/01/2026", "close": "$345"},
            {"date": "09/27/2026", "close": "$345"},
            {"date": "bad", "close": "$345"},
            {"date": "09/25/2026", "close": "NaN"},
            {"date": "09/24/2026", "close": "Infinity"},
            None,
        ]
        quotes = nasdaq.parse_history(payload, "AAPL", "苹果", NOW)
        self.assertEqual(set(quotes), {date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)})

    def test_missing_previous_session_does_not_compare_to_two_days_ago(self):
        payload = history()
        del payload["data"]["tradesTable"]["rows"][1]
        quote = nasdaq.parse_history(payload, "AAPL", "苹果", NOW)[date(2026, 9, 30)]
        self.assertIsNone(quote.previous_close)
        self.assertIsNone(quote.previous_volume)
        self.assertIsNone(quote.pct)

    def test_identity_and_business_error_are_rejected(self):
        for payload in (history("MSFT"), {"data": None, "status": {"rCode": 400}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                nasdaq.parse_history(payload, "AAPL", "苹果", NOW)

    def test_invalid_volume_does_not_drop_valid_price(self):
        payload = history()
        payload["data"]["tradesTable"]["rows"][0]["volume"] = "-1"
        quote = nasdaq.parse_history(payload, "AAPL", "苹果", NOW)[date(2026, 9, 30)]
        self.assertEqual(quote.last, 333.02)
        self.assertIsNone(quote.volume)

    def test_postmarket_uses_today_close_not_nasdaq_previous_info(self):
        records = nasdaq.parse_history(history(), "AAPL", "苹果", NOW)
        quote = nasdaq.parse_extended(extended(), "AAPL", "苹果", "postmarket", records, NOW)
        self.assertEqual(quote.last, 334.25)
        self.assertEqual(quote.previous_close, 333.02)
        self.assertAlmostEqual(quote.pct, (334.25 / 333.02 - 1) * 100)
        self.assertEqual(quote.asof, datetime(2026, 9, 30, 19, 59, 59, tzinfo=NY))
        self.assertEqual(quote.session, "postmarket")

    def test_premarket_uses_previous_completed_session(self):
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        records = nasdaq.parse_history(history(), "AAPL", "苹果", now)
        quote = nasdaq.parse_extended(
            extended("Oct 1, 2026", "07:59:55", "$334.50"), "AAPL", "苹果", "premarket", records, now,
        )
        self.assertEqual(quote.previous_close, 333.02)
        self.assertEqual(quote.previous_date, date(2026, 9, 30))
        self.assertEqual(quote.asof, datetime(2026, 10, 1, 7, 59, 55, tzinfo=NY))

    def test_resume_date_is_not_an_actual_trade_date(self):
        quote = nasdaq.parse_extended(extended(), "AAPL", "苹果", "postmarket", {}, NOW)
        self.assertEqual(quote.trade_date, date(2026, 9, 30))
        payload = extended()
        payload["data"]["lastUpdateInfo"].pop(0)
        self.assertIsNone(nasdaq.parse_extended(payload, "AAPL", "苹果", "postmarket", {}, NOW))

    def test_missing_baseline_keeps_actual_price_without_fabricated_pct(self):
        quote = nasdaq.parse_extended(extended(), "AAPL", "苹果", "postmarket", {}, NOW)
        self.assertEqual(quote.last, 334.25)
        self.assertIsNone(quote.previous_close)
        self.assertIsNone(quote.pct)

    def test_no_trades_cannot_be_filled_from_consolidated_summary(self):
        payload = extended()
        payload["data"]["tradeDetailTable"]["rows"] = None
        self.assertIsNone(nasdaq.parse_extended(payload, "AAPL", "苹果", "postmarket", {}, NOW))

    def test_future_trade_and_wrong_session_window_are_rejected(self):
        cases = [
            (extended("Oct 1, 2026", "19:59:59"), "postmarket"),
            (extended(clock="15:59:59"), "postmarket"),
            (extended(clock="20:00:00"), "postmarket"),
            (extended(clock="09:30:00"), "premarket"),
            (extended(clock="03:59:59"), "premarket"),
            (extended(clock="19:59:59+02:00"), "postmarket"),
        ]
        for payload, session in cases:
            with self.subTest(payload=payload, session=session):
                self.assertIsNone(nasdaq.parse_extended(payload, "AAPL", "苹果", session, {}, NOW))

    def test_nonfinite_or_boolean_extended_prices_are_rejected(self):
        for price in ("NaN", "Infinity", True, "$0", "$-5"):
            with self.subTest(price=price):
                self.assertIsNone(nasdaq.parse_extended(
                    extended(price=price), "AAPL", "苹果", "postmarket", {}, NOW,
                ))

    def test_halfday_postmarket_stops_at_seventeen(self):
        now = datetime(2026, 11, 27, 19, tzinfo=NY)
        good = nasdaq.parse_extended(
            extended("Nov 27, 2026", "16:59:59"), "AAPL", "苹果", "postmarket", {}, now,
        )
        self.assertEqual(good.asof.hour, 16)
        self.assertIsNone(nasdaq.parse_extended(
            extended("Nov 27, 2026", "17:00:00"), "AAPL", "苹果", "postmarket", {}, now,
        ))

    @patch.object(nasdaq, "fetch_text")
    def test_unsupported_symbols_make_no_network_requests(self, fetch):
        for symbol in ("^GSPC", "RTY=F", "not-a-stock"):
            self.assertFalse(nasdaq.load(symbol, symbol, NOW).quotes)
        fetch.assert_not_called()

    @patch.object(nasdaq, "fetch_text")
    def test_load_uses_etf_assetclass_and_never_adds_night(self, fetch):
        fetch.side_effect = [json.dumps(history("SPY")), '{"data":null}', '{"data":null}']
        result = nasdaq.load("SPY", "SPY", NOW)
        self.assertEqual(result.completed["SPY"].last, 333.02)
        self.assertTrue(all("assetclass=etf" in call.args[0] for call in fetch.call_args_list))
        self.assertIn("fromdate=2026-09-17", fetch.call_args_list[0].args[0])
        self.assertFalse(result.overnight)

    @patch.object(nasdaq, "fetch_text")
    def test_history_failure_keeps_actual_postmarket(self, fetch):
        fetch.side_effect = [OSError("unavailable"), '{"data":null}', json.dumps(extended())]
        result = nasdaq.load("AAPL", "苹果", NOW)
        self.assertEqual(result.postmarket["AAPL"].last, 334.25)
        self.assertIsNone(result.postmarket["AAPL"].pct)
        self.assertEqual(len(result.notes), 1)

    @patch.object(nasdaq, "fetch_text")
    def test_malformed_premarket_update_keeps_history_and_fetches_postmarket(self, fetch):
        malformed = extended()
        malformed["data"]["lastUpdateInfo"] = 5
        fetch.side_effect = [json.dumps(history()), json.dumps(malformed), json.dumps(extended())]
        result = nasdaq.load("AAPL", "苹果", NOW)
        self.assertEqual(result.completed["AAPL"].last, 333.02)
        self.assertEqual(result.postmarket["AAPL"].last, 334.25)
        self.assertFalse(result.premarket)
        self.assertEqual(len(result.notes), 1)
        self.assertIn("premarket", result.notes[0])
        self.assertEqual(fetch.call_count, 3)

    @patch.object(nasdaq, "fetch_text")
    def test_extended_failure_keeps_daily_history(self, fetch):
        fetch.side_effect = [json.dumps(history()), OSError("pre"), OSError("post")]
        result = nasdaq.load("AAPL", "苹果", NOW)
        self.assertEqual(result.completed["AAPL"].last, 333.02)
        self.assertEqual(len(result.notes), 2)

    @patch.object(nasdaq, "fetch_text")
    def test_response_clock_preserves_a_trade_arriving_during_download(self, fetch):
        frozen = datetime(2026, 9, 30, 19, 59, 50, tzinfo=NY)
        finished = datetime(2026, 9, 30, 20, 0, 1, tzinfo=NY)
        fetch.side_effect = [json.dumps(history()), '{"data":null}', json.dumps(extended())]
        result = nasdaq.load("AAPL", "苹果", frozen, clock=lambda: finished)
        self.assertEqual(result.postmarket["AAPL"].asof.second, 59)
        self.assertEqual(result.postmarket["AAPL"].observed_at, finished)


if __name__ == "__main__":
    unittest.main()
