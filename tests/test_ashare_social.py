from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from ashare.compose import build_brief
from ashare.models import CST, CapitalMix, MarketData, NewsItem, Quote, SectorMove, TurnoverComparison
from ashare.narrative import focus_items, headline
from ashare.social import social_copy
from common.events import CalendarEvent


def market() -> MarketData:
    return MarketData(
        indices=[Quote(str(i), name, 2000, pct=pct, trade_day="2026-09-30", session="15:30:00")
                 for i, (name, pct) in enumerate((("上证指数", .3), ("深证成指", -.1),
                                                 ("创业板指", -.2), ("科创50", -2), ("中证1000", -.4)))],
        overseas=[Quote(str(i), name, 2000, pct=pct, session="09-30 收盘")
                  for i, (name, pct) in enumerate((("道琼斯", -.8), ("纳斯达克", .2), ("标普500", -.3),
                                                  ("日经225", 3)))],
        sectors_up=[SectorMove("生物制药", 2), SectorMove("酿酒行业", 1), SectorMove("钢铁行业", .5)],
        sectors_down=[SectorMove("电子器件", -2), SectorMove("印刷包装", -1), SectorMove("纺织行业", -.5)],
        capital=[CapitalMix("沪市", -10e8, 0, 0, 0, 0), CapitalMix("深市", -20e8, 0, 0, 0, 0)],
        turnover_comparison=TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 679.4e9, 661.71e9),
    )


class SocialSummaryTests(unittest.TestCase):
    def build(self, kind="close", data=None, now=None):
        return build_brief(kind, data or market(), now or datetime(2026, 10, 1, 18, tzinfo=CST))

    def test_summary_selects_image_facts_without_full_rankings(self):
        brief = self.build()
        text = social_copy(brief)
        self.assertIn("A股收盘精选｜2026年9月30日 星期三", text)
        self.assertIn(headline(brief), text)
        self.assertIn("增加176.9亿元", text)
        self.assertIn("生物制药", text)
        self.assertIn("电子器件", text)
        self.assertNotIn("中证1000", text)
        self.assertNotIn("钢铁行业", text)
        self.assertNotIn("纺织行业", text)
        self.assertLessEqual(len(text), 650)
        self.assertTrue(text.rstrip().endswith("不构成投资建议。"))

    def test_text_and_image_select_same_news_event_and_observation(self):
        data = market()
        now = datetime(2026, 10, 1, 18, tzinfo=CST)
        data.news = [NewsItem(now - timedelta(hours=2), "美联储维持利率不变", "东财", 1),
                     NewsItem(now - timedelta(hours=1), "某公司发布三季度业绩", "见闻", 1)]
        for event in (None, CalendarEvent("美国非农就业与失业率", now + timedelta(days=1), "美国劳工统计局")):
            with self.subTest(event=event):
                brief = self.build(data=data, now=now)
                brief.event = event
                text = social_copy(brief)
                for item in focus_items(brief):
                    self.assertIn(item.title, text)
                    if item.stamp:
                        self.assertIn(item.stamp, text)
                self.assertNotIn("某公司发布三季度业绩", text)

    def test_holiday_morning_has_current_foreign_clock_and_a_reference_date(self):
        brief = self.build("morning")
        brief.event = CalendarEvent("美国非农就业与失业率", datetime(2026, 10, 2, 20, 30, tzinfo=CST), "美国劳工统计局")
        text = social_copy(brief)
        self.assertIn("A股早盘精选｜2026年10月8日 星期四", text)
        self.assertIn("下次交易10月8日", text)
        self.assertIn("A股参考9月30日收盘", text)
        self.assertIn("09.30 收盘", text)
        self.assertIn("假期关注", text)
        self.assertNotIn("昨日", text)

    def test_missing_data_is_not_zero_or_a_completed_close(self):
        text = social_copy(self.build(data=MarketData(indices=[])))
        self.assertIn("数据暂缺", text)
        self.assertIn("待确认", text)
        self.assertNotIn("0.00%", text)
        self.assertNotIn("主力+0", text)

    def test_single_market_funds_keep_single_market_label(self):
        data = market()
        data.capital = data.capital[:1]
        text = social_copy(self.build(data=data))
        self.assertIn("沪市主力-10.0亿元", text)
        self.assertNotIn("沪深主力", text)

    def test_intraday_and_stale_data_limits_are_retained(self):
        data = market()
        data.indices[0].session = "11:30:00"
        data.capital[0].trade_day = "2026-09-29"
        text = social_copy(self.build(data=data, now=datetime(2026, 9, 30, 12, tzinfo=CST)))
        self.assertIn("盘中快照", text)
        self.assertIn("盘中成交额尚未完整", text)
        self.assertIn("日期不一致的数据已跳过", text)

    def test_news_numbers_and_attribution_are_not_truncated(self):
        data = market()
        now = datetime(2026, 10, 1, 18, tzinfo=CST)
        title = "CME FedWatch：美联储10月加息概率较一日前降13.8个百分点 年内至少再加息一次概率降至86.8%"
        data.news = [NewsItem(now - timedelta(hours=1), title, "东财", 2)]
        text = social_copy(self.build(data=data, now=now))
        self.assertIn(title, text)
        self.assertNotIn("…", text)


if __name__ == "__main__":
    unittest.main()
