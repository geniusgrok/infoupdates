import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from usstock.models import NY
from usstock.sources import cboe


NOW = datetime(2026, 10, 1, 0, 10, tzinfo=NY)


def history(code="RUT"):
    return {"symbol": f"_{code}", "data": [
        {"date": "2026-09-28", "close": "2817.9122", "volume": "0.0"},
        {"date": "2026-09-29", "close": "2807.9220", "volume": "0.0"},
        {"date": "2026-09-30", "close": "2796.8641", "volume": "0.0"},
    ]}


def quote(code="RUT", price=2796.864, stamp="2026-09-30T16:09:55"):
    return {
        "symbol": f"_{code}", "timestamp": "2026-10-01 03:44:02",
        "data": {"symbol": f"^{code}", "current_price": price, "last_trade_time": stamp,
                 "prev_day_close": price, "price_change_percent": -0.3953},
    }


class CboeTests(unittest.TestCase):
    def test_russell_history_retains_real_close_and_previous_day(self):
        record = cboe.parse_history(history(), "^RUT", "罗素2000", NOW)[date(2026, 9, 30)]
        self.assertEqual(record.last, 2796.8641)
        self.assertEqual(record.previous_close, 2807.922)
        self.assertAlmostEqual(record.pct, (2796.8641 / 2807.922 - 1) * 100)
        self.assertEqual(record.previous_date, date(2026, 9, 29))
        self.assertEqual(record.source, "Cboe日线")
        self.assertIsNone(record.volume)

    def test_uncompleted_history_and_future_rows_are_rejected(self):
        now = datetime(2026, 9, 30, 15, 55, tzinfo=NY)
        records = cboe.parse_history(history(), "^RUT", "罗素2000", now)
        self.assertEqual(max(records), date(2026, 9, 29))
        payload = history()
        payload["data"].append({"date": "2026-10-01", "close": "3000"})
        self.assertNotIn(date(2026, 10, 1), cboe.parse_history(payload, "^RUT", "罗素2000", NOW))

    def test_malformed_and_nonfinite_history_rows_preserve_good_data(self):
        payload = history()
        payload["data"] += [
            {"date": "2026-09-25", "close": "NaN"},
            {"date": "2026-09-24", "close": True},
            {"date": "2026-09-23", "close": "Infinity"},
            {"date": "2026-09-27", "close": "2800"},
            {"date": "bad", "close": "2800"}, None,
        ]
        self.assertEqual(len(cboe.parse_history(payload, "^RUT", "罗素2000", NOW)), 3)

    def test_no_cross_gap_previous_close(self):
        payload = history()
        del payload["data"][1]
        record = cboe.parse_history(payload, "^RUT", "罗素2000", NOW)[date(2026, 9, 30)]
        self.assertIsNone(record.previous_close)
        self.assertIsNone(record.pct)

    def test_history_identity_is_required(self):
        with self.assertRaises(ValueError):
            cboe.parse_history(history("SPX"), "^RUT", "罗素2000", NOW)

    def test_quote_uses_actual_et_time_not_document_timestamp(self):
        records = cboe.parse_history(history(), "^RUT", "罗素2000", NOW)
        record = cboe.parse_quote(quote(), "^RUT", "罗素2000", records, NOW)
        self.assertEqual(record.asof, datetime(2026, 9, 30, 16, 9, 55, tzinfo=NY))
        self.assertEqual(record.trade_date, date(2026, 9, 30))
        self.assertEqual(record.last, 2796.864)
        self.assertEqual(record.session, "regular")

    def test_rolled_previous_close_and_vendor_pct_are_not_used(self):
        records = cboe.parse_history(history(), "^RUT", "罗素2000", NOW)
        record = cboe.parse_quote(quote(), "^RUT", "罗素2000", records, NOW)
        self.assertEqual(record.previous_close, 2807.922)
        self.assertAlmostEqual(record.pct, (2796.864 / 2807.922 - 1) * 100)

    def test_missing_history_keeps_actual_price_without_guessed_pct(self):
        record = cboe.parse_quote(quote(), "^RUT", "罗素2000", {}, NOW)
        self.assertEqual(record.last, 2796.864)
        self.assertIsNone(record.pct)
        self.assertIsNone(record.previous_close)

    def test_quote_identity_is_required_in_outer_and_inner_objects(self):
        for payload in (quote("SPX"), {"symbol": "_RUT", "data": quote("SPX")["data"]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                cboe.parse_quote(payload, "^RUT", "罗素2000", {}, NOW)

    def test_future_time_weekend_and_bad_clock_are_rejected(self):
        for stamp in ("2026-10-01T16:09:55", "2026-09-27T16:09:55", "bad", None):
            with self.subTest(stamp=stamp):
                self.assertIsNone(cboe.parse_quote(quote(stamp=stamp), "^RUT", "罗素2000", {}, NOW))

    def test_nonfinite_or_nonpositive_quote_prices_are_rejected(self):
        for price in (float("nan"), float("inf"), True, 0, -1):
            with self.subTest(price=price):
                self.assertIsNone(cboe.parse_quote(quote(price=price), "^RUT", "罗素2000", {}, NOW))

    def test_ten_year_yield_divides_by_ten_and_respects_offset(self):
        payload = history("TNX")
        payload["data"][1]["close"] = "52.55"
        payload["data"][2]["close"] = "52.93"
        records = cboe.parse_history(payload, "^TNX", "10年美债收益率", NOW)
        self.assertAlmostEqual(records[date(2026, 9, 30)].last, 5.293)
        record = cboe.parse_quote(
            quote("TNX", 52.93, "2026-09-30T13:59:55.044000-05:00"),
            "^TNX", "10年美债收益率", records, NOW,
        )
        self.assertAlmostEqual(record.last, 5.293)
        self.assertAlmostEqual(record.previous_close, 5.255)
        self.assertEqual((record.asof.hour, record.asof.minute), (14, 59))
        self.assertEqual(record.session, "reference")
        self.assertEqual(record.unit, "%")

    def test_spx_and_vix_map_to_actual_cboe_symbols(self):
        for symbol, code in (("^GSPC", "SPX"), ("^VIX", "VIX")):
            with self.subTest(symbol=symbol):
                record = cboe.parse_quote(quote(code), symbol, symbol, {}, NOW)
                self.assertEqual(record.symbol, symbol)
                self.assertEqual(record.source, "Cboe")

    @patch.object(cboe, "fetch_text")
    def test_dji_ixic_and_night_are_not_misrepresented(self, fetch):
        for symbol in ("^DJI", "^IXIC", "AAPL"):
            result = cboe.load(symbol, symbol, NOW)
            self.assertFalse(result.quotes)
            self.assertFalse(result.overnight)
        fetch.assert_not_called()

    @patch.object(cboe, "fetch_text")
    def test_history_failure_retains_valid_quote(self, fetch):
        fetch.side_effect = [OSError("offline"), json.dumps(quote())]
        result = cboe.load("^RUT", "罗素2000", NOW)
        self.assertEqual(result.quotes["^RUT"].last, 2796.864)
        self.assertEqual(len(result.notes), 1)
        self.assertIsNone(result.quotes["^RUT"].pct)

    @patch.object(cboe, "fetch_text")
    def test_quote_failure_retains_daily_history(self, fetch):
        fetch.side_effect = [json.dumps(history()), OSError("offline")]
        result = cboe.load("^RUT", "罗素2000", NOW)
        self.assertEqual(result.completed["^RUT"].last, 2796.8641)
        self.assertEqual(result.quotes["^RUT"].last, 2796.8641)
        self.assertEqual(len(result.notes), 1)

    @patch.object(cboe, "fetch_text")
    def test_actual_response_clock_allows_new_quote_without_changing_asof(self, fetch):
        frozen = datetime(2026, 9, 30, 16, 9, 50, tzinfo=NY)
        finished = datetime(2026, 9, 30, 16, 9, 57, tzinfo=NY)
        fetch.side_effect = [json.dumps(history()), json.dumps(quote())]
        result = cboe.load("^RUT", "罗素2000", frozen, clock=lambda: finished)
        self.assertEqual(result.quotes["^RUT"].asof.second, 55)
        self.assertEqual(result.quotes["^RUT"].observed_at, finished)


if __name__ == "__main__":
    unittest.main()
