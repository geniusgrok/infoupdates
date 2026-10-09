import io
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch
from urllib.error import HTTPError

from ashare.compose import build_brief
from ashare.data import load_capital, load_flows, load_indices, load_market, load_turnover_comparison
from ashare.history import load_daily
from ashare.models import (
    CST, Breadth, CapitalMix, CrossBorder, MarketData, Quote, SectorFlow, SectorMove, TurnoverComparison,
)
from ashare.parse import parse_kamt, parse_tencent_capital, parse_tencent_turnover
from ashare.social import social_copy
from ashare.sources import eastmoney_capital, eastmoney_cross_border
from common.http import fetch_bytes


def _response(body: bytes):
    class Response:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Response()


class HttpRetryTests(unittest.TestCase):
    def test_retries_reset_empty_body_and_429_but_not_404(self):
        with patch("common.http.time.sleep"):
            calls = {"n": 0}

            def urlopen(request, timeout=0):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise ConnectionResetError("reset")
                return _response(b"ok")

            with patch("common.http.urllib.request.urlopen", side_effect=urlopen):
                self.assertEqual(fetch_bytes("https://example.test/a", "https://example.test/", retries=2), b"ok")
            self.assertEqual(calls["n"], 3)

            calls["n"] = 0

            def empty_then_body(request, timeout=0):
                calls["n"] += 1
                return _response(b"" if calls["n"] == 1 else b"payload")

            with patch("common.http.urllib.request.urlopen", side_effect=empty_then_body):
                self.assertEqual(fetch_bytes("https://example.test/b", "https://example.test/", retries=1), b"payload")

            def limited(request, timeout=0):
                raise HTTPError(request.full_url, 429, "rate", {}, io.BytesIO(b"later"))

            with patch("common.http.urllib.request.urlopen", side_effect=limited) as request:
                with self.assertRaises(HTTPError):
                    fetch_bytes("https://example.test/c", "https://example.test/", retries=1)
                self.assertEqual(request.call_count, 2)

            def missing(request, timeout=0):
                raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO(b"no"))

            with patch("common.http.urllib.request.urlopen", side_effect=missing) as request:
                with self.assertRaises(HTTPError):
                    fetch_bytes("https://example.test/d", "https://example.test/", retries=3)
                self.assertEqual(request.call_count, 1)


class ParserTests(unittest.TestCase):
    def test_tencent_turnover_uses_wan_yuan_and_adjacent_sessions(self):
        payload = {"data": {"sh000001": {"day": [
            ["2026-10-08", "1", "3811.90", "2", "3", "4", {}, "0.99", "81118309.64"],
            ["2026-10-09", "1", "3813.79", "2", "3", "4", {}, "1.10", "88856120"],
        ]}}}
        comparison = parse_tencent_turnover(payload, date(2026, 10, 9))
        self.assertEqual(comparison.previous_date, date(2026, 10, 8))
        self.assertAlmostEqual(comparison.previous, 81118309.64 * 10_000)
        self.assertAlmostEqual(comparison.current, 88856120 * 10_000)
        self.assertEqual(comparison.source, "腾讯")

    def test_tencent_capital_keeps_the_requested_session(self):
        payload = {"data": {
            "todayFundFlow": {"mainNetIn": "-1", "superFlow": "-2", "bigFlow": "-3", "normalFlow": "4", "smallFlow": "5"},
            "historyFundFlow": {"oneDayKlineList": [
                {"date": "2026-10-08", "mainNetIn": "-17777135654"},
                {"date": "2026-10-09", "mainNetIn": "-6657838932"},
            ]},
        }}
        previous = parse_tencent_capital("沪市", payload, "2026-10-08")
        self.assertEqual(previous.trade_day, "2026-10-08")
        self.assertEqual(previous.main, -17777135654)
        self.assertIsNone(previous.super_order)
        current = parse_tencent_capital("沪市", payload, "2026-10-09")
        self.assertEqual(current.main, -1)
        self.assertEqual(current.super_order, -2)

    def test_kamt_ignores_unpublished_north_zero_and_converts_wan_yuan(self):
        payload = {"data": {
            "hk2sh": {"status": 3, "date2": "2026-10-09", "buySellAmt": 100, "netBuyAmt": 0},
            "hk2sz": {"status": 3, "date2": "2026-10-09", "buySellAmt": 0, "netBuyAmt": 0},
            "sh2hk": {"status": 1, "date2": "2026-10-09", "buySellAmt": 1, "netBuyAmt": -10},
            "sz2hk": {"status": 1, "date2": "2026-10-09", "buySellAmt": 1, "netBuyAmt": 4},
        }}
        cross = parse_kamt(payload, "2026-10-09")
        self.assertEqual(cross.north_turnover, 100 * 10_000)
        self.assertEqual(cross.south_net, -6 * 10_000)
        self.assertIsNone(parse_kamt(payload, "2026-10-08"))

    def test_eastmoney_capital_selects_the_reference_day_not_the_latest_bar(self):
        payload = {"data": {"klines": [
            "2026-10-08,-100,1,2,3,4",
            "2026-10-09,-9,1,2,3,4",
        ]}}
        with patch("ashare.sources.push_json", return_value=payload):
            mix = eastmoney_capital("1.000001", "沪市", "2026-10-08")
        self.assertEqual(mix.trade_day, "2026-10-08")
        self.assertEqual(mix.main, -100)


