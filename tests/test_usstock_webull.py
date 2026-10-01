from __future__ import annotations

import copy
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from usstock.models import NY
from usstock.sources.webull import night_snapshots, parse_snapshots


NOW = datetime(2026, 10, 1, 0, 15, tzinfo=NY)
OBSERVED = NOW.astimezone(timezone.utc) + timedelta(seconds=1)


def payload():
    # Reduced public Webull overnight-ranking response, not a regular quote.
    return {
        "rankType": "overnight",
        "latestUpdateTime": 1790815045421,
        "data": [{
            "ticker": {
                "tickerId": 913256135, "symbol": "AAPL", "type": 2,
                "regionId": 6, "currencyCode": "USD",
            },
            "values": {
                "dt": "overnight", "tickerId": 913256135,
                "close": "333.02", "overnightPrice": "334.25",
                "overnightChangeRatio": "0.0037", "overnightVolume": "16284",
            },
        }],
    }


class WebullSnapshotTests(unittest.TestCase):
    def test_night_price_keeps_observation_separate_from_unknown_trade_time(self):
        quote = parse_snapshots(payload(), NOW, OBSERVED)["AAPL"]
        self.assertEqual((quote.symbol, quote.name, quote.last), ("AAPL", "苹果", 334.25))
        self.assertEqual((quote.session, quote.source), ("overnight", "Webull"))
        self.assertIsNone(quote.asof)
        self.assertIsNone(quote.trade_date)
        self.assertEqual(quote.previous_date, date(2026, 9, 30))
        self.assertEqual(quote.observed_at, OBSERVED)
        self.assertEqual(quote.previous_close, 333.02)
        self.assertAlmostEqual(quote.pct, (334.25 / 333.02 - 1) * 100)

    def test_etf_and_spy_use_local_names(self):
        data = payload()
        original = data["data"][0]
        for symbol in ("XLK", "SPY"):
            row = copy.deepcopy(original)
            row["ticker"].update(symbol=symbol, type=3)
            data["data"].append(row)
        values = parse_snapshots(data, NOW, OBSERVED)
        self.assertEqual(values["XLK"].name, "科技")
        self.assertEqual(values["SPY"].name, "标普ETF")

    def test_only_matching_us_stock_and_etf_identities_are_accepted(self):
        for field, value in (
            ("symbol", "AAPB"), ("symbol", "aapl"), ("symbol", "^GSPC"),
            ("regionId", 1), ("currencyCode", "HKD"), ("type", 1),
            ("tickerId", True), ("tickerId", 0),
        ):
            with self.subTest(field=field, value=value):
                data = payload()
                data["data"][0]["ticker"][field] = value
                self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})
        data = payload()
        data["data"][0]["values"]["tickerId"] = 123
        self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})

    def test_regular_or_postmarket_values_do_not_become_night_quotes(self):
        for field, value in (("rankType", "postmarket"), ("dt", "regular")):
            with self.subTest(field=field):
                data = payload()
                target = data if field == "rankType" else data["data"][0]["values"]
                target[field] = value
                self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})

    def test_malformed_rows_and_nonfinite_prices_do_not_discard_other_rows(self):
        data = payload()
        data["data"].extend([None, 7, {}, {"ticker": [], "values": {}}, {"ticker": {}, "values": []}])
        self.assertEqual(list(parse_snapshots(data, NOW, OBSERVED)), ["AAPL"])
        for price in (None, True, "nan", "inf", "-inf", "0", "-1", "bad"):
            with self.subTest(price=price):
                data = payload()
                data["data"][0]["values"]["overnightPrice"] = price
                self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})

    def test_disagreeing_or_missing_basis_keeps_price_without_fabricated_change(self):
        for overrides in (
            {"close": "0"}, {"close": "nan"}, {"close": None},
            {"overnightChangeRatio": "nan"}, {"overnightChangeRatio": None},
            {"overnightChangeRatio": "0.37"},
        ):
            with self.subTest(overrides=overrides):
                data = payload()
                data["data"][0]["values"].update(overrides)
                quote = parse_snapshots(data, NOW, OBSERVED)["AAPL"]
                self.assertEqual(quote.last, 334.25)
                self.assertIsNone(quote.pct)
                self.assertIsNone(quote.previous_close)
                self.assertIsNone(quote.previous_date)

    def test_old_or_future_ranking_time_is_not_a_current_night_snapshot(self):
        for stamp in (
            None, True, "nan", "bad", 0,
            int((NOW - timedelta(days=1)).timestamp() * 1000),
            int((OBSERVED + timedelta(minutes=1)).timestamp() * 1000),
        ):
            with self.subTest(stamp=stamp):
                data = payload()
                data["latestUpdateTime"] = stamp
                self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})

    def test_bad_payload_shape_is_empty(self):
        for data in (None, [], 1, {}, {**payload(), "data": {}}, {**payload(), "data": None}):
            with self.subTest(data=data):
                self.assertEqual(parse_snapshots(data, NOW, OBSERVED), {})

    def test_snapshots_are_rejected_after_night_window_even_if_recently_observed(self):
        for now in (
            datetime(2026, 9, 30, 19, 59, tzinfo=NY),
            datetime(2026, 10, 1, 4, tzinfo=NY),
            datetime(2026, 10, 1, 9, 25, tzinfo=NY),
        ):
            with self.subTest(now=now):
                self.assertEqual(parse_snapshots(payload(), now, now.astimezone(timezone.utc)), {})

    @patch("usstock.sources.webull.fetch_text")
    def test_inactive_weekend_and_holiday_windows_never_request(self, fetch):
        for now in (
            datetime(2026, 10, 1, 4, tzinfo=NY),
            datetime(2026, 10, 1, 9, 25, tzinfo=NY),
            datetime(2026, 10, 2, 20, tzinfo=NY),
            datetime(2026, 10, 3, 0, 15, tzinfo=NY),
            datetime(2026, 9, 6, 20, tzinfo=NY),  # Monday is Labor Day.
            datetime(2030, 1, 1, 23, tzinfo=NY),
        ):
            with self.subTest(now=now):
                self.assertEqual(night_snapshots(now), {})
        fetch.assert_not_called()

    @patch("usstock.sources.webull.fetch_text")
    def test_single_public_request_has_bounded_timeout_and_no_cache(self, fetch):
        fetch.return_value = json.dumps(payload())
        with patch("usstock.sources.webull.datetime", wraps=datetime) as clock:
            clock.now.return_value = OBSERVED
            values = night_snapshots(NOW)
        self.assertEqual(values["AAPL"].observed_at, OBSERVED)
        fetch.assert_called_once()
        args, kwargs = fetch.call_args
        self.assertIn("/api/wlas/ranking/overnight?", args[0])
        self.assertIn("pageSize=1000", args[0])
        self.assertEqual(kwargs, {"referer": "https://app.webull.com/", "timeout": 10, "retries": 0})

    @patch("usstock.sources.webull.fetch_text")
    def test_capture_finishing_after_four_am_discards_snapshot(self, fetch):
        fetch.return_value = json.dumps(payload())
        before = datetime(2026, 10, 1, 3, 59, 59, tzinfo=NY)
        after = datetime(2026, 10, 1, 4, 0, 1, tzinfo=NY).astimezone(timezone.utc)
        with patch("usstock.sources.webull.datetime", wraps=datetime) as clock:
            clock.now.return_value = after
            self.assertEqual(night_snapshots(before), {})

    def test_sunday_start_belongs_to_monday_session(self):
        now = datetime(2026, 10, 4, 20, tzinfo=NY)
        data = payload()
        data["latestUpdateTime"] = int(now.timestamp() * 1000)
        self.assertIn("AAPL", parse_snapshots(data, now, now.astimezone(timezone.utc)))

    def test_sunday_basis_is_friday_close_and_does_not_fabricate_trade_date(self):
        now = datetime(2026, 10, 4, 20, 30, tzinfo=NY)
        data = payload()
        data["latestUpdateTime"] = int(now.timestamp() * 1000)
        quote = parse_snapshots(data, now, now.astimezone(timezone.utc))["AAPL"]
        self.assertEqual(quote.previous_date, date(2026, 10, 2))
        self.assertIsNone(quote.asof)
        self.assertIsNone(quote.trade_date)

    @patch("usstock.sources.webull.fetch_text", side_effect=TimeoutError("source unavailable"))
    def test_transport_errors_remain_visible_to_provider_diagnostics(self, fetch):
        with self.assertRaises(TimeoutError):
            night_snapshots(NOW)


if __name__ == "__main__":
    unittest.main()
