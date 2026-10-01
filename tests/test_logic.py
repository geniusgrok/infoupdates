from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from a_share_brief.compose import build_brief
from a_share_brief.fetch import MarketData
from a_share_brief.models import Breadth, CapitalMix, CrossBorder, NewsItem, Quote, TurnoverComparison
from a_share_brief.narrative import market_summary
from a_share_brief.rank import select_news
from a_share_brief.social import social_copy

CST = timezone(timedelta(hours=8))


def market(day: str = "2026-09-30", session: str = "15:30:00") -> MarketData:
    return MarketData(
        indices=[
            Quote("sh000001", "上证指数", 3842.19, pct=0.31, amount=679.4e9, trade_day=day, session=session),
            Quote("sz399001", "深证成指", 12887.62, pct=-0.11, amount=758.6e9, trade_day=day, session=session),
        ],
        breadth=Breadth(2470, 2747, 147, 52, 9),
    )


class SessionTests(unittest.TestCase):
    def test_morning_does_not_switch_at_noon(self) -> None:
        now = datetime(2026, 9, 30, 12, 30, tzinfo=CST)
        brief = build_brief("morning", market("2026-09-29"), now)
        self.assertEqual(brief.edition_date(), date(2026, 9, 30))
        self.assertFalse(brief.preview)

    def test_next_morning_skips_national_holiday(self) -> None:
        now = datetime(2026, 9, 30, 20, tzinfo=CST)
        brief = build_brief("morning", market(), now)
        self.assertEqual(brief.edition_date(), date(2026, 10, 8))
        self.assertEqual(brief.session_label(), "9月30日")
        holiday = build_brief("morning", market(), now.replace(month=10, day=1, hour=8))
        self.assertEqual(holiday.edition_date(), date(2026, 10, 8))
        self.assertTrue(holiday.preview)

    def test_weekends_and_makeup_workdays_are_not_sessions(self) -> None:
        for day, expected in (("2026-09-18", date(2026, 9, 21)), ("2026-02-14", date(2026, 2, 24))):
            now = datetime.fromisoformat(day + "T20:00:00").replace(tzinfo=CST)
            brief = build_brief("morning", market(day), now)
            self.assertEqual(brief.edition_date(), expected)

    def test_now_is_normalized_to_china_time(self) -> None:
        now = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)
        brief = build_brief("morning", market("2026-09-29"), now)
        self.assertEqual(brief.generated_at.hour, 14)
        self.assertEqual(brief.generated_at.utcoffset(), timedelta(hours=8))
        self.assertEqual(brief.edition_date(), date(2026, 9, 30))

    def test_intraday_quote_is_not_described_as_close(self) -> None:
        now = datetime(2026, 9, 30, 12, 30, tzinfo=CST)
        brief = build_brief("close", market(session="11:30:00"), now)
        self.assertTrue(brief.is_intraday)
        self.assertNotIn("上证收于", brief.narrative.summary)
        self.assertIn("盘中", social_copy(brief))

    def test_unknown_calendar_does_not_block_close(self) -> None:
        now = datetime(2027, 9, 30, 20, tzinfo=CST)
        brief = build_brief("close", market("2027-09-30"), now)
        self.assertEqual(brief.edition_date(), now.date())
        self.assertTrue(any("交易日历" in note for note in brief.notes))
        with self.assertRaisesRegex(ValueError, "交易日历"):
            build_brief("morning", market("2027-09-30"), now).edition_date()

    def test_malformed_date_degrades_without_crash(self) -> None:
        brief = build_brief("close", market("not-a-date"), datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertEqual(brief.hero.last, 0)
        self.assertTrue(any("日期" in note for note in brief.notes))
        self.assertNotIn("上证指数  3,842.19", social_copy(brief))

    def test_previous_day_incomplete_quote_is_not_called_close(self) -> None:
        brief = build_brief("morning", market("2026-09-29", "11:30:00"), datetime(2026, 9, 30, 8, tzinfo=CST))
        self.assertTrue(brief.is_intraday)
        self.assertNotIn("9月29日收盘", social_copy(brief))

    def test_future_source_clock_cannot_override_current_intraday_time(self) -> None:
        data = market(session="15:35:00")
        data.turnover_comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 1100e8, 1000e8)
        brief = build_brief("close", data, datetime(2026, 9, 30, 9, 30, tzinfo=CST))
        self.assertTrue(brief.is_intraday)
        self.assertIsNone(brief.turnover_comparison)
        self.assertNotIn("上证收于", brief.narrative.summary)

    def test_market_summary_rejects_manually_attached_intraday_comparison(self) -> None:
        brief = build_brief("close", market(session="11:30:00"), datetime(2026, 9, 30, 12, tzinfo=CST))
        brief.turnover_comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 100e8, 1000e8)
        self.assertIn("量能待确认", market_summary(brief))
        self.assertNotIn("缩量", market_summary(brief))

    def test_future_quote_is_not_displayed_as_current(self) -> None:
        brief = build_brief("close", market("2026-10-08"), datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertEqual(brief.hero.last, 0)
        self.assertNotIn("上证指数  3,842.19", social_copy(brief))


class AmountAndNarrativeTests(unittest.TestCase):
    def test_weight_style_cites_the_growth_index_that_triggered_it(self) -> None:
        data = market()
        data.indices[0].pct = 0.2
        data.indices.extend([
            Quote("sh000016", "上证50", 2800, pct=0.5),
            Quote("sh000688", "科创50", 1500, pct=1.0),
            Quote("sz399006", "创业板指", 3100, pct=-2.0),
        ])
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertEqual(brief.narrative.style, "权重护盘")
        self.assertIn("上证50 +0.50%、创业板指 -2.00%", brief.narrative.summary)
        self.assertNotIn("科创50 +1.00%，权重强于成长", brief.narrative.summary)

    def test_growth_style_cites_chinext_when_star_is_missing(self) -> None:
        data = market()
        data.indices[0].pct = 0.2
        data.indices.append(Quote("sz399006", "创业板指", 3100, pct=1.5))
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertEqual(brief.narrative.style, "成长占优")
        self.assertIn("创业板指 +1.50%，成长强于权重", brief.narrative.summary)

    def test_valid_turnover_comparison_across_national_holiday(self) -> None:
        data = market("2026-10-08")
        data.turnover_comparison = TurnoverComparison(date(2026, 10, 8), date(2026, 9, 30), 1100e8, 1000e8)
        brief = build_brief("close", data, datetime(2026, 10, 8, 20, tzinfo=CST))
        self.assertEqual(brief.turnover_comparison, data.turnover_comparison)
        self.assertIn("放量", market_summary(brief))
        self.assertIn("增加100.0亿元", market_summary(brief))

    def test_duplicate_indices_do_not_inflate_turnover(self) -> None:
        data = market()
        data.indices.append(data.indices[0])
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertAlmostEqual(brief.turnover, 1438e9)

    def test_different_date_amounts_are_not_summed(self) -> None:
        data = market()
        data.indices[1].trade_day = "2026-09-29"
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertIsNone(brief.turnover)

    def test_one_market_cannot_be_called_shanghai_shenzhen_total(self) -> None:
        data = market()
        data.capital = [CapitalMix("沪市", -10e8, -4e8, -6e8, 1e8, 9e8)]
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertIsNone(brief.main_net)
        self.assertNotIn("沪深主力", social_copy(brief))
        self.assertIn("沪市主力", social_copy(brief))

    def test_duplicate_market_capital_does_not_make_a_total(self) -> None:
        data = market()
        sh = CapitalMix("沪市", -10e8, -4e8, -6e8, 1e8, 9e8)
        data.capital = [sh, sh]
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertIsNone(brief.main_net)

    def test_market_summary_uses_amount_delta_in_yuan(self) -> None:
        data = market()
        data.turnover_comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 679.4e9, 661.71e9)
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        line = market_summary(brief)
        self.assertIn("平量", line)
        self.assertIn("增加176.9亿元", line)
        self.assertNotIn("%", line)
        self.assertNotIn("亿股", line)

    def test_incomplete_or_stale_comparison_is_not_shrinkage(self) -> None:
        for now, comp in (
            (datetime(2026, 9, 30, 12, tzinfo=CST), TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 100e9, 600e9)),
            (datetime(2026, 9, 30, 20, tzinfo=CST), TurnoverComparison(date(2026, 9, 29), date(2026, 9, 28), 100e9, 600e9)),
            (datetime(2026, 9, 30, 20, tzinfo=CST), TurnoverComparison(date(2026, 9, 30), date(2026, 9, 28), 100e9, 600e9)),
        ):
            data = market(session="11:30:00" if now.hour == 12 else "15:30:00")
            data.turnover_comparison = comp
            brief = build_brief("close", data, now)
            self.assertIsNone(brief.turnover_comparison)
            self.assertIn("量能待确认", market_summary(brief))
            self.assertNotIn("缩量", market_summary(brief))

    def test_missing_breadth_does_not_assert_sentiment(self) -> None:
        data = market()
        data.breadth = None
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertIn("市场情绪待确认", market_summary(brief))
        self.assertEqual(brief.narrative.sentiment, "待确认")

    def test_negative_flow_has_no_double_negative(self) -> None:
        from a_share_brief.models import SectorFlow
        data = market()
        data.sector_out = [SectorFlow("1", "半导体", -100e8)]
        brief = build_brief("morning", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertIn("净流出100.0亿", " ".join(brief.narrative.watch))
        self.assertNotIn("净流出-", " ".join(brief.narrative.watch))

    def test_stale_capital_and_cross_border_are_not_displayed(self) -> None:
        data = market()
        data.capital = [CapitalMix("沪市", -10e8, -4e8, -6e8, 1e8, 9e8, trade_day="2026-09-29")]
        data.cross = CrossBorder(south_net=10e8, trade_day="2026-09-29")
        brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
        self.assertEqual(brief.capital, [])
        self.assertIsNone(brief.cross)

    def test_volume_thresholds_and_delta_direction(self) -> None:
        for current, expected, delta in ((1000e8, "基本平量", "基本持平"), (1050e8, "基本平量", "增加50.0亿元"),
                                         (1060e8, "放量", "增加60.0亿元"), (940e8, "缩量", "减少60.0亿元")):
            data = market()
            data.turnover_comparison = TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), current, 1000e8)
            brief = build_brief("close", data, datetime(2026, 9, 30, 20, tzinfo=CST))
            self.assertIn(expected, market_summary(brief))
            self.assertIn(delta, market_summary(brief))


class NewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 30, 9, tzinfo=CST)

    def select(self, items: list[NewsItem], limit: int = 7, kind: str = "morning") -> list[NewsItem]:
        return select_news(items, kind=kind, trade_date=date(2026, 9, 29), now=self.now, limit=limit)

    def test_future_items_are_excluded(self) -> None:
        future = NewsItem(self.now + timedelta(hours=1), "美联储宣布最新利率决议安排", "见闻", 5)
        self.assertEqual(self.select([future]), [])

    def test_naive_item_timestamps_are_china_local(self) -> None:
        item = NewsItem(self.now.replace(tzinfo=None), "央行今日开展公开市场逆回购操作", "东财", 2)
        chosen = self.select([item])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].published.utcoffset(), timedelta(hours=8))

    def test_zero_and_negative_limits_return_empty(self) -> None:
        item = NewsItem(self.now, "央行今日开展公开市场逆回购操作", "东财", 2)
        self.assertEqual(self.select([item], limit=0), [])
        self.assertEqual(self.select([item], limit=-1), [])

    def test_high_score_company_noise_is_excluded(self) -> None:
        item = NewsItem(self.now, "某公司董事长辞职并计划减持公司股份", "见闻", 20)
        self.assertEqual(self.select([item]), [])

    def test_policy_about_reductions_is_kept(self) -> None:
        item = NewsItem(self.now, "证监会进一步规范大股东减持行为", "见闻", 2)
        self.assertEqual(len(self.select([item])), 1)

    def test_same_headline_prefix_does_not_remove_distinct_news(self) -> None:
        items = [
            NewsItem(self.now, "央行货币政策委员会会议指出，持续支持实体经济高质量发展", "见闻", 2),
            NewsItem(self.now, "央行货币政策委员会会议指出，人民币汇率应保持合理均衡水平", "见闻", 2),
        ]
        self.assertEqual(len(self.select(items)), 2)

    def test_similar_wording_for_distinct_entities_is_not_duplicate(self) -> None:
        pairs = (
            ("美国第二季度GDP年化季环比终值增长3.8%，高于预期", "美国第二季度PCE年化季环比终值增长3.8%，高于预期"),
            ("美国第二季度GDP年化季环比终值增长3.8%，高于预期", "英国第二季度GDP年化季环比终值增长3.8%，高于预期"),
            ("日本央行宣布维持基准利率不变，符合市场预期", "韩国央行宣布维持基准利率不变，符合市场预期"),
        )
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                items = [NewsItem(self.now, title, "见闻", 2) for title in (left, right)]
                self.assertEqual(len(self.select(items)), 2)

    def test_exact_and_full_title_containment_are_duplicates(self) -> None:
        title = "美国第二季度GDP年化季环比终值增长3.8%，高于预期"
        items = [NewsItem(self.now, headline, "见闻", 2) for headline in (title, title, "最新数据：" + title)]
        self.assertEqual(len(self.select(items)), 1)

    def test_intraday_morning_can_include_last_night(self) -> None:
        item = NewsItem(self.now - timedelta(hours=12), "美联储最新会议纪要受到全球市场关注", "见闻", 2)
        self.assertEqual(len(select_news([item], kind="morning", trade_date=self.now.date(), now=self.now)), 1)

    def test_utc_now_has_same_news_window(self) -> None:
        item = NewsItem(self.now.replace(hour=8), "央行今日开展公开市场逆回购操作", "东财", 2)
        chosen = select_news([item], kind="morning", trade_date=self.now.date(), now=self.now.astimezone(timezone.utc))
        self.assertEqual(len(chosen), 1)


if __name__ == "__main__":
    unittest.main()
