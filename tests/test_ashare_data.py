from __future__ import annotations

import json
import math
import unittest
from datetime import date, datetime, timezone
from dataclasses import replace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from ashare import data, parse, sources
from common.format import fmt_amount, fmt_pct, fmt_px, fmt_yi
from ashare.models import CST, CapitalMix, Quote, TurnoverComparison


def cn_body(last: str = '3842.19') -> str:
    return f'上证指数,3839.25,3830.45,{last},3851.22,3833.09,0,0,414560247,679398992445,2026-09-30,15:35:28'


def qq_body(change: str = '11.74', pct: str = '0.31') -> str:
    fields = ['0'] * 38
    fields[1:6] = ['上证指数', '000001', '3842.19', '3830.45', '3839.25']
    fields[30:38] = ['20260930161500', change, pct, '3851.22', '3833.09', '3842.19/414560247/679398992445', '414560247', '67939899']
    return '~'.join(fields)


def history_row(day: str, amount: str, volume: str = '999999999') -> list[str]:
    return [day, '3800', '3842.19', '11', '0.31%', '3790', '3860', volume, amount, '-']


def history_payload(rows: list[list[str]]) -> list[dict]:
    return [{'status': 0, 'code': 'zs_000001', 'hq': rows}]


class NumericDataTests(unittest.TestCase):
    def test_nonfinite_quotes_are_not_market_prices(self) -> None:
        for bad in ('NaN', 'inf', '-inf'):
            with self.subTest(bad=bad):
                self.assertIsNone(parse.parse_cn_index('sh000001', cn_body(bad)))
                self.assertIsNone(parse.parse_us_index('gb_dji', f'道琼斯,{bad},0.1,2026-09-30,1', '道琼斯'))
                self.assertIsNone(parse.parse_nikkei('b_NKY', f'日经,{bad},1,0.1'))

    def test_missing_tencent_fields_keep_their_positions(self) -> None:
        quote = parse.parse_tencent_quote('sh000001', qq_body(pct='--'), '上证指数')
        assert quote is not None
        self.assertAlmostEqual(quote.pct, (3842.19 - 3830.45) / 3830.45 * 100)
        self.assertEqual(quote.high, 3851.22)
        self.assertEqual(quote.low, 3833.09)

    def test_missing_tencent_fx_percent_does_not_take_change_or_later_metrics(self) -> None:
        body = '310~美元人民币~USDCNY~6.7046~0~20260930210758~6.7065~6.7050~6.7055~6.7030~6.7046~6.7047~-0.0019~--~0.06'
        quote = parse.parse_tencent_fx('fx_susdcny', body, '在岸人民币')
        assert quote is not None
        self.assertIsNone(quote.pct)

    def test_cn_quotes_preserve_snapshot_clock(self) -> None:
        sina = parse.parse_cn_index('sh000001', cn_body())
        qq = parse.parse_tencent_quote('sh000001', qq_body(), '上证指数')
        assert sina is not None and qq is not None
        self.assertEqual(sina.session, '15:35:28')
        self.assertEqual(qq.session, '16:15')

    def test_missing_limit_pool_count_is_not_an_approximation(self) -> None:
        breadth = parse.parse_fenbu([{'11': 20}, {'-11': 10}, {'0': 5}])
        self.assertIsNone(breadth.limit_up)
        self.assertIsNone(breadth.limit_down)

    def test_zero_tencent_amount_is_known_zero(self) -> None:
        fields = qq_body().split('~')
        fields[37] = '0'
        quote = parse.parse_tencent_quote('sh000001', '~'.join(fields), '上证指数')
        assert quote is not None
        self.assertEqual(quote.amount, 0)

    def test_invalid_capital_is_missing(self) -> None:
        self.assertIsNone(parse.parse_fflow_line('沪市', '2026-09-30,NaN,1,2,3,4'))
        self.assertIsNone(parse.parse_tencent_capital('沪市', {'todayFundFlow': {
            'mainNetIn': 'inf', 'superFlow': '1', 'bigFlow': '2', 'normalFlow': '3', 'smallFlow': '4'}}))

    def test_cross_border_bad_field_does_not_discard_valid_other_side(self) -> None:
        cross = parse.parse_cross_border({'NF_DEAL_AMT': '--'}, {'006': {'NET_DEAL_AMT': '100'}})
        self.assertIsNone(cross.north_turnover)
        self.assertEqual(cross.south_net, 100_000_000)

    def test_southbound_splits_do_not_mix_trade_dates(self) -> None:
        cross = parse.parse_cross_border(None, {
            '006': {'TRADE_DATE': '2026-09-30 00:00:00', 'NET_DEAL_AMT': '100'},
            '002': {'TRADE_DATE': '2026-09-29 00:00:00', 'NET_DEAL_AMT': '40'},
            '004': {'TRADE_DATE': '2026-09-30 00:00:00', 'NET_DEAL_AMT': '60'},
        })
        self.assertEqual(cross.south_net, 100_000_000)
        self.assertIsNone(cross.south_sh)
        self.assertEqual(cross.south_sz, 60_000_000)

    def test_nonfinite_numbers_format_as_missing(self) -> None:
        for formatter in (fmt_amount, fmt_pct, fmt_px, fmt_yi):
            for bad in (math.nan, math.inf, -math.inf):
                with self.subTest(formatter=formatter.__name__, bad=bad):
                    self.assertEqual(formatter(bad), '—')

    def test_signed_rounding_does_not_lose_sign_at_half_unit(self) -> None:
        self.assertEqual(fmt_yi(5_000_000, signed=True), '+0.1亿')
        self.assertEqual(fmt_yi(-5_000_000, signed=True), '-0.1亿')
        self.assertEqual(fmt_yi(400_000, signed=True, digits=2), '0.00亿')
        self.assertEqual(fmt_yi(1_000_000, signed=True, digits=2), '+0.01亿')
        self.assertEqual(fmt_yi(-1_000_000, signed=True, digits=2), '-0.01亿')
        self.assertEqual(fmt_yi(10_000_000, signed=True, digits=0), '0亿')
        self.assertEqual(fmt_pct(0.04, digits=1), '0.0%')

    def test_capital_trade_date_is_parsed_from_source(self) -> None:
        capital = parse.parse_fflow_line('沪市', '2026-09-30,-100,90,10,-60,-40')
        assert capital is not None
        self.assertEqual(capital.trade_day, '2026-09-30')
        qq = parse.parse_tencent_capital('沪市', {'data': {
            'todayFundFlow': {'mainNetIn': '-100', 'superFlow': '-40', 'bigFlow': '-60', 'normalFlow': '10', 'smallFlow': '90'},
            'todayFundTrend': {'minList': [{'time': '202609301500'}]},
        }})
        assert qq is not None
        self.assertEqual(qq.trade_day, '2026-09-30')


