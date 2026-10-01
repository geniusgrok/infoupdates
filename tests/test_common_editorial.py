from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from common.editorial import build_focus
from common.events import CalendarEvent
from common.news import NewsItem

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 1, 14, tzinfo=CST)


class EditorialTests(unittest.TestCase):
    def test_one_relevant_message_and_verified_future_event(self):
        news = [NewsItem(NOW - timedelta(minutes=2), "微软科学总裁宣布离职", "见闻", rank=15),
                NewsItem(NOW - timedelta(hours=1), "美联储官员：通胀仍然过高。需继续关注数据。", "东财", rank=60)]
        event = CalendarEvent("美国非农就业报告", NOW + timedelta(days=1), "美国劳工统计局")
        focus = build_focus(news, NOW, market="ashare", event=event)
        self.assertEqual(len(focus), 2)
        self.assertEqual(focus[0].title, "美联储官员：通胀仍然过高")
        self.assertIn("10-01 13:00 CST", focus[0].stamp)
        self.assertEqual(focus[1].label, "下一事件")
        self.assertIn("10-02 14:00 CST", focus[1].stamp)
        self.assertEqual(len(news), 2)
        self.assertIn("需继续关注", news[1].title)

    def test_future_news_is_never_displayed(self):
        news = [NewsItem(NOW + timedelta(seconds=1), "美国公布最新就业数据", "见闻")]
        focus = build_focus(news, NOW, market="usstock")
        self.assertEqual(focus[0].title, "暂无可核实的重要消息")

    def test_past_distant_or_undated_event_becomes_observation(self):
        for at in (NOW, NOW - timedelta(hours=1), NOW + timedelta(days=4), NOW.replace(tzinfo=None)):
            with self.subTest(at=at):
                focus = build_focus([], NOW, market="ashare", event=CalendarEvent("非农就业", at, "BLS"), watch="关注上涨范围能否扩大。")
                self.assertEqual(focus[1].label, "继续观察")
                self.assertEqual(focus[1].title, "关注上涨范围能否扩大。")
                self.assertEqual(focus[1].stamp, "")

    def test_event_display_converts_to_new_york_date(self):
        focus = build_focus([], NOW, market="usstock", event=CalendarEvent("美国非农就业", datetime(2026, 10, 2, 20, 30, tzinfo=CST), "BLS"))
        self.assertIn("计划 10-02 08:30 EDT", focus[1].stamp)

    def test_naive_news_time_uses_chinese_source_convention(self):
        news = [NewsItem(datetime(2026, 10, 1, 13), "美联储官员讨论利率预期", "见闻")]
        focus = build_focus(news, NOW, market="usstock")
        self.assertIn("10-01 01:00 EDT", focus[0].stamp)

    def test_invalid_market_is_rejected(self):
        with self.assertRaises(ValueError):
            build_focus([], NOW, market="unknown")

    def test_broad_market_driver_precedes_an_ordinary_price_flash(self):
        news = [NewsItem(NOW - timedelta(minutes=2), "美光科技美股盘前转跌，现跌0.4%", "见闻", rank=24),
                NewsItem(NOW - timedelta(hours=1), "美股股指期货涨幅收窄，布伦特原油突破100美元", "见闻", rank=34)]
        focus = build_focus(news, NOW, market="usstock")
        self.assertIn("布伦特", focus[0].title)
        self.assertIn("通胀和行业成本", focus[0].context)

    def test_compact_indicator_label_preserves_condition_and_quotation(self):
        title = '美联储卡什卡利：即使PCE数据低于预期，通胀“仍然过高”'
        news = [NewsItem(NOW, title, "见闻")]
        focus = build_focus(news, NOW, market="usstock")
        self.assertEqual(focus[0].title, title.replace("PCE数据", "PCE"))
        self.assertIn("即使", focus[0].title)
        self.assertIn('“仍然过高”', focus[0].title)
        self.assertEqual(news[0].title, title)


if __name__ == "__main__":
    unittest.main()
