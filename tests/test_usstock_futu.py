from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from urllib.error import URLError

from usstock.calendar import session_close, session_open
from usstock.models import NY
from usstock.sources.futu import SYMBOLS, load, parse

NOW = datetime(2026, 9, 30, 23, 15, tzinfo=NY)


def moment(clock: str, day: str = "2026-09-30") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=NY)


def bar(clock: str, price: float = 333.02, *, day: str = "2026-09-30", volume: int = 10) -> dict:
    return {"time": moment(clock, day).timestamp(), "cc_price": price, "volume": volume}


def fixture() -> dict:
    # 富途 AAPL 页面 2026-09-30 的最小真实字段；20:02 snapshot 仍声明盘后。
    return {
        "stock_info": {
            "stockCode": "AAPL", "marketLabel": "US", "stockId": "205189",
            "price": "333.020", "priceLastClose": "329.400",
            "exchangeDataTimeMs": "1790798400872", "delayTime": 0,
            "before_open_stock_info": {
                "status": 2, "exchange_time": 1790812969000, "price": "334.250",
            },
        },
        "stock_charts_data": {"minuteChartsData": {
            "stockId": "205189", "last_close_price": 329400,
            "list": [bar("09:30", 330.39), bar("15:59", 333.725), bar("16:00", 333.02),
                     bar("19:59", 334.35), bar("20:00", 334.25)],
        }},
    }


def html(state: dict) -> str:
    return f'<html><script>window.__INITIAL_STATE__={json.dumps(state)};</script></html>'


