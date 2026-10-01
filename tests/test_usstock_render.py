from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from common.news import NewsItem
from usstock import render
from usstock.models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, NY, SECTOR_NAMES, Brief, Quote,
)


class AuditedCanvas(render.Canvas):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.cards = []

    def text(self, *args, **kwargs):
        bounds = super().text(*args, **kwargs)
        self.texts.append((args[2], bounds))
        return bounds

    def card(self, x, y, width, height, *args, **kwargs):
        super().card(x, y, width, height, *args, **kwargs)
        self.cards.append((x, y, x + width, y + height))


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def briefing(kind="postmarket"):
    day = date(2026, 9, 30)
    clock = datetime(2026, 9, 30, 17, 40, tzinfo=NY)
    closing = datetime(2026, 9, 30, 16, 0, tzinfo=NY)
    before = datetime(2026, 9, 30, 8, 40, tzinfo=NY)
    overnight = datetime(2026, 9, 29, 21, 24, tzinfo=NY)

    def quotes(names, last=150, session="regular", asof=closing):
        return [Quote(symbol, name, last + i * 10, pct=1.23 - i * .4, asof=asof,
                      session=session, source="BOATS Real Time Price" if session == "overnight" else "Yahoo Finance", unit="points" if symbol.startswith("^") or symbol.endswith("=F") else "USD")
                for i, (symbol, name) in enumerate(names.items())]

    indices = quotes(INDEX_NAMES, last=6500)
    stocks = quotes(MEGA_NAMES, session="premarket" if kind == "premarket" else "regular",
                    asof=before if kind == "premarket" else closing)
    reference = quotes(MACRO_NAMES)
    reference[1].last, reference[1].unit = 4.25, "%"
    reference[3].last, reference[3].unit = 4186.6, "USD/盎司"
    reference[4].last, reference[4].unit = 89.83, "USD/桶"
    activity = Quote("SPY", "标普500ETF", 680, pct=.7, asof=closing, volume=83e6,
                     previous_volume=75e6, previous_date=date(2026, 9, 29))
    return Brief(kind=kind, generated_at=before if kind == "premarket" else clock,
                 edition_date=day, reference_date=date(2026, 9, 29) if kind == "premarket" else day, indices=indices,
                 futures=quotes(FUTURE_NAMES, 6500, "reference", before), stocks=stocks,
                 sectors=quotes(SECTOR_NAMES, session="overnight" if kind == "premarket" else "regular",
                                asof=overnight if kind == "premarket" else closing), references=reference, activity=activity,
                 news=[NewsItem(datetime(2026, 9, 30, 15, 40, tzinfo=NY), title, "见闻")
                       for title in ("美国核心PCE数据公布，市场关注通胀与就业变化", "美联储官员讨论货币政策与经济增长", "科技企业发布季度业绩报告")],
                 headline="美股主要指数涨跌互现", sentiment="平淡",
                 market_summary="市场情绪平淡，夜盘/盘前活跃度待确认。" if kind == "premarket" else "市场情绪平淡，SPY基本平量；成交量按股数统计。",
                 stocks_label="盘前行情" if kind == "premarket" else "收盘行情", complete=kind == "postmarket")