def _index(name, symbol, day, amount=811e9):
    return Quote(symbol, name, 3811.9, pct=-0.79, trade_day=day, session="15:00:00", amount=amount, source="腾讯日线")


class FallbackChainTests(unittest.TestCase):
    def test_preopen_live_date_rolls_forward_but_brief_uses_previous_close(self):
        now = datetime(2026, 10, 9, 9, 28, tzinfo=CST)
        rolled = [Quote("sh000001", "上证指数", 3810, pct=0, trade_day="2026-10-09", session="09:25:00")]

        def daily(symbol, name, start, end, observed_at, required_day=None):
            amount = 811e9 if symbol == "sh000001" else 100e9
            return [_index(name, symbol, "2026-09-30", amount), _index(name, symbol, "2026-10-08", amount)]

        with patch("ashare.sources.sina_indices", return_value=rolled), \
             patch("ashare.sources.tencent_indices", return_value=rolled), \
             patch("ashare.history.load_daily", side_effect=daily):
            quotes, note = load_indices(now)
        self.assertEqual(quotes[0].trade_day, "2026-10-08")
        self.assertIn("指数改用日线", note)
        self.assertTrue(all(quote.trade_day == "2026-10-08" for quote in quotes))

    def test_turnover_capital_and_flows_skip_failed_sources(self):
        day = date(2026, 10, 9)
        comparison = TurnoverComparison(day, date(2026, 10, 8), 888e9, 811e9, "腾讯")
        flows = ([SectorFlow("pt", "有色金属", 3.1e9)], [SectorFlow("pt2", "电子", -1.4e9)])
        with patch("ashare.sources.sohu_turnover", return_value=None), \
             patch("ashare.sources.eastmoney_turnover", side_effect=ConnectionResetError("reset")), \
             patch("ashare.sources.tencent_turnover", return_value=comparison):
            self.assertEqual(load_turnover_comparison(day).source, "腾讯")
        with patch("ashare.sources.eastmoney_capital", side_effect=ConnectionResetError("reset")), \
             patch("ashare.sources.tencent_capital", side_effect=lambda code, market, trade_day="": CapitalMix(
                 market, -1e9, -2e8, -8e8, 3e8, 7e8, trade_day=trade_day or "2026-10-09")):
            rows, note = load_capital("2026-10-09")
        self.assertEqual(len(rows), 2)
        self.assertIn("腾讯", note)
        with patch("ashare.sources.eastmoney_flows", side_effect=RuntimeError("东财行业资金为空")), \
             patch("ashare.sources.sina_sector_money", side_effect=TimeoutError("timed out")), \
             patch("ashare.sources.tencent_flows", return_value=flows):
            inflow, outflow, source, note = load_flows()
        self.assertEqual(source, "腾讯行业")
        self.assertEqual(inflow[0].name, "有色金属")
        self.assertIn("腾讯", note)
        self.assertTrue(outflow)

    def test_cross_border_uses_realtime_when_the_daily_report_is_empty(self):
        def fetch(url, referer, **kwargs):
            if "kamt/get" in url:
                return json.dumps({"data": {
                    "hk2sh": {"status": 3, "date2": "2026-10-09", "buySellAmt": 14168318.65, "netBuyAmt": 0},
                    "hk2sz": {"status": 3, "date2": "2026-10-09", "buySellAmt": 15505216.6, "netBuyAmt": 0},
                    "sh2hk": {"status": 1, "date2": "2026-10-09", "netBuyAmt": -180156.6},
                    "sz2hk": {"status": 1, "date2": "2026-10-09", "netBuyAmt": 211117.59},
                }})
            if "datacenter-web.eastmoney.com" in url:
                return json.dumps({"success": False, "result": None, "message": "返回数据为空", "code": 9201})
            raise AssertionError(url)

        with patch("ashare.sources.fetch_text", side_effect=fetch):
            cross = eastmoney_cross_border("2026-10-09")
        self.assertAlmostEqual(cross.north_turnover, (14168318.65 + 15505216.6) * 10_000, delta=1)
        self.assertAlmostEqual(cross.south_net, (-180156.6 + 211117.59) * 10_000, delta=1)
        self.assertEqual(cross.trade_day, "2026-10-09")

    def test_history_uses_tencent_when_sohu_and_eastmoney_fail(self):
        observed = datetime(2026, 10, 9, 16, tzinfo=CST)
        bars = [
            Quote("sh000001", "上证指数", 3842.19, trade_day="2026-09-30", session="15:00:00", amount=679e9, source="腾讯日线"),
            Quote("sh000001", "上证指数", 3811.9, trade_day="2026-10-08", session="15:00:00", amount=811e9, source="腾讯日线"),
        ]
        with patch("ashare.history.fetch_text", side_effect=OSError("sohu down")), \
             patch("ashare.sources.push_json", side_effect=ConnectionResetError("eastmoney reset")), \
             patch("ashare.sources.tencent_kline", return_value=bars):
            quotes = load_daily("sh000001", "上证指数", date(2026, 9, 30), date(2026, 10, 8), observed, required_day=date(2026, 10, 8))
        self.assertEqual([quote.trade_day for quote in quotes], ["2026-09-30", "2026-10-08"])
        self.assertEqual(quotes[-1].source, "腾讯日线")
        self.assertAlmostEqual(quotes[-1].amount, 811e9)

    def test_morning_and_close_stay_complete_when_primary_sources_fail(self):
        morning_at = datetime(2026, 10, 9, 8, tzinfo=CST)
        close_at = datetime(2026, 10, 9, 15, 40, tzinfo=CST)

        def daily(symbol, name, start, end, observed_at, required_day=None):
            day = required_day.isoformat()
            amount = 811e9 if symbol == "sh000001" else 870e9
            prior = "2026-09-30" if day == "2026-10-08" else "2026-10-08"
            return [_index(name, symbol, prior, amount * 0.9), _index(name, symbol, day, amount)]

        def capital(code, market, trade_day=""):
            return CapitalMix(market, -2e9, -1e9, -1e9, 5e8, 1.5e9, trade_day=trade_day)

        def turnover(trade_date):
            previous = date(2026, 10, 8) if trade_date == date(2026, 10, 9) else date(2026, 9, 30)
            return TurnoverComparison(trade_date, previous, 888e9, 811e9, "腾讯")

        def cross(trade_day=""):
            return CrossBorder(2.9e11, -1.3e9, -1.8e9, 5e8, trade_day=trade_day)

        patches = patch.multiple(
            "ashare.sources",
            sina_indices=lambda: (_ for _ in ()).throw(TimeoutError("sina")),
            tencent_indices=lambda: (_ for _ in ()).throw(ConnectionResetError("tencent")),
            sina_overseas=lambda: ([Quote("gb_dji", "道琼斯", 40000, 0.1, session="10-08 收盘")],
                                   [Quote("fx", "在岸人民币", 7.1, 0.01)]),
            tencent_overseas=lambda: ([], []),
            sina_sectors=lambda: ([SectorMove("陶瓷行业", 2.1, "龙头")], [SectorMove("电子信息", -1.4)]),
            eastmoney_flows=lambda: (_ for _ in ()).throw(RuntimeError("东财行业资金为空")),
            sina_sector_money=lambda: (_ for _ in ()).throw(TimeoutError("timed out")),
            tencent_flows=lambda: ([SectorFlow("a", "有色金属", 3e9)], [SectorFlow("b", "电子", -1e9)]),
            eastmoney_capital=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionResetError("reset")),
            tencent_capital=capital,
            sohu_turnover=lambda trade_date: None,
            eastmoney_turnover=lambda trade_date: (_ for _ in ()).throw(ConnectionResetError("kline reset")),
            tencent_turnover=turnover,
            eastmoney_breadth=lambda day: Breadth(1644, 3615, 100, 40, 8),
            eastmoney_cross_border=cross,
            news=lambda: [],
        )
        with patches, patch("ashare.history.load_daily", side_effect=daily):
            morning_data = load_market(morning_at)
            morning = build_brief("morning", morning_data, morning_at)
            close_data = load_market(close_at)
            close = build_brief("close", close_data, close_at)

        morning_text = social_copy(morning)
        close_text = social_copy(close)
        self.assertEqual(morning.trade_date, date(2026, 10, 8))
        self.assertEqual(morning.edition_date, date(2026, 10, 9))
        self.assertIn("A股参考10月8日收盘", morning_text)
        self.assertNotIn("10月9日行情", morning_text)
        self.assertNotIn("A股数据暂缺", morning_text)
        self.assertNotIn("量能待确认", morning_text)
        self.assertNotIn("主力资金待确认", morning_text)
        self.assertIn("成交额较上日", morning_text)
        self.assertIsNotNone(morning.cross.north_turnover)
        self.assertTrue(morning.sector_in)
        self.assertEqual(morning.flow_source, "腾讯行业")
        self.assertEqual(close.trade_date, date(2026, 10, 9))
        self.assertIn("10月9日收盘", close_text)
        self.assertIn("成交额较上日", close_text)
        self.assertNotIn("量能待确认", close_text)
        self.assertNotIn("主力资金待确认", close_text)
        self.assertNotIn("上日完整成交额暂缺", close_text)
        self.assertIsNotNone(close.cross.south_net)
        self.assertEqual(close.turnover_comparison.source, "腾讯")

    def test_total_failure_still_names_the_missing_field(self):
        with patch("ashare.sources.eastmoney_capital", side_effect=ConnectionResetError("reset")), \
             patch("ashare.sources.tencent_capital", side_effect=TimeoutError("timed out")):
            rows, note = load_capital("2026-10-08")
        self.assertEqual(rows, [])
        self.assertIn("主力资金暂缺", note)


