from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from common import news


class SharedNewsTests(unittest.TestCase):
    def test_malformed_provider_envelope_is_a_controlled_failure(self):
        for payload in ([], {"data": ["provider error"]}, {"data": {"items": {}}}):
            with self.subTest(payload=payload), patch.object(news, "fetch_text", return_value=json.dumps(payload)):
                with self.assertRaises(ValueError):
                    news.wscn_items("global-channel", pages=1)
        for payload in ([], {"data": ["provider error"]}, {"data": {"fastNewsList": {}}}):
            with self.subTest(payload=payload), patch.object(news, "fetch_text", return_value=json.dumps(payload)):
                with self.assertRaises(ValueError):
                    news.em_items("103")

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


if __name__ == '__main__':
    unittest.main()