class MarketSourceTests(unittest.TestCase):
    def market_jobs(self, hero: Quote) -> dict[str, Mock]:
        return {
            'load_indices': Mock(return_value=([hero], None)),
            'load_overseas': Mock(return_value=([], [], None)),
            'load_sectors': Mock(return_value=([], [], '新浪行业', None)),
            'load_flows': Mock(return_value=([], [], '东财行业', None)),
            'load_capital': Mock(return_value=([], None)),
            'eastmoney_breadth': Mock(return_value=None),
            'eastmoney_cross_border': Mock(return_value=None),
            'news': Mock(return_value=[]),
            'load_turnover_comparison': Mock(return_value=None),
        }

    def test_market_loads_history_for_quote_day_and_keeps_same_source_amounts(self) -> None:
        hero = Quote('sh000001', '上证指数', 3842.19, trade_day='2026-09-30', amount=1)
        overrides = self.market_jobs(hero)
        comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 679_398_960_000, 661_704_280_000)
        overrides['load_turnover_comparison'].return_value = comparison
        with patch.multiple(data, **{key: value for key, value in overrides.items() if key.startswith('load_')}), \
                patch.multiple(sources, **{key: value for key, value in overrides.items() if not key.startswith('load_')}):
            market = data.load_market()
        overrides['load_turnover_comparison'].assert_called_once_with(date(2026, 9, 30))
        overrides['load_capital'].assert_called_once_with('2026-09-30')
        overrides['eastmoney_cross_border'].assert_called_once_with('2026-09-30')
        overrides['eastmoney_breadth'].assert_called_once_with('20260930')
        self.assertEqual(market.turnover_comparison, comparison)
        self.assertEqual(market.turnover_comparison.current, 679_398_960_000)

    def test_unknown_quote_date_does_not_invent_current_day_history(self) -> None:
        overrides = self.market_jobs(Quote('sh000001', '上证指数', 3842.19))
        with patch.multiple(data, **{key: value for key, value in overrides.items() if key.startswith('load_')}), \
                patch.multiple(sources, **{key: value for key, value in overrides.items() if not key.startswith('load_')}):
            market = data.load_market()
        for name in ('load_turnover_comparison', 'load_capital', 'eastmoney_cross_border', 'eastmoney_breadth'):
            overrides[name].assert_not_called()
        self.assertIsNone(market.turnover_comparison)
        self.assertIn('沪市成交额比较暂缺', market.notes)

    def test_optional_source_unexpected_error_preserves_other_results(self) -> None:
        hero = Quote('sh000001', '上证指数', 3842.19, trade_day='2026-09-30')
        overseas = Quote('gb_dji', '道琼斯', 46000)
        comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 150, 100)
        overrides = self.market_jobs(hero)
        overrides['news'].side_effect = AttributeError('changed provider envelope')
        overrides['load_overseas'].return_value = ([overseas], [], None)
        overrides['load_turnover_comparison'].return_value = comparison
        with patch.multiple(data, **{key: value for key, value in overrides.items() if key.startswith('load_')}), \
                patch.multiple(sources, **{key: value for key, value in overrides.items() if not key.startswith('load_')}):
            market = data.load_market()
        self.assertEqual(market.indices, [hero])
        self.assertEqual(market.overseas, [overseas])
        self.assertEqual(market.turnover_comparison, comparison)
        self.assertEqual(market.news, [])
        self.assertIn('要闻暂缺', market.notes)
        self.assertTrue(any('news' in note and 'AttributeError' in note for note in market.notes))

    def test_index_fallback_does_not_mix_trading_dates(self) -> None:
        primary = [Quote('sh000001', '上证指数', 3842.19, trade_day='2026-09-30'),
                   Quote('sz399001', '深证成指', 12800, trade_day='2026-09-29')]
        secondary = [Quote('sz399001', '深证成指', 12887.62, trade_day='2026-09-30'),
                     Quote('sh000688', '科创50', 1500, trade_day='2026-09-29')]
        with patch.object(sources, 'sina_indices', return_value=primary), patch.object(sources, 'tencent_indices', return_value=secondary):
            indices, note = data.load_indices()
        self.assertEqual([q.trade_day for q in indices], ['2026-09-30', '2026-09-30'])
        self.assertEqual(indices[1].last, 12887.62)
        self.assertIn('腾讯', note)

    def test_partial_capital_explains_missing_market(self) -> None:
        sh = CapitalMix('沪市', -100, -40, -60, 10, 90)
        with patch.object(sources, 'eastmoney_capital', side_effect=[sh, RuntimeError('missing')]), patch.object(sources, 'tencent_capital', side_effect=RuntimeError('missing')):
            capital, note = data.load_capital()
        self.assertEqual(capital, [sh])
        self.assertIn('深市', note)
        self.assertIn('暂缺', note)

    def test_capital_rejects_stale_primary_and_uses_same_date_fallback(self) -> None:
        old_sh = CapitalMix('沪市', -100, -40, -60, 10, 90, trade_day='2026-09-29')
        old_sz = CapitalMix('深市', -100, -40, -60, 10, 90, trade_day='2026-09-29')
        new_sh = CapitalMix('沪市', -120, -40, -80, 20, 100, trade_day='2026-09-30')
        new_sz = CapitalMix('深市', -130, -50, -80, 20, 110, trade_day='2026-09-30')
        with patch.object(sources, 'eastmoney_capital', side_effect=[old_sh, old_sz]), patch.object(sources, 'tencent_capital', side_effect=[new_sh, new_sz]):
            capital, note = data.load_capital('2026-09-30')
        self.assertEqual(capital, [new_sh, new_sz])
        self.assertIn('腾讯', note)

    def test_malformed_primary_payload_still_uses_capital_fallback(self) -> None:
        sh = CapitalMix("沪市", -100, -40, -60, 10, 90, trade_day="2026-09-30")
        sz = CapitalMix("深市", -120, -50, -70, 20, 100, trade_day="2026-09-30")
        with patch.object(sources, "fetch_text", return_value="[]"), \
                patch.object(sources, "tencent_capital", side_effect=[sh, sz]):
            capital, note = data.load_capital("2026-09-30")
        self.assertEqual(capital, [sh, sz])
        self.assertIn("腾讯", note)

    def test_malformed_sector_backup_does_not_abort_other_market_data(self) -> None:
        for payload in ("{}", "[null, 1, \"error\"]"):
            with patch.object(sources, "fetch_text", return_value=payload):
                inflow, outflow, _, note = data.load_flows()
            self.assertEqual((inflow, outflow), ([], []))
            self.assertEqual(note, "行业资金暂缺")

    def test_cross_border_invalid_envelope_rows_are_missing(self) -> None:
        for payload in ({"result": []}, {"result": {"data": "error"}}, {"result": {"data": [None]}}):
            self.assertIsNone(sources._first_row(payload))

    def test_cross_border_rejects_latest_rows_from_other_date(self) -> None:
        with patch.object(sources, '_cross_row', return_value={'TRADE_DATE': '2026-09-29 00:00:00', 'NF_DEAL_AMT': '100', 'NET_DEAL_AMT': '100'}):
            with self.assertRaises(RuntimeError):
                sources.eastmoney_cross_border('2026-09-30')


