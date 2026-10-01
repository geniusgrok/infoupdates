from __future__ import annotations

import unittest
from datetime import date, datetime

from common.events import CalendarEvent
from common.news import NewsItem
from usstock.models import FUTURE_NAMES, INDEX_NAMES, MEGA_NAMES, NY, SECTOR_NAMES, Brief, Quote
from usstock.social import social_copy


def briefing(kind="premarket"):
    day, previous = date(2026, 10, 1), date(2026, 9, 30)
    now = datetime(2026, 10, 1, 8, tzinfo=NY)
    before = datetime(2026, 10, 1, 7, 59, tzinfo=NY)
    close = datetime(2026, 9, 30, 16, tzinfo=NY)
    phase, clock = ("premarket", before) if kind == "premarket" else ("regular", close)
    stocks = [Quote(symbol, name, 100 + i, pct=value, asof=clock, session=phase)
              for i, ((symbol, name), value) in enumerate(zip(MEGA_NAMES.items(), [2, .2, .01, .5, .3, -.1, -3]))]
    sectors = [Quote(symbol, name, 100, pct=1 - i / 2, asof=clock, session=phase)
               for i, (symbol, name) in enumerate(SECTOR_NAMES.items())]
    indices = [Quote(symbol, name, 6000, pct=.2, asof=close) for symbol, name in INDEX_NAMES.items()]
    futures = [Quote(symbol, name, 6000, pct=.4, asof=before, session="futures")
               for symbol, name in FUTURE_NAMES.items()]
    news = [NewsItem(datetime(2026, 10, 1, 7, 30, tzinfo=NY), "英伟达NVDA发布新的季度业绩指引", "公开快讯")]
    return Brief(kind, now, day if kind == "premarket" else previous, previous,
                 indices=indices if kind == "postmarket" else [], futures=futures,
                 stocks=stocks, sectors=sectors, news=news, headline="股指涨跌分化",
                 market_summary="盘前情绪平淡，量能待开盘确认。" if kind == "premarket" else "情绪平淡 · SPY股数代理待确认",
                 event=CalendarEvent("美国非农就业与失业率", datetime(2026, 10, 2, 8, 30, tzinfo=NY), "美国劳工统计局"))


