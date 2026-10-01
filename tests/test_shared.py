from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from brief_common import news


class SharedNewsTests(unittest.TestCase):
    def test_bad_news_row_does_not_discard_other_valid_rows(self):
        payload = {'data': {'items': [
            {'title': '美国经济数据发布时间已确定', 'display_time': 'bad'},
            {'title': '美国经济数据发布时间已确定', 'display_time': float('inf')},
            None,
            {'title': '美联储官员讲话将受到市场关注', 'display_time': 1790802000, 'score': 'NaN'},
        ]}}
        with patch.object(news, 'fetch_text', return_value=json.dumps(payload)):
            result = news.wscn_items('global-channel', pages=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, '美联储官员讲话将受到市场关注')
        self.assertEqual(result[0].source_score, 1)
        self.assertIsNotNone(result[0].published.tzinfo)

    def test_eastmoney_bad_timestamp_does_not_break_valid_news(self):
        payload = {'data': {'fastNewsList': [
            {'title': '美股三大指数收盘涨跌互现', 'showTime': 'missing-date'},
            {'title': '美股三大指数收盘涨跌互现', 'showTime': '2026-10-01 05:00:00', 'titleColor': 'bad'},
        ]}}
        with patch.object(news, 'fetch_text', return_value=json.dumps(payload)):
            result = news.em_items('103')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_score, 1.4)
        self.assertEqual(result[0].published.hour, 5)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_legacy_data_and_commands_use_the_canonical_ashare_implementation(self):
        from a_share_brief import __main__ as legacy_cli
        from a_share_brief.compose import build_brief as legacy_build
        from a_share_brief.fetch import MarketData
        from a_share_brief.models import CST, Quote
        from ashare.__main__ import main
        from ashare.compose import build_brief
        from ashare.models import Brief

        data = MarketData(indices=[Quote('sh000001', '上证指数', 3842.19, pct=.31,
                                      trade_day='2026-09-30', session='15:35:00')])
        now = datetime(2026, 9, 30, 20, tzinfo=CST)
        self.assertIsInstance(legacy_build('close', data, now=now), Brief)
        self.assertIs(legacy_build, build_brief)
        self.assertIs(legacy_cli.main, main)


if __name__ == '__main__':
    unittest.main()