class IndexDateFallbackTests(unittest.TestCase):
    def quotes(self, day: str) -> list[Quote]:
        return [Quote(symbol, name, 3800, pct=.3, trade_day=day)
                for symbol, name in parse.INDEX_NAMES.items()]

    def load(self, primary, secondary, now):
        with patch.object(sources, 'sina_indices', return_value=primary), \
                patch.object(sources, 'tencent_indices', return_value=secondary) as backup:
            result = data.load_indices(now=now)
        return result, backup

    def test_complete_stale_primary_switches_to_newer_same_day_bag(self) -> None:
        primary = self.quotes('2026-09-29')
        secondary = self.quotes('2026-09-30')[:2] + [primary[-1]]
        (indices, note), backup = self.load(primary, secondary, datetime(2026, 9, 30, 10, tzinfo=CST))
        backup.assert_called_once_with()
        self.assertEqual(indices, [replace(quote, source="腾讯") for quote in secondary[:2]])
        self.assertEqual({quote.trade_day for quote in indices}, {'2026-09-30'})
        self.assertIn('腾讯', note)

    def test_both_sources_stale_keep_actual_date_and_explain_delay(self) -> None:
        primary = self.quotes('2026-09-29')
        (indices, note), backup = self.load(primary, self.quotes('2026-09-29'), datetime(2026, 9, 30, 10, tzinfo=CST))
        backup.assert_called_once_with()
        self.assertEqual(indices, [replace(quote, source="新浪") for quote in primary])
        self.assertIn('行情仅到2026-09-29', note)

    def test_future_primary_uses_legal_secondary_and_future_secondary_cannot_replace_old_day(self) -> None:
        now = datetime(2026, 9, 30, 10, tzinfo=CST)
        for primary_day, secondary_day, expected in (('2026-10-08', '2026-09-30', '2026-09-30'),
                                                      ('2026-09-29', '2026-10-08', '2026-09-29')):
            with self.subTest(primary_day=primary_day):
                (indices, note), backup = self.load(self.quotes(primary_day), self.quotes(secondary_day), now)
                backup.assert_called_once_with()
                self.assertEqual({quote.trade_day for quote in indices}, {expected})
                if expected < '2026-09-30':
                    self.assertIn('行情仅到2026-09-29', note)

    def test_preopen_weekend_and_holiday_keep_legal_previous_reference(self) -> None:
        for now, day in ((datetime(2026, 9, 30, 9, 29, tzinfo=CST), '2026-09-29'),
                         (datetime(2026, 9, 26, 10, tzinfo=CST), '2026-09-24'),
                         (datetime(2026, 10, 1, 10, tzinfo=CST), '2026-09-30')):
            with self.subTest(now=now):
                primary = self.quotes(day)
                (indices, note), backup = self.load(primary, [], now)
                backup.assert_not_called()
                self.assertEqual(indices, [replace(quote, source="新浪") for quote in primary])
                self.assertIsNone(note)

    def test_open_boundary_uses_china_clock_and_refreshes_previous_day(self) -> None:
        now = datetime(2026, 9, 30, 1, 30, tzinfo=timezone.utc)
        (indices, _), backup = self.load(self.quotes('2026-09-29'), self.quotes('2026-09-30'), now)
        backup.assert_called_once_with()
        self.assertEqual({quote.trade_day for quote in indices}, {'2026-09-30'})

    def test_unknown_calendar_preserves_actual_day_without_guessing_staleness(self) -> None:
        primary = self.quotes('2026-12-31')
        (indices, note), backup = self.load(primary, [], datetime(2027, 1, 4, 10, tzinfo=CST))
        backup.assert_not_called()
        self.assertEqual(indices, [replace(quote, source="新浪") for quote in primary])
        self.assertNotIn('行情仅到', note or '')

    def test_holiday_source_date_cannot_replace_legal_previous_session(self) -> None:
        primary = self.quotes('2026-09-30')
        (indices, note), backup = self.load(primary, self.quotes('2026-10-01'), datetime(2026, 10, 8, 10, tzinfo=CST))
        backup.assert_called_once_with()
        self.assertEqual(indices, [replace(quote, source="新浪") for quote in primary])
        self.assertIn('行情仅到2026-09-30', note)


