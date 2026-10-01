import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from ashare.calendar import edition_date as a_edition
from ashare.compose import build_brief as a_brief
from ashare.models import CST, MarketData as AData, Quote as AQuote, TurnoverComparison
from ashare.narrative import market_summary
from common.archive import Archive
from common.events import CalendarEvent
from review.bls import calculate, collect_releases, values
from review.events import comparisons, record_metric
from usstock.calendar import session_close
from usstock.compose import build_brief as u_brief
from usstock.models import NY, MarketData as UData, Quote as UQuote
from usstock.social import social_copy
from weekly.compose import build_weekly


class FinanceTests(unittest.TestCase):
    def test_ashare_turnover_is_yuan_delta_between_adjacent_sessions(self):
        now = datetime(2026, 9, 30, 18, tzinfo=CST)
        data = AData([AQuote('sh000001', '上证指数', 3000, trade_day='2026-09-30', session='15:30:00')])
        data.turnover_comparison = TurnoverComparison(now.date(), date(2026, 9, 29), 679.4e9, 661.71e9)
        line = market_summary(a_brief('close', data, now))
        self.assertIn('增加176.9亿元', line)
        self.assertIn('平量', line)
        self.assertNotIn('%', line)
        self.assertNotIn('亿股', line)
        data.turnover_comparison.previous_date = date(2026, 9, 28)
        self.assertIsNone(a_brief('close', data, now).turnover_comparison)

    def test_premarket_selects_latest_actual_trade_and_never_previous_regular_close(self):
        now = datetime(2026, 10, 1, 8, tzinfo=NY)
        basis = date(2026, 9, 30)
        data = UData(completed={'AAPL': UQuote('AAPL', '苹果', 100, asof=session_close(basis))})
        self.assertEqual(u_brief('premarket', data, now).stocks, [])
        for bag, at, price in (('postmarket', now.replace(day=30, month=9, hour=19), 101),
                               ('overnight', now.replace(hour=2), 102), ('premarket', now.replace(hour=7), 103)):
            getattr(data, bag)['AAPL'] = UQuote('AAPL', '苹果', price, 1, 100, asof=at, session=bag, previous_date=basis)
        brief = u_brief('premarket', data, now)
        self.assertEqual((brief.stocks[0].last, brief.stocks[0].asof), (103, now.replace(hour=7)))
        self.assertEqual(brief.stocks[0].session, 'premarket')

    def test_night_snapshot_keeps_unknown_trade_time_and_expires_at_premarket(self):
        now = datetime(2026, 10, 1, 1, tzinfo=NY)
        data = UData(overnight={'AAPL': UQuote('AAPL', '苹果', 102, 2, 100, session='overnight',
                                              source='Webull', observed_at=now, previous_date=date(2026, 9, 30))})
        brief = u_brief('premarket', data, now)
        self.assertIsNone(brief.stocks[0].asof)
        self.assertIn('成交时间未披露', social_copy(brief))
        self.assertEqual(u_brief('premarket', data, now.replace(hour=4)).stocks, [])

    def test_exchange_holidays_halfday_and_dst(self):
        self.assertEqual(a_edition('morning', datetime(2026, 10, 1, 8, tzinfo=CST), date(2026, 9, 30)), date(2026, 10, 8))
        self.assertEqual(session_close(date(2026, 11, 27)).hour, 13)
        self.assertEqual(session_close(date(2026, 3, 6)).astimezone(timezone.utc).hour, 21)
        self.assertEqual(session_close(date(2026, 3, 9)).astimezone(timezone.utc).hour, 20)

    def test_weekly_returns_use_real_close_endpoints_and_holidays(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            for day, price in ((date(2026, 9, 18), 100), (date(2026, 9, 24), 110)):
                data = AData([AQuote('sh000001', '上证指数', price, pct=99, source='新浪', trade_day=day.isoformat(), session='15:30:00')])
                archive.capture('ashare', data, datetime.combine(day, datetime.min.time(), CST) + timedelta(hours=18))
            for day, price in ((date(2026, 9, 18), 100), (date(2026, 9, 25), 90)):
                data = UData(completed={'^GSPC': UQuote('^GSPC', '标普500', price, pct=99, asof=session_close(day))})
                archive.capture('usstock', data, session_close(day) + timedelta(hours=1))
            brief = build_weekly(archive, datetime(2026, 9, 26, 10, tzinfo=CST))
            self.assertAlmostEqual(brief['metrics'][0]['pct'], 10)
            self.assertAlmostEqual(brief['metrics'][2]['pct'], -10)
            self.assertEqual(brief['metrics'][0]['end_date'], '2026-09-24')
            self.assertEqual(brief['metrics'][0]['expected'], 4)

    def test_weekly_missing_close_or_different_source_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            now = datetime(2026, 9, 26, 10, tzinfo=CST)
            for day, source in ((date(2026, 9, 18), 'Yahoo Finance'), (date(2026, 9, 24), 'Yahoo Finance')):
                data = UData(completed={'^GSPC': UQuote('^GSPC', '标普500', 100, asof=session_close(day), source=source)})
                archive.capture('usstock', data, session_close(day) + timedelta(hours=1))
            self.assertIsNone(build_weekly(archive, now)['metrics'][2]['pct'])
            day = date(2026, 9, 25)
            data = UData(completed={'^GSPC': UQuote('^GSPC', '标普500', 110, asof=session_close(day), source='Cboe')})
            archive.capture('usstock', data, session_close(day) + timedelta(hours=1))
            self.assertIsNone(build_weekly(archive, now)['metrics'][2]['pct'])

    def test_official_results_keep_raw_series_units_and_observed_time(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
            archive.add_event(CalendarEvent('美国非农就业与失业率', at, '美国劳工统计局'), at - timedelta(days=1))
            def fetch(url, *args, **kwargs):
                series = url.split('data/', 1)[1].split('?', 1)[0]
                points = [('2026-08', 159075), ('2026-09', 159225)] if series == 'CES0000000001' else [('2026-09', 4.2)]
                return json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': series, 'data': [
                    {'year': day[:4], 'period': 'M' + day[-2:], 'value': str(value)} for day, value in points]}]}})
            with patch('review.bls.fetch_text', side_effect=fetch) as request:
                collect_releases(archive, at - timedelta(minutes=1))
                request.assert_not_called()
                collect_releases(archive, at + timedelta(hours=2))
                collect_releases(archive, at + timedelta(hours=3))
                self.assertEqual(request.call_count, 2)
            results = archive.events()[0]['evidence']
            data = next(item['data'] for item in results if item['data']['metric'] == '非农新增就业')
            self.assertEqual(data['value'], 15)
            self.assertEqual(values(data['series_payload'], 'CES0000000001')['2026-08'], 159075)
            self.assertIsNone(data['source_published_at'])
            points = {'2025-09': 100, '2026-08': 102.4, '2026-09': 102.5}
            self.assertEqual(calculate(points, '2026-09', 'year'), 2.5)
            self.assertEqual(calculate(points, '2026-09', 'month'), .1)

    def test_late_forecast_is_not_prior_expectation_and_conflicting_results_stay_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Archive(folder)
            at = datetime(2026, 10, 2, 8, 30, tzinfo=NY)
            key = archive.add_event(CalendarEvent('美国非农就业与失业率', at, '美国劳工统计局'), at - timedelta(days=1))
            metric = {'metric': '非农新增就业', 'period': '2026-09', 'unit': '万人', 'value': 17,
                      'source': '见闻', 'url': 'https://wallstreetcn.com/'}
            record_metric(archive, key, 'expectation', metric, at - timedelta(hours=1), at + timedelta(hours=1))
            record_metric(archive, key, 'result', metric | {'value': 15}, at, at + timedelta(hours=1))
            self.assertIn('未留存可比较的事前预期', comparisons(archive.events()[0])[0])
            record_metric(archive, key, 'result', metric | {'value': 16, 'source': '东财'}, at, at + timedelta(hours=1))
            self.assertIn('待核实', comparisons(archive.events()[0])[0])