class FutuTests(unittest.TestCase):
    def test_real_page_keeps_regular_and_postmarket_separate_without_night(self):
        result = parse(html(fixture()), "AAPL", "苹果", NOW)
        self.assertAlmostEqual(result.completed["AAPL"].pct, (333.02 / 329.4 - 1) * 100)
        self.assertEqual(result.premarket["AAPL"].asof, moment("09:30"))
        after = result.postmarket["AAPL"]
        self.assertEqual((after.last, after.previous_close, after.asof), (334.25, 333.02, moment("20:00")))
        self.assertEqual(after.previous_date, date(2026, 9, 30))
        self.assertEqual((after.source, after.observed_at), ("Futu", NOW))
        self.assertEqual(result.overnight, {})

    def test_window_rejects_status_two_snapshot_after_twenty(self):
        state = fixture()
        state["stock_info"]["before_open_stock_info"]["price"] = "999"
        state["stock_charts_data"]["minuteChartsData"]["list"] = []
        result = parse(html(state), "AAPL", "苹果", NOW)
        self.assertFalse(result.postmarket)
        self.assertFalse(result.overnight)

    def test_last_trade_before_close_does_not_prove_completion(self):
        state = fixture()
        state["stock_info"]["exchangeDataTimeMs"] = moment("15:59:59.887").timestamp() * 1000
        state["stock_charts_data"]["minuteChartsData"]["list"] = []
        result = parse(html(state), "AAPL", "苹果", NOW)
        self.assertEqual(result.quotes["AAPL"].asof, moment("15:59:59.887"))
        self.assertFalse(result.completed)
        state["stock_charts_data"]["minuteChartsData"]["list"] = [bar("16:00")]
        result = parse(html(state), "AAPL", "苹果", NOW)
        self.assertEqual(result.completed["AAPL"].asof, moment("16:00"))

    def test_premarket_uses_same_source_previous_regular_close(self):
        state = fixture()
        state["stock_info"]["before_open_stock_info"] = {
            "status": 1, "exchange_time": moment("05:10", "2026-10-01").timestamp() * 1000, "price": "335.02",
        }
        result = parse(html(state), "AAPL", "苹果", moment("06:00", "2026-10-01"))
        self.assertEqual(result.premarket["AAPL"].previous_close, 333.02)
        self.assertEqual(result.premarket["AAPL"].previous_date, date(2026, 9, 30))

    def test_source_identity_and_invalid_prices_fail_without_raising(self):
        for overrides in ({"stockCode": "MSFT"}, {"marketLabel": "HK"},
                          {"price": "NaN"}, {"price": "Infinity"}, {"price": -1}, {"price": True}):
            with self.subTest(overrides=overrides):
                state = fixture()
                state["stock_info"].update(overrides)
                state["stock_info"].pop("before_open_stock_info")
                state.pop("stock_charts_data")
                result = parse(html(state), "AAPL", "苹果", NOW)
                self.assertFalse(result.quotes)
                self.assertTrue(result.notes)

    def test_quote_with_invalid_reference_keeps_price_without_fake_percent(self):
        state = fixture()
        state.pop("stock_charts_data")
        state["stock_info"]["priceLastClose"] = "NaN"
        result = parse(html(state), "AAPL", "苹果", NOW)
        self.assertEqual(result.quotes["AAPL"].last, 333.02)
        self.assertIsNone(result.quotes["AAPL"].pct)

    def test_future_and_old_source_clocks_are_not_replaced_with_fetch_time(self):
        for stamp in (NOW.timestamp() + 1, (NOW - timedelta(days=8)).timestamp()):
            with self.subTest(stamp=stamp):
                state = fixture()
                state.pop("stock_charts_data")
                state["stock_info"].pop("before_open_stock_info")
                state["stock_info"]["exchangeDataTimeMs"] = stamp * 1000
                self.assertFalse(parse(html(state), "AAPL", "苹果", NOW).quotes)

    def test_futures_use_settlement_and_expose_ten_minute_delay(self):
        state = {"stock_info": {
            "stockCode": "ESMAIN", "marketLabel": "US", "price": "7749.50",
            "priceLastClose": "100", "future": {"lastSettlementPrice": "7715.50"},
            "exchangeDataTimeMs": moment("23:04").timestamp() * 1000, "delaySeconds": 600,
        }}
        result = parse(html(state), "ES=F", "标普期货", NOW)
        value = result.quotes["ES=F"]
        self.assertEqual((value.previous_close, value.session, value.delay_minutes), (7715.5, "futures", 10))
        self.assertAlmostEqual(value.pct, (7749.5 / 7715.5 - 1) * 100)
        self.assertFalse(result.completed)
        self.assertIn("10", result.notes[0])

    def test_treasury_index_is_scaled_to_actual_yield_percent(self):
        state = {"stock_info": {
            "stockCode": ".TNX", "marketLabel": "US", "price": "52.930", "priceLastClose": "52.550",
            "exchangeDataTimeMs": moment("14:59:55.108").timestamp() * 1000,
        }}
        value = parse(html(state), "^TNX", "10年收益率", NOW).quotes["^TNX"]
        self.assertEqual((value.last, value.previous_close, value.unit, value.session), (5.293, 5.255, "%", "reference"))

    def test_minute_raw_prices_are_scaled_once(self):
        state = fixture()
        state["stock_info"]["exchangeDataTimeMs"] = None
        state["stock_charts_data"]["minuteChartsData"]["list"] = [{"time": moment("16:00").timestamp(), "price": 333020}]
        value = parse(html(state), "AAPL", "苹果", NOW).completed["AAPL"]
        self.assertEqual((value.last, value.previous_close), (333.02, 329.4))

    def test_other_instrument_minute_chart_cannot_override_quote(self):
        state = fixture()
        state["stock_charts_data"]["minuteChartsData"]["stockId"] = "OTHER"
        state["stock_charts_data"]["minuteChartsData"]["list"] = [bar("16:00", 999)]
        result = parse(html(state), "AAPL", "苹果", NOW)
        self.assertEqual(result.quotes["AAPL"].last, 333.02)
        self.assertFalse(result.premarket)

    def test_minute_and_rounded_volumes_do_not_become_complete_day_volume(self):
        state = fixture()
        state["stock_info"]["volume"] = "9999万"
        start = session_open(date(2026, 9, 30))
        rows = [{"time": (start + timedelta(minutes=n)).timestamp(), "cc_price": 333.02, "volume": n}
                for n in range(1, 391)]
        state["stock_charts_data"]["minuteChartsData"]["list"] = rows
        value = parse(html(state), "AAPL", "苹果", NOW).completed["AAPL"]
        self.assertIsNone(value.volume)
        self.assertIsNone(value.previous_volume)
        state["stock_charts_data"]["minuteChartsData"]["list"] = rows[:-1]
        self.assertIsNone(parse(html(state), "AAPL", "苹果", NOW).completed["AAPL"].volume)

    def test_halfday_end_bars_use_actual_exchange_schedule(self):
        state = fixture()
        state["stock_info"]["exchangeDataTimeMs"] = moment("13:00", "2026-11-27").timestamp() * 1000
        state["stock_info"].pop("before_open_stock_info")
        state["stock_charts_data"]["minuteChartsData"]["list"] = [
            bar("13:00", day="2026-11-27"), bar("17:00", 334.25, day="2026-11-27"),
            bar("18:00", 999, day="2026-11-27"),
        ]
        result = parse(html(state), "AAPL", "苹果", moment("19:00", "2026-11-27"))
        self.assertEqual(result.completed["AAPL"].asof, session_close(date(2026, 11, 27)))
        self.assertEqual(result.postmarket["AAPL"].asof, moment("17:00", "2026-11-27"))
        self.assertEqual(result.postmarket["AAPL"].last, 334.25)

    def test_public_load_uses_plain_get_without_retry_or_private_keys(self):
        with patch("usstock.sources.futu.fetch_text", return_value=html(fixture())) as fetch:
            self.assertTrue(load("AAPL", "苹果", NOW).quotes)
            fetch.assert_called_once_with("https://www.futunn.com/stock/AAPL-US",
                                          referer="https://www.futunn.com/", timeout=10, retries=0)
        with patch("usstock.sources.futu.fetch_text", side_effect=URLError("unavailable")):
            self.assertTrue(load("AAPL", "苹果", NOW).notes)
        with patch("usstock.sources.futu.fetch_text") as fetch:
            self.assertTrue(load("UNKNOWN", "未知", NOW).notes)
            fetch.assert_not_called()

    def test_live_load_validates_clock_when_response_finishes(self):
        state = fixture()
        state.pop("stock_charts_data")
        state["stock_info"]["exchangeDataTimeMs"] = moment("16:00").timestamp() * 1000
        before = moment("15:59:59")
        after = moment("16:00:01")
        with patch("usstock.sources.futu.fetch_text", return_value=html(state)):
            self.assertFalse(load("AAPL", "苹果", before).quotes)
            value = load("AAPL", "苹果", before, clock=lambda: after).quotes["AAPL"]
        self.assertEqual((value.asof, value.observed_at), (moment("16:00"), after))

    def test_non_quote_html_and_malformed_state_return_empty_partial(self):
        for page in ("<html>challenge</html>", "<script>window.__INITIAL_STATE__={bad};</script>"):
            result = parse(page, "AAPL", "苹果", NOW)
            self.assertFalse(result.quotes)
            self.assertTrue(result.notes)
        self.assertEqual(len(SYMBOLS), 32)


if __name__ == "__main__":
    unittest.main()