class USRenderTests(unittest.TestCase):
    def draw_brief(self, brief):
        canvas = AuditedCanvas()
        with tempfile.TemporaryDirectory() as folder, patch.object(render, "Canvas", return_value=canvas):
            path = render.render_png(brief, Path(folder) / "nested" / "poster.png")
            with Image.open(path) as picture:
                self.assertEqual(picture.size, (1080, 1620))
                self.assertEqual(picture.getpixel((0, 1000)), render.BG)
        self.assert_layout(canvas)
        return canvas

    def assert_layout(self, canvas):
        for value, bounds in canvas.texts:
            self.assertGreaterEqual(bounds[0], 0, value)
            self.assertGreaterEqual(bounds[1], 0, value)
            self.assertLessEqual(bounds[2], render.WIDTH, value)
            self.assertLessEqual(bounds[3], render.HEIGHT, value)
            for card in canvas.cards:
                if intersects(bounds, card):
                    for actual in (bounds[0] - card[0], bounds[1] - card[1], card[2] - bounds[2], card[3] - bounds[3]):
                        self.assertGreaterEqual(actual, 8, (value, bounds, card))
        for i, (value, bounds) in enumerate(canvas.texts):
            for other, other_bounds in canvas.texts[i + 1:]:
                self.assertFalse(intersects(bounds, other_bounds), (value, bounds, other, other_bounds))

    def test_complete_editions_contain_core_information_and_safe_bounds(self):
        for kind in ("premarket", "postmarket"):
            with self.subTest(kind=kind):
                canvas = self.draw_brief(briefing(kind))
                text = "\n".join(value for value, _ in canvas.texts)
                self.assertIn("2026.09.30 星期三", text)
                self.assertIn("美股盘前精选" if kind == "premarket" else "美股盘后精选", text)
                if kind == "postmarket":
                    self.assertIn("8300万股", text)
                    self.assertIn("较09.29增加800万股", text)
                else:
                    self.assertIn("最新科技股", text)
                    self.assertIn("最新板块ETF", text)
                    self.assertIn("7 / 7家", text)
                    self.assertIn("09.29基准", text)
                    self.assertNotIn("8300万股", text)
                self.assertIn("VIX恐慌指数", text)
                self.assertIn("4.25%", text)
                self.assertIn("$4,186.60/盎司", text)
                self.assertIn("$89.83/桶", text)
                for symbol in MEGA_NAMES:
                    self.assertIn(symbol, text)
                for name in (FUTURE_NAMES if kind == "premarket" else INDEX_NAMES).values():
                    self.assertIn(name, text)
                self.assertNotIn("主力净", text)

    def test_snapshot_shows_capture_clock_without_claiming_trade_time(self):
        brief = briefing("premarket")
        brief.stocks = [Quote("AAPL", "苹果", 333.9, pct=.26, session="overnight", source="Webull",
                              observed_at=datetime(2026, 9, 29, 23, 30, tzinfo=NY), cached=True)]
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("夜盘快照", text)
        self.assertIn("采集23:30", text)
        self.assertIn("成交时间未披露", text)
        self.assertIn("缓存", text)
        self.assertIn("Webull", text)

    def test_missing_quotes_never_become_zero_or_dollars(self):
        for kind in ("premarket", "postmarket"):
            with self.subTest(kind=kind):
                canvas = self.draw_brief(Brief(kind, datetime(2026, 9, 30, 8, tzinfo=NY), date(2026, 9, 30)))
                text = "\n".join(value for value, _ in canvas.texts)
                if kind == "postmarket":
                    self.assertIn("SPY日线股数待确认", text)
                else:
                    self.assertNotIn("SPY日线股数", text)
                self.assertIn("时点待确认", text)
                self.assertNotIn("0股", text)
                self.assertNotIn("$0", text)

    def test_long_content_and_large_changes_remain_inside_cards(self):
        for kind in ("premarket", "postmarket"):
            brief = briefing(kind)
            brief.headline = "美股主要指数涨跌互现，市场关注经济数据以及货币政策变化"
            brief.sentiment = "平淡偏谨慎"
            for quote in brief.indices + brief.futures + brief.stocks + brief.sectors + brief.references:
                quote.pct = -123.45
                quote.last = 1000000.99
            for quote in brief.stocks + brief.sectors:
                quote.name = "金融与科技服务行业及企业长期发展"
            brief.extended_stocks = [Quote("AAPL", "苹果", 1000000.99, pct=123.45,
                                            asof=datetime(2026, 9, 30, 19, 58, tzinfo=NY), session="postmarket")]
            brief.news[0].title = "美联储官员表示未来将继续根据就业与通胀等经济数据判断利率路径，投资者关注多项宏观数据及企业盈利变化"
            brief.news[0].source = "新闻来源名称较长的测试媒体"
            with self.subTest(kind=kind):
                self.draw_brief(brief)

    def test_stock_rows_show_each_actual_session_and_time(self):
        brief = briefing("premarket")
        brief.stocks[0].session = "overnight"
        brief.stocks[0].asof = datetime(2026, 9, 29, 21, 24, tzinfo=NY)
        brief.stocks[1].session = "postmarket"
        brief.stocks[1].asof = datetime(2026, 9, 29, 18, 30, tzinfo=NY)
        brief.references[0].asof = None
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("夜盘09.29 21:24", text)
        self.assertIn("盘后09.29 18:30", text)
        self.assertIn("盘前09.30 08:40", text)
        self.assertIn("时点待确认", text)

    def test_latest_overnight_prices_and_etf_times_are_not_previous_close(self):
        brief = briefing("premarket")
        brief.stocks[0] = Quote("AAPL", "苹果", 334.03, pct=.77,
                                asof=datetime(2026, 9, 29, 21, 24, tzinfo=NY), session="overnight")
        brief.stocks[1] = Quote("MSFT", "微软", 999.88, pct=0,
                                asof=datetime(2026, 9, 29, 16, 0, tzinfo=NY), session="regular")
        brief.sectors[0].asof = datetime(2026, 9, 29, 21, 24, tzinfo=NY)
        brief.sectors[1].asof = datetime(2026, 9, 29, 23, 30, tzinfo=NY)
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("$334.03", text)
        self.assertIn("+0.77%", text)
        self.assertIn("6 / 7家", text)
        self.assertNotIn("$999.88", text)
        self.assertNotIn("前收", text)
        stamps = [value for value, bounds in canvas.texts if 838 < bounds[1] < 1108]
        self.assertIn("夜盘09.29 21:24", stamps)
        self.assertIn("夜盘09.29 23:30", stamps)

    def test_premarket_regular_only_data_stays_unavailable(self):
        brief = briefing("premarket")
        for quote in brief.stocks + brief.sectors:
            quote.session = "regular"
            quote.pct = 0
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("0 / 7家", text)
        self.assertNotIn("前收", text)
        self.assertNotIn("0.00%", text)
        self.assertNotIn("SPY日线股数", text)

    def test_partial_latest_etfs_do_not_repeat_in_both_rankings(self):
        for count in range(1, 5):
            brief = briefing("premarket")
            brief.sectors = brief.sectors[:count]
            with self.subTest(count=count):
                canvas = self.draw_brief(brief)
                rows = [value for value, bounds in canvas.texts if 838 < bounds[1] < 1108]
                for quote in brief.sectors:
                    self.assertEqual(rows.count(f"{quote.name} {quote.symbol}"), 1)
                self.assertTrue(any(value == "—" for value in rows))
                self.assertTrue(any("BOATS" in value for value, _ in canvas.texts))

    def test_futures_keep_their_session_when_edition_is_next_day(self):
        brief = briefing("premarket")
        brief.edition_date = date(2026, 10, 1)
        for quote in brief.futures:
            quote.session = "futures"
            quote.asof = datetime(2026, 9, 30, 18, 0, tzinfo=NY)
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("期货09.30 18:00", text)
        self.assertNotIn("前收09.30 18:00", text)

    def test_afterhours_detail_preserves_regular_close_percentage(self):
        brief = briefing("postmarket")
        brief.stocks[0].pct = -2.34
        brief.extended_stocks = [Quote("AAPL", "苹果", 334.03, pct=.30,
                                        asof=datetime(2026, 9, 30, 19, 55, tzinfo=NY), session="postmarket")]
        canvas = self.draw_brief(brief)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("-2.34%", text)
        self.assertIn("盘后$334.03 +0.30%", text)
        self.assertIn("09.30 19:55", text)
        self.assertEqual(text.count("盘后$334.03 +0.30%"), 1)
        brief.kind = "premarket"
        canvas = self.draw_brief(brief)
        self.assertFalse(any(value.startswith("盘后$") for value, _ in canvas.texts))

    def test_generation_and_news_use_real_dst_timezone(self):
        for utc, expected in ((datetime(2026, 9, 30, 21, 40, tzinfo=timezone.utc), "生成09.30 17:40 EDT"),
                              (datetime(2026, 12, 1, 22, 40, tzinfo=timezone.utc), "生成12.01 17:40 EST")):
            brief = briefing()
            brief.generated_at = utc
            brief.news[0].published = utc
            canvas = self.draw_brief(brief)
            self.assertIn(expected, [value for value, _ in canvas.texts])
            self.assertTrue(any(expected.removeprefix("生成") in value for value, _ in canvas.texts))

    def test_volume_comparison_requires_earlier_date_and_valid_numbers(self):
        brief = briefing()
        brief.activity.previous_date = brief.activity.trade_date
        self.assertNotIn("较", render._activity(brief))
        brief.activity.previous_date = date(2026, 10, 1)
        self.assertNotIn("较", render._activity(brief))
        brief.activity.volume = float("nan")
        self.assertIn("待确认", render._activity(brief))


if __name__ == "__main__":
    unittest.main()