def _empty_market(now):
    with patch("ashare.sources.sina_indices", side_effect=TimeoutError("sina")), \
         patch("ashare.sources.tencent_indices", side_effect=TimeoutError("tencent")), \
         patch("ashare.history.load_daily", side_effect=OSError("daily")), \
         patch("ashare.sources.sina_overseas", side_effect=TimeoutError("overseas")), \
         patch("ashare.sources.tencent_overseas", side_effect=TimeoutError("overseas")), \
         patch("ashare.sources.sina_sectors", side_effect=TimeoutError("sectors")), \
         patch("ashare.sources.sina_sector_money", side_effect=TimeoutError("sectors")), \
         patch("ashare.sources.eastmoney_flows", side_effect=RuntimeError("empty")), \
         patch("ashare.sources.tencent_flows", side_effect=TimeoutError("flows")), \
         patch("ashare.sources.eastmoney_capital", side_effect=ConnectionResetError("reset")), \
         patch("ashare.sources.tencent_capital", side_effect=TimeoutError("capital")), \
         patch("ashare.sources.sohu_turnover", return_value=None), \
         patch("ashare.sources.eastmoney_turnover", side_effect=ConnectionResetError("reset")), \
         patch("ashare.sources.tencent_turnover", side_effect=TimeoutError("turnover")), \
         patch("ashare.sources.eastmoney_breadth", side_effect=TimeoutError("breadth")), \
         patch("ashare.sources.eastmoney_cross_border", side_effect=RuntimeError("跨境资金为空")), \
         patch("ashare.sources.news", side_effect=TimeoutError("news")):
        return load_market(now)


class EmptyMarketTests(unittest.TestCase):
    def test_missing_quotes_do_not_label_the_morning_as_the_unopened_session(self):
        now = datetime(2026, 10, 9, 8, tzinfo=CST)
        data = _empty_market(now)
        brief = build_brief("morning", data, now)
        text = social_copy(brief)
        self.assertEqual(brief.trade_date, date(2026, 10, 8))
        self.assertIn("A股参考10月8日行情待确认", text)
        self.assertNotIn("A股参考10月9日", text)
        self.assertTrue(any("主力资金暂缺" in note or "沪市成交额比较暂缺" in note or "跨境资金暂缺" in note for note in brief.notes))

    def test_calendar_fallback_uses_previous_session_without_a_quote(self):
        now = datetime(2026, 10, 9, 9, 28, tzinfo=CST)
        brief = build_brief("morning", MarketData(indices=[]), now)
        self.assertEqual(brief.trade_date, date(2026, 10, 8))
        self.assertEqual(brief.edition_date, date(2026, 10, 9))
        self.assertIn("A股参考10月8日行情待确认", social_copy(brief))