class USSocialTests(unittest.TestCase):
    def test_summary_keeps_three_image_stocks_and_two_focus_items(self):
        text = social_copy(briefing())
        self.assertIn("2026年10月1日 星期四（美东日期）", text)
        for symbol in ("NVDA", "TSLA", "AAPL"):
            self.assertIn(f"({symbol})", text)
        for symbol in ("MSFT", "AMZN", "GOOGL", "META"):
            self.assertNotIn(f"({symbol})", text)
        self.assertIn("科技 +1.00%", text)
        self.assertIn("通信服务 -4.00%", text)
        self.assertNotIn("金融 +0.50%", text)
        self.assertEqual(text.count("关键消息："), 1)
        self.assertEqual(text.count("下一事件："), 1)
        self.assertIn("计划 10-02 08:30", text)
        self.assertLessEqual(len(text.splitlines()), 10)

    def test_mixed_stages_keep_dates_and_do_not_merge_post_reference_into_premarket(self):
        brief = briefing()
        brief.stocks = [Quote("AAPL", "苹果", 100, 1, asof=datetime(2026, 9, 30, 23, 59, tzinfo=NY), session="overnight"),
                        Quote("NVDA", "英伟达", 200, 2, asof=datetime(2026, 10, 1, 7, 59, tzinfo=NY), session="premarket")]
        text = social_copy(brief)
        self.assertIn("夜盘 09-30 23:59 ET", text)
        self.assertIn("盘前 10-01 07:59 ET", text)
        self.assertIn("比较基准日期：2026-09-30收盘", text)

    def test_unknown_night_trade_time_is_never_changed_to_observed_trade_time(self):
        brief = briefing()
        brief.stocks = [Quote("AAPL", "苹果", 100, 1, session="overnight", source="Webull",
                              observed_at=datetime(2026, 9, 30, 23, 59, tzinfo=NY), cached=True, delay_minutes=15)]
        text = social_copy(brief)
        self.assertIn("夜盘快照·采集09-30 23:59 ET·成交时间未披露·缓存·延迟15分钟", text)
        self.assertIn("Webull", text)

    def test_quote_times_are_a_range_and_cache_status_remains_specific(self):
        brief = briefing()
        brief.stocks[0].asof = datetime(2026, 10, 1, 7, 58, tzinfo=NY)
        text = social_copy(brief)
        self.assertIn("盘前 10-01 07:58–07:59 ET", text)
        brief.stocks[0].cached = True
        text = social_copy(brief)
        self.assertIn("苹果(AAPL) +2.00%（盘前 10-01 07:58 ET·缓存）", text)
        self.assertIn("特斯拉(TSLA) -3.00%（盘前 10-01 07:59 ET）", text)

    def test_postmarket_regular_and_extended_changes_have_separate_bases(self):
        brief = briefing("postmarket")
        brief.extended_stocks = [Quote("AAPL", "苹果", 102.5, .3,
                                      asof=datetime(2026, 9, 30, 19, 55, tzinfo=NY), session="postmarket")]
        text = social_copy(brief)
        self.assertIn("苹果(AAPL) +2.00%", text)
        self.assertIn("常规收盘 09-30 16:00 ET", text)
        self.assertIn("盘后延长交易（相对当日常规收盘）", text)
        self.assertIn("苹果(AAPL) $102.50 +0.30%", text)
        self.assertNotIn("苹果(AAPL) +0.30%", text)
        self.assertIn("SPY为供应商披露日线股数代理，非全市场成交额", text)

    def test_only_biggest_selected_postmarket_move_is_summarized(self):
        brief = briefing("postmarket")
        clock = datetime(2026, 9, 30, 19, 55, tzinfo=NY)
        brief.extended_stocks = [Quote("AAPL", "苹果", 105, 3, asof=clock, session="postmarket"),
                                 Quote("NVDA", "英伟达", 96, -4, asof=clock, session="postmarket"),
                                 Quote("TSLA", "特斯拉", 100, None, asof=clock, session="postmarket")]
        text = social_copy(brief)
        self.assertIn("英伟达(NVDA) $96.00 -4.00%", text)
        self.assertNotIn("苹果(AAPL) $105.00", text)
        self.assertNotIn("特斯拉(TSLA) $100.00", text)
        brief.extended_stocks = [brief.extended_stocks[2]]
        self.assertIn("特斯拉(TSLA) $100.00 —", social_copy(brief))

    def test_cached_daily_spy_metric_keeps_source_time_and_cache_label(self):
        brief = briefing("postmarket")
        brief.activity = Quote("SPY", "SPY日线股数", 600, asof=datetime(2026, 9, 30, 16, tzinfo=NY),
                               cached=True, delay_minutes=15)
        text = social_copy(brief)
        self.assertIn("SPY日线时点：常规收盘 09-30 16:00 ET·缓存·延迟15分钟", text)

    def test_stale_close_and_source_failures_are_summarized_with_real_date(self):
        brief = briefing("postmarket")
        brief.edition_date = date(2026, 10, 1)
        brief.notes = [f"供应商{i} AAPL暂缺：TimeoutError" for i in range(20)] + ["常规场行情日期不一致，已跳过其他日期的数据"]
        text = social_copy(brief)
        self.assertIn("收盘行情仅到2026-09-30", text)
        self.assertIn("日期不一致的行情已剔除", text)
        self.assertIn("部分数据源暂缺，采用可用报价", text)
        self.assertNotIn("TimeoutError", text)
        self.assertLessEqual(len(text.splitlines()), 10)

    def test_missing_event_keeps_same_image_watch_and_no_regular_close_fallback(self):
        brief = briefing()
        brief.event = None
        brief.stocks = []
        brief.sectors = []
        text = social_copy(brief)
        self.assertIn("继续观察：关注通胀、利率与科技业绩能否支持当前走势。", text)
        self.assertIn("重点个股：行情待确认", text)
        self.assertIn("最新个股0/7、ETF0/11可用", text)
        self.assertNotIn("道琼斯 +0.20%", text)
        self.assertNotIn("下一事件", text)
