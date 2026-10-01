from __future__ import annotations

import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from usstock import data
from usstock.calendar import last_completed_session, previous_trading_day, session_close
from usstock.models import NY, MarketData, Quote

NOW = datetime(2026, 9, 30, 23, 30, tzinfo=NY)


def full(symbol="AAPL", source="Yahoo Finance", now=NOW, last=101) -> MarketData:
    day = last_completed_session(now)
    at = session_close(day)
    regular = Quote(symbol, symbol, last, 1, 100, at,
                    previous_date=previous_trading_day(day), source=source)
    if symbol == "SPY":
        regular.volume, regular.previous_volume = 70_000_000, 60_000_000
    result = MarketData(quotes={symbol: regular}, completed={symbol: regular})
    if symbol in {"AAPL", "XLK"}:
        post = replace(regular, pct=2, previous_close=last, asof=at + timedelta(hours=3),
                       session="postmarket", previous_date=day)
        result.postmarket[symbol] = post
    return result


def snapshot(symbol="AAPL", *, observed=NOW, price=104):
    return Quote(symbol, symbol, price, 4, 100, asof=None, session="overnight",
                 previous_date=last_completed_session(observed), source="Webull", observed_at=observed)


class AggregationTests(unittest.TestCase):
    def capture(self, *, symbol="AAPL", primary=None, secondary=None, final=None,
                now=NOW, snapshots=None, clock=None):
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(data, SYMBOLS={symbol: symbol},
                                MEGA_NAMES={symbol: symbol} if symbol == "AAPL" else {},
                                SECTOR_NAMES={symbol: symbol} if symbol == "XLK" else {}))
            stack.enter_context(patch.object(data, "apply_cache", side_effect=lambda result, **kwargs: result))
            stack.enter_context(patch.object(data, "wscn_items", return_value=[]))
            stack.enter_context(patch.object(data, "em_items", return_value=[]))
            calls = {}
            for provider, payload in ((data.yahoo, primary), (data.nasdaq, secondary),
                                      (data.cboe, secondary), (data.futu, final)):
                options = {"side_effect": payload} if callable(payload) or isinstance(payload, Exception) else {"return_value": payload or MarketData()}
                calls[provider.__name__.rsplit(".", 1)[-1]] = stack.enter_context(patch.object(provider, "load", **options))
            calls["webull"] = stack.enter_context(patch.object(data.webull, "night_snapshots", return_value=snapshots or {}))
            if clock is not None:
                stack.enter_context(patch.object(data, "_clock", side_effect=clock))
            result = data.load_market() if now is None else data.load_market(now=now)
        return result, calls

    def test_complete_primary_does_not_request_secondary_sources(self):
        result, calls = self.capture(primary=full())
        self.assertEqual(result.completed["AAPL"].source, "Yahoo Finance")
        calls["nasdaq"].assert_not_called()
        calls["futu"].assert_not_called()

    def test_primary_source_failure_uses_independent_nasdaq(self):
        result, calls = self.capture(primary=OSError("unavailable"), secondary=full(source="Nasdaq日线"))
        self.assertEqual(result.completed["AAPL"].source, "Nasdaq日线")
        calls["nasdaq"].assert_called_once()
        calls["futu"].assert_not_called()

    def test_unknown_provider_error_is_local_and_fallback_continues(self):
        result, calls = self.capture(primary=AttributeError("bad source response"), secondary=full(source="Nasdaq日线"))
        self.assertIn("AAPL", result.completed)
        self.assertTrue(any("AttributeError" in note for note in result.notes))
        calls["nasdaq"].assert_called_once()

    def test_missing_post_fills_only_that_whole_quote(self):
        primary = full()
        primary.postmarket.clear()
        backup = full(source="Nasdaq日线", last=109)
        result, _ = self.capture(primary=primary, secondary=backup)
        self.assertEqual(result.completed["AAPL"].source, "Yahoo Finance")
        self.assertEqual(result.postmarket["AAPL"].source, "Nasdaq日线")
        self.assertEqual(result.postmarket["AAPL"].previous_close, 109)

    def test_missing_change_quality_gets_whole_secondary_record(self):
        primary = full()
        primary.completed["AAPL"] = replace(primary.completed["AAPL"], pct=None, previous_close=None)
        backup = full(source="Nasdaq日线", last=109)
        result, _ = self.capture(primary=primary, secondary=backup)
        self.assertEqual(result.completed["AAPL"].last, 109)
        self.assertEqual(result.completed["AAPL"].source, "Nasdaq日线")
        self.assertEqual(result.completed["AAPL"].previous_close, 100)

    def test_spy_missing_previous_volume_triggers_nasdaq_same_source_pair(self):
        primary = full("SPY")
        primary.completed["SPY"] = replace(primary.completed["SPY"], volume=5_000_000, previous_volume=None)
        backup = full("SPY", source="Nasdaq日线")
        backup.completed["SPY"] = replace(backup.completed["SPY"], volume=62_110_040, previous_volume=36_910_550)
        result, calls = self.capture(symbol="SPY", primary=primary, secondary=backup)
        chosen = result.completed["SPY"]
        self.assertEqual((chosen.volume, chosen.previous_volume), (62_110_040, 36_910_550))
        self.assertEqual(chosen.source, "Nasdaq日线")
        calls["nasdaq"].assert_called_once()

    def test_future_clock_or_wrong_identity_cannot_block_valid_backup(self):
        for bad in (replace(full().completed["AAPL"], symbol="MSFT"),
                    replace(full().completed["AAPL"], asof=NOW + timedelta(seconds=1))):
            with self.subTest(bad=bad):
                primary = MarketData(quotes={"AAPL": bad}, completed={"AAPL": bad})
                result, _ = self.capture(primary=primary, secondary=full(source="Nasdaq日线"))
                self.assertEqual(result.completed["AAPL"].source, "Nasdaq日线")

    def test_index_falls_back_cboe_before_futu(self):
        result, calls = self.capture(symbol="^RUT", secondary=full("^RUT", source="Cboe日线"))
        self.assertEqual(result.completed["^RUT"].source, "Cboe日线")
        calls["cboe"].assert_called_once()
        calls["futu"].assert_not_called()
        calls["nasdaq"].assert_not_called()

    def test_futures_falls_back_directly_to_futu(self):
        quote = Quote("ES=F", "标普期货", 110, 10, 100, NOW, session="futures", source="Futu", delay_minutes=10)
        result, calls = self.capture(symbol="ES=F", final=MarketData(quotes={"ES=F": quote}))
        self.assertEqual(result.quotes["ES=F"].delay_minutes, 10)
        calls["futu"].assert_called_once()
        calls["nasdaq"].assert_not_called()
        calls["cboe"].assert_not_called()

    def test_real_timestamped_night_does_not_request_webull(self):
        primary = full()
        primary.overnight["AAPL"] = replace(snapshot(), source="BOATS Real Time Price", asof=NOW, observed_at=None)
        result, calls = self.capture(primary=primary)
        self.assertEqual(result.overnight["AAPL"].asof, NOW)
        calls["webull"].assert_not_called()

    def test_missing_night_gets_one_bulk_snapshot_without_faked_asof(self):
        result, calls = self.capture(primary=full(), snapshots={"AAPL": snapshot()})
        chosen = result.overnight["AAPL"]
        self.assertIsNone(chosen.asof)
        self.assertIsNone(chosen.trade_date)
        self.assertEqual(chosen.observed_at, NOW)
        calls["webull"].assert_called_once()

    def test_snapshot_not_loaded_outside_actual_night_window(self):
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        result, calls = self.capture(primary=full(now=now), snapshots={"AAPL": snapshot()}, now=now)
        self.assertNotIn("AAPL", result.overnight)
        calls["webull"].assert_not_called()

    def test_live_response_end_clock_accepts_real_observed_time_after_start(self):
        started = NOW
        finished = NOW + timedelta(seconds=2)
        stamps = iter([started])
        def clock():
            return next(stamps, finished)
        result, calls = self.capture(now=None, clock=clock, primary=full(),
                                     snapshots={"AAPL": snapshot(observed=finished)})
        self.assertEqual(result.overnight["AAPL"].observed_at, finished)
        self.assertTrue(callable(calls["yahoo"].call_args.kwargs["clock"]))

    def test_future_task_and_news_errors_do_not_break_sibling_data(self):
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(data, SYMBOLS={"AAPL": "苹果", "MSFT": "微软"}, MEGA_NAMES={}, SECTOR_NAMES={}))
            stack.enter_context(patch.object(data, "apply_cache", side_effect=lambda result, **kwargs: result))
            def one(symbol, name, now, clock):
                if symbol == "AAPL":
                    raise AttributeError("unexpected task error")
                return full("MSFT")
            stack.enter_context(patch.object(data, "_load_symbol", side_effect=one))
            stack.enter_context(patch.object(data, "wscn_items", side_effect=AttributeError("news error")))
            stack.enter_context(patch.object(data, "em_items", return_value=[]))
            result = data.load_market(now=NOW)
        self.assertIn("MSFT", result.completed)
        self.assertNotIn("AAPL", result.completed)
        self.assertTrue(any("AAPL" in note and "AttributeError" in note for note in result.notes))
        self.assertTrue(any("快讯暂缺" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