class TurnoverHistoryTests(unittest.TestCase):
    def parse_history(self, rows: list[list[str]], day: date = date(2026, 9, 30)):
        return parse.parse_sohu_turnover(history_payload(rows), day)

    def test_amount_uses_column_eight_and_wan_to_yuan(self) -> None:
        comparison = self.parse_history([history_row('2026-09-30', '67939896.00'), history_row('2026-09-29', '66170428.00')])
        assert comparison is not None
        self.assertEqual(comparison.current, 679_398_960_000)
        self.assertEqual(comparison.previous, 661_704_280_000)
        self.assertAlmostEqual((comparison.current - comparison.previous) / 1e8, 176.9468)
        self.assertEqual(comparison.trade_date, date(2026, 9, 30))
        self.assertEqual(comparison.previous_date, date(2026, 9, 29))
        self.assertEqual(comparison.source, '搜狐')

    def test_previous_real_trading_day_after_holiday(self) -> None:
        comparison = self.parse_history([history_row('2026-09-30', '100'), history_row('2026-10-08', '150')], date(2026, 10, 8))
        assert comparison is not None
        self.assertEqual(comparison.previous_date, date(2026, 9, 30))

    def test_current_day_must_match_quote(self) -> None:
        self.assertIsNone(self.parse_history([history_row('2026-09-29', '100'), history_row('2026-09-28', '90')]))

    def test_missing_previous_amount_does_not_skip_to_earlier_day(self) -> None:
        self.assertIsNone(self.parse_history([history_row('2026-09-30', '150'), history_row('2026-09-29', '--'), history_row('2026-09-28', '90')]))

    def test_invalid_amount_is_missing_and_never_volume(self) -> None:
        for invalid in ('--', 'NaN', 'inf', '-1'):
            with self.subTest(invalid=invalid):
                self.assertIsNone(self.parse_history([history_row('2026-09-30', invalid), history_row('2026-09-29', '90')]))

    def test_status_failure_wrong_symbol_and_conflicting_duplicate_are_rejected(self) -> None:
        rows = [history_row('2026-09-30', '150'), history_row('2026-09-29', '90')]
        self.assertIsNone(parse.parse_sohu_turnover([{'status': 1, 'code': 'zs_000001', 'hq': rows}], date(2026, 9, 30)))
        self.assertIsNone(parse.parse_sohu_turnover([{'status': 0, 'code': 'zs_399001', 'hq': rows}], date(2026, 9, 30)))
        self.assertIsNone(self.parse_history(rows + [history_row('2026-09-30', '200')]))

    def test_loader_requests_history_relative_to_quote_date(self) -> None:
        payload = history_payload([history_row('2026-09-30', '150'), history_row('2026-09-29', '90')])
        with patch.object(sources, 'fetch_text', return_value=json.dumps(payload)) as get:
            comparison = data.load_turnover_comparison(date(2026, 9, 30))
        assert comparison is not None
        query = parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertEqual(query['code'], ['zs_000001'])
        self.assertEqual(query['end'], ['20260930'])
        self.assertLessEqual(query['start'][0], '20260916')
        self.assertEqual(comparison.current, 1_500_000)

    def test_history_network_failure_is_safe_missing(self) -> None:
        with patch.object(sources, 'fetch_text', side_effect=OSError('offline')):
            self.assertIsNone(data.load_turnover_comparison(date(2026, 9, 30)))


