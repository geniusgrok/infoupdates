from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from common.events import CalendarEvent
from common.render import HEIGHT, WIDTH

from ashare import render
from ashare.compose import build_brief
from ashare.models import MarketData
from ashare.models import Breadth, CapitalMix, CrossBorder, CST, NewsItem, Quote, SectorFlow, SectorMove, TurnoverComparison


class AuditedCanvas(render.Canvas):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.cards = []

    def text(self, *args, **kwargs):
        with patch.object(self.draw, "text", wraps=self.draw.text) as drawing:
            bounds = super().text(*args, **kwargs)
        self.texts.append((drawing.call_args.args[1], bounds))
        return bounds

    def card(self, x, y, width, height, *args, **kwargs):
        super().card(x, y, width, height, *args, **kwargs)
        self.cards.append((x, y, x + width, y + height))


def market():
    day = "2026-09-30"
    indices = [Quote(symbol, name, last, pct=pct, trade_day=day, session="15:35:28", amount=amount)
               for symbol, name, last, pct, amount in (
                   ("sh000001", "上证指数", 3842.19, .31, 679398960000),
                   ("sz399001", "深证成指", 12403.66, .12, 901000000000),
                   ("sz399006", "创业板指", 3135.28, -.23, None),
                   ("sh000688", "科创50", 1530.01, -2.51, None),
                   ("sh000300", "沪深300", 4433.65, .34, None),
                   ("sh000016", "上证50", 2823.28, .47, None),
                   ("sh000905", "中证500", 6323.40, -.12, None),
                   ("sh000852", "中证1000", 6598.33, -.31, None))]
    return MarketData(
        indices=indices,
        overseas=[Quote(str(i), name, 2000 + i * 500, pct=.32, trade_day=day) for i, name in enumerate(render.MORNING_ABROAD + ("恒生指数", "恒生科技"))],
        fx=[Quote("fx_susdcny", "在岸人民币", 6.7046)],
        capital=[CapitalMix("沪市", -50e8, 0, 0, 0, 0, day), CapitalMix("深市", -86.3e8, 0, 0, 0, 0, day)],
        sectors_up=[SectorMove(name, 2.35 - i * .4) for i, name in enumerate(("生物制药", "酿酒行业", "金融行业"))],
        sectors_down=[SectorMove(name, -1.8 - i * .3) for i, name in enumerate(("电子器件", "家用电器", "有色金属"))],
        sector_in=[SectorFlow(str(i), name, (2.5 - i * .3) * 1e8) for i, name in enumerate(("化学制药", "银行", "保险"))],
        sector_out=[SectorFlow(str(i), name, (-15.4 + i * .3) * 1e8) for i, name in enumerate(("半导体", "通信设备", "软件开发"))],
        breadth=Breadth(2332, 2609, 226, 50, 11),
        cross=CrossBorder(220e8, 49e8, 25e8, 24e8, day),
        turnover_comparison=TurnoverComparison(date(2026, 9, 30), date(2026, 9, 29), 679398960000, 661704280000),
    )


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class RenderRegressionTests(unittest.TestCase):
    def draw_brief(self, kind, data, now=None, event=None):
        now = now or datetime(2026, 9, 30, 20, 40, tzinfo=CST)
        brief = build_brief(kind, data, now=now)
        brief.event = event
        canvas = AuditedCanvas()
        with tempfile.TemporaryDirectory() as folder, patch.object(render, "Canvas", return_value=canvas):
            path = render.render_png(brief, Path(folder) / "nested" / "poster.png")
            with Image.open(path) as picture:
                self.assertEqual(picture.size, (1080, 1620))
                self.assertEqual(picture.getpixel((0, 1000)), render.BG)
        self.assert_layout(canvas)
        return brief, canvas

    def assert_layout(self, canvas):
        for value, bounds in canvas.texts:
            self.assertGreaterEqual(bounds[0], 0, value)
            self.assertGreaterEqual(bounds[1], 0, value)
            self.assertLessEqual(bounds[2], WIDTH, value)
            self.assertLessEqual(bounds[3], HEIGHT, value)
            for card in canvas.cards:
                if intersects(bounds, card):
                    for actual, edge in ((bounds[0] - card[0], 8), (bounds[1] - card[1], 8), (card[2] - bounds[2], 8), (card[3] - bounds[3], 8)):
                        self.assertGreaterEqual(actual, edge, (value, bounds, card))
        for i, (value, bounds) in enumerate(canvas.texts):
            for other, other_bounds in canvas.texts[i + 1:]:
                self.assertFalse(intersects(bounds, other_bounds), (value, bounds, other, other_bounds))

    def test_selected_layouts_have_safe_bounds_and_money_units(self):
        for kind in ("close", "morning"):
            with self.subTest(kind=kind):
                brief, canvas = self.draw_brief(kind, market())
                text = "\n".join(value for value, _ in canvas.texts)
                self.assertIn("成交额较上日增加176.9亿元", text)
                self.assertNotIn("亿股", text)
                self.assertIn("星期四" if kind == "morning" else "星期三", text)
                self.assertIn("2026.10.08" if kind == "morning" else "2026.09.30", text)

    def test_long_sectors_and_two_digit_foreign_changes_do_not_overlap(self):
        for kind in ("close", "morning"):
            data = market()
            for quote in data.overseas:
                quote.pct = -12.34
            data.sectors_up[0] = SectorMove("医疗器械服务及技术研发", 12.34)
            data.sectors_down[0] = SectorMove("计算机应用及软件技术服务", -15.89)
            data.sector_in[0] = SectorFlow("long", "医疗器械服务", 101.9e8)
            data.sector_out[0] = SectorFlow("long", "医疗器械服务", -101.9e8)
            data.news = [NewsItem(datetime(2026, 9, 30, 19, 40, tzinfo=CST), "美联储官员表示未来将继续根据就业与通胀等经济数据判断利率路径，投资者关注多项宏观数据及企业盈利变化", "见闻", 2)]
            with self.subTest(kind=kind):
                _, canvas = self.draw_brief(kind, data)
                self.assertTrue(any(value == "-101.9亿" for value, _ in canvas.texts) if kind == "close" else any(value == "美股三大指数下跌" for value, _ in canvas.texts))

    def test_missing_data_and_limit_counts_remain_unavailable(self):
        for kind in ("close", "morning"):
            _, canvas = self.draw_brief(kind, MarketData(indices=[]))
            text = "\n".join(value for value, _ in canvas.texts)
            self.assertIn("待确认", text)
            self.assertNotIn("涨停 0", text)
            self.assertNotIn("+0.0亿", text)
        data = market()
        data.breadth.limit_up = data.breadth.limit_down = None
        _, canvas = self.draw_brief("close", data)
        self.assertTrue(any("涨停 — · 跌停 —" in value for value, _ in canvas.texts))

    def test_sector_out_only_and_single_market_funds_are_visible(self):
        data = market()
        data.capital = data.capital[:1]
        data.sector_in = []
        _, canvas = self.draw_brief("close", data)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("沪市主力净额", text)
        self.assertNotIn("沪深主力净额", text)
        self.assertIn("-15.4亿", text)
        data.capital = []
        _, canvas = self.draw_brief("close", data)
        self.assertTrue(any(value == "-15.4亿" for value, _ in canvas.texts))

    def test_breadth_zero_and_one_sided_bars(self):
        for up, down, flat in ((0, 0, 0), (5500, 0, 0), (0, 5500, 0), (0, 0, 5500)):
            data = market()
            data.breadth = Breadth(up, down, flat, None, None)
            with self.subTest(up=up, down=down, flat=flat):
                _, canvas = self.draw_brief("close", data)
                color = render.HAIR if up + down + flat == 0 else render.RED if up else render.GREEN if down else render.MUTED
                self.assertEqual(canvas.image.getpixel((800, 597)), color)

    def test_utc_time_is_rendered_as_cst(self):
        for kind in ("close", "morning"):
            _, canvas = self.draw_brief(kind, market(), datetime(2026, 9, 30, 12, 40, tzinfo=timezone.utc))
            self.assertTrue(any(value == "生成09.30 20:40 CST" for value, _ in canvas.texts))

    def test_wrapping_keeps_percentage_tokens_together(self):
        canvas = AuditedCanvas()
        canvas.paragraph(10, 10, "沪深300+0.29%，科创50-2.51%。", 240, 28, 3)
        lines = [value for value, _ in canvas.texts]
        self.assertTrue(any("+0.29%" in value for value in lines))
        self.assertTrue(any("-2.51%" in value for value in lines))
        self.assertFalse(any(value.startswith("%") for value in lines))

    def test_morning_headline_follows_available_prices(self):
        for changes, expected in (((-.1, -.2, -.3), "美股三大指数下跌"), ((.1, -.2, .3), "美股三大指数涨跌互现"), ((0, 0, 0), "美股三大指数平收"), ((.1, None, .3), "美股行情待确认")):
            data = market()
            for quote, change in zip(data.overseas[:3], changes):
                quote.pct = change
            with self.subTest(changes=changes):
                _, canvas = self.draw_brief("morning", data)
                self.assertTrue(any(value == expected for value, _ in canvas.texts))

    def test_news_keep_reported_actual_and_expected_in_one_selected_headline(self):
        data = market()
        data.news = [NewsItem(datetime(2026, 9, 30, 19, 30, tzinfo=CST), "美国8月核心PCE物价指数同比 3.1%，预期 3.0%", "见闻", 2)]
        _, canvas = self.draw_brief("morning", data)
        text = "".join(value for value, _ in canvas.texts)
        self.assertIn("同比 3.1%，预期 3.0%", text)
        self.assertIn("09-30 19:30 CST · 见闻", text)
        data.news[0].title = "美国今晚公布PCE，市场预期同比3.0%"
        _, canvas = self.draw_brief("morning", data)
        text = "".join(value for value, _ in canvas.texts)
        self.assertIn("预期同比3.0%", text)
        self.assertNotIn("3.1%", text)

    def test_focus_body_uses_two_lines_and_keeps_long_headline_numbers(self):
        cases = (
            ("CME FedWatch：美联储10月加息概率较一日前降13.8个百分点 年内至少再加息一次概率降至86.8%", "86.8%", False),
            ("美联储维持利率不变", "利率不变", True),
        )
        for title, detail, show_context in cases:
            with self.subTest(title=title):
                data = market()
                data.news = [NewsItem(datetime(2026, 9, 30, 19, 30, tzinfo=CST), title, "东财", 2)]
                _, canvas = self.draw_brief("morning", data)
                body = [value for value, bounds in canvas.texts if bounds[0] >= 204 and 1248 <= bounds[1] < 1362]
                self.assertEqual(len(body), 2)
                self.assertIn(detail, "".join(body))
                context = "关注利率预期变化能否传导至股市。"
                self.assertEqual(context in body, show_context)
                if not show_context:
                    self.assertIn("13.8个", "".join(body))
                    self.assertFalse(any("…" in value for value in body))

    def test_compact_selection_leaves_complete_data_in_brief(self):
        for kind in ("close", "morning"):
            with self.subTest(kind=kind):
                brief, canvas = self.draw_brief(kind, market())
                text = "\n".join(value for value, _ in canvas.texts)
                self.assertIn("生物制药", text)
                self.assertIn("酿酒行业", text)
                self.assertIn("电子器件", text)
                self.assertIn("家用电器", text)
                self.assertNotIn("金融行业", text)
                self.assertNotIn("有色金属", text)
                self.assertNotIn("沪深300", text)
                self.assertNotIn("中证1000", text)
                self.assertNotIn("盘中看点", text)
                self.assertNotIn("外围参考", text)
                self.assertEqual(len(brief.indices), 8)
                self.assertEqual(len(brief.sectors_up), 3)
                self.assertEqual(len(brief.sectors_down), 3)

    def test_holiday_event_keeps_actual_date_and_distinguishes_edition(self):
        now = datetime(2026, 10, 1, 15, 0, tzinfo=CST)
        event = CalendarEvent("美国非农就业与失业率", datetime(2026, 10, 2, 20, 30, tzinfo=CST), "美国劳工统计局")
        _, canvas = self.draw_brief("morning", market(), now, event)
        text = "\n".join(value for value, _ in canvas.texts)
        self.assertIn("休市前瞻 · 下次交易10.08 · A股参考09.30", text)
        self.assertIn("假期关注", text)
        self.assertIn("计划 10-02 20:30 CST · 美国劳工统计局", text)
        self.assertNotIn("下一事件", text)


if __name__ == "__main__":
    unittest.main()