class TurnoverFallbackTests(unittest.TestCase):
    def payload(self, rows: list[str]) -> dict:
        return {"data": {"code": "000001", "market": 1, "klines": rows}}

    def row(self, day: str, amount: str) -> str:
        return f"{day},3800,3842.19,3860,3790,999999999,{amount},1,0.3,11,0"

    def test_eastmoney_amount_is_rmb_and_not_volume(self) -> None:
        payload = self.payload([self.row("2026-09-30", "679398960000"),
                                self.row("2026-09-29", "661704280000")])
        comparison = parse.parse_eastmoney_turnover(payload, date(2026, 9, 30))
        assert comparison is not None
        self.assertEqual(comparison.current, 679_398_960_000)
        self.assertEqual(comparison.previous, 661_704_280_000)
        self.assertEqual(comparison.source, "东财")

    def test_eastmoney_missing_amount_does_not_substitute_volume_or_earlier_session(self) -> None:
        for amount in ("--", "NaN", "inf", "-1"):
            with self.subTest(amount=amount):
                rows = [self.row("2026-09-30", "150"), self.row("2026-09-29", amount),
                        self.row("2026-09-28", "90")]
                self.assertIsNone(parse.parse_eastmoney_turnover(self.payload(rows), date(2026, 9, 30)))
        rows = [self.row("2026-09-30", "150"), self.row("2026-09-28", "90")]
        self.assertIsNone(parse.parse_eastmoney_turnover(self.payload(rows), date(2026, 9, 30)))

    def test_eastmoney_rejects_another_market_or_conflicting_duplicate(self) -> None:
        rows = [self.row("2026-09-30", "150"), self.row("2026-09-29", "90")]
        payload = self.payload(rows)
        payload["data"]["market"] = 0
        self.assertIsNone(parse.parse_eastmoney_turnover(payload, date(2026, 9, 30)))
        payload = self.payload(rows + [self.row("2026-09-30", "200")])
        self.assertIsNone(parse.parse_eastmoney_turnover(payload, date(2026, 9, 30)))

    def test_source_failure_uses_two_days_from_one_fallback(self) -> None:
        comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 150, 90, "东财")
        with patch.object(sources, "sohu_turnover", side_effect=OSError("offline")), \
                patch.object(sources, "eastmoney_turnover", return_value=comparison) as secondary:
            found = data.load_turnover_comparison(date(2026, 9, 30))
        self.assertEqual(found, comparison)
        secondary.assert_called_once_with(date(2026, 9, 30))

    def test_complete_primary_does_not_fetch_fallback(self) -> None:
        comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 150, 90)
        with patch.object(sources, "sohu_turnover", return_value=comparison), \
                patch.object(sources, "eastmoney_turnover") as secondary:
            self.assertEqual(data.load_turnover_comparison(date(2026, 9, 30)), comparison)
        secondary.assert_not_called()

    def test_missing_previous_session_never_skips_a_trading_day(self) -> None:
        payload = history_payload([history_row("2026-09-30", "150"), history_row("2026-09-28", "90")])
        self.assertIsNone(parse.parse_sohu_turnover(payload, date(2026, 9, 30)))


if __name__ == '__main__':
    unittest.main()
