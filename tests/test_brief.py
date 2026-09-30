from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from a_share_brief.compose import build_brief
from a_share_brief.fetch import MarketData
from a_share_brief.format import fmt_amount, fmt_yi, million_to_ccy
from a_share_brief.models import NewsItem, Quote
from a_share_brief.parse import (
    INDEX_ORDER,
    combine_quotes,
    parse_cme_future,
    parse_cn_index,
    parse_fenbu,
    parse_fflow_line,
    parse_fx,
    parse_hk_index,
    parse_qq_capital,
    parse_qq_fx,
    parse_qq_quote,
    parse_qq_spark,
    parse_sina_board_money,
    parse_sina_industries,
    parse_us_index,
)
from a_share_brief.rank import select_news
from a_share_brief.render import render_png
from a_share_brief.social import social_copy

CST = timezone(timedelta(hours=8))

SHANGHAI = (
    "上证指数,3839.2527,3830.4513,3842.1946,3851.2169,3833.0863,0,0,414560247,679398992445,"
    "0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-09-30,15:35:28,00,"
)
DOW = (
    "道琼斯,51349.9219,-0.26,2026-09-30 04:39:04,-131.5900,51416.9609,51505.1914,51129.1797,"
    "54744.3281,45057.2812,386242184,458828496,0,0.00,--,0.00,0.00,0.00,0.00,0,0,0.0000,0.00,0.0000,,"
    "Sep 29 04:38PM EDT,51481.5117,0,1,2026"
)
HSI = "HSI,恒生指数,24393.880,24523.570,24637.650,24332.640,24613.270,89.700,0.370,0.000,0.000,0,0,0,0,0,0,2026/09/30,16:08:50"
FX = "20:28:00,6.7037000000,6.7047000000,6.7065000000,38,6.7050000000,6.7059000000,6.7021000000,6.7047000000,在岸人民币,-0.0268,-0.0018"
ES = "7741.550,,7740.250,7740.500,7756.500,7719.500,20:28:56,7732.000,7739.000,0,7,8,2026-09-30,标普500指数期货,0"
INDUSTRY = (
    '{"new_swzz":"new_swzz,生物制药,155,14.97,0.32,2.1627624631775,1,1,sz300122,6.881,13.980,0.900,智飞生物",'
    '"new_dzqj":"new_dzqj,电子器件,10,1,1,-1.9066573200439,1,1,sz000001,1,1,1,新亚制程",'
    '"new_cxg":"new_cxg,次新股,10,1,1,-1.6,1,1,sz000001,1,1,1,泰诺麦博"}'
)
FENBU = [
    {"-1": 1006},
    {"-10": 7},
    {"-11": 9},
    {"-2": 782},
    {"-3": 456},
    {"-4": 242},
    {"-5": 140},
    {"-6": 58},
    {"-7": 24},
    {"-8": 15},
    {"-9": 8},
    {"0": 147},
    {"1": 1060},
    {"10": 20},
    {"11": 52},
    {"2": 758},
    {"3": 293},
    {"4": 143},
    {"5": 75},
    {"6": 30},
    {"7": 19},
    {"8": 13},
    {"9": 7},
]


class ParseTests(unittest.TestCase):
    def test_shanghai_index(self) -> None:
        quote = parse_cn_index("sh000001", SHANGHAI)
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.name, "上证指数")
        self.assertAlmostEqual(quote.last, 3842.1946, places=2)
        self.assertAlmostEqual(quote.pct or 0, 0.3066, places=2)
        self.assertAlmostEqual(quote.amount or 0, 679398992445, places=0)
        self.assertEqual(quote.trade_day, "2026-09-30")

    def test_us_hk_fx_futures(self) -> None:
        dow = parse_us_index("gb_dji", DOW, "道琼斯")
        assert dow is not None
        self.assertAlmostEqual(dow.pct or 0, -0.26, places=2)
        self.assertEqual(dow.session, "09-29 收盘")
        hsi = parse_hk_index("rt_hkHSI", HSI, "恒生指数")
        assert hsi is not None
        self.assertAlmostEqual(hsi.last, 24613.27, places=2)
        self.assertAlmostEqual(hsi.pct or 0, 0.37, places=2)
        fx = parse_fx("fx_susdcny", FX, "在岸人民币")
        assert fx is not None
        self.assertAlmostEqual(fx.last, 6.7047, places=4)
        self.assertEqual(fx.name, "在岸人民币")
        future = parse_cme_future("hf_ES", ES, "标普500期货")
        assert future is not None
        self.assertAlmostEqual(future.last, 7741.55, places=2)
        self.assertAlmostEqual(future.prev_close or 0, 7739.0, places=2)
        self.assertGreater(future.pct or 0, 0)

    def test_industry_percent_is_not_rescaled(self) -> None:
        leaders, laggards = parse_sina_industries(INDUSTRY, limit=3)
        self.assertEqual(leaders[0].name, "生物制药")
        self.assertAlmostEqual(leaders[0].pct, 2.1628, places=3)
        self.assertEqual(leaders[0].leader, "智飞生物")
        self.assertEqual(laggards[0].name, "电子器件")
        self.assertNotIn("次新股", [item.name for item in leaders + laggards])
        gainers, losers = parse_sina_industries(
            '{"a":"a,食品,1,1,1,1.2,1,1,sz1,1,1,1,甲","b":"b,钢铁,1,1,1,0.4,1,1,sz2,1,1,1,乙"}'
        )
        self.assertEqual([item.name for item in gainers], ["食品", "钢铁"])
        self.assertEqual(losers, [])

    def test_fenbu_and_flow_units(self) -> None:
        breadth = parse_fenbu(FENBU, limit_up=52, limit_down=9)
        self.assertEqual(breadth.up, 2470)
        self.assertEqual(breadth.down, 2747)
        self.assertEqual(breadth.flat, 147)
        self.assertEqual(sum(bucket.count for bucket in breadth.buckets), breadth.total)
        flow = parse_fflow_line("沪市", "2026-09-30,-5926543360,8544112640,-2617573376,-3751186432,-2175356928,-0.87")
        assert flow is not None
        self.assertAlmostEqual(flow.main, flow.large + flow.super_order)
        self.assertAlmostEqual(million_to_ccy(6863.61) or 0, 6_863_610_000)
        self.assertEqual(fmt_yi(-13628162048, signed=True), "-136.3亿")
        self.assertEqual(fmt_amount(1_438_000_000_000), "1.44万亿")

    def test_news_prefers_recap_over_reduction(self) -> None:
        now = datetime(2026, 9, 30, 20, 40, tzinfo=CST)
        items = [
            NewsItem(now, "长城证券：股东拟减持不超1.5%股份", "见闻", source_score=2),
            NewsItem(now.replace(hour=15), "9月30日收评：A股9月收官，双创指数回落", "见闻", source_score=2),
            NewsItem(now.replace(hour=9, minute=22), "央行今日开展8335亿元隔夜逆回购操作", "东财", source_score=2),
        ]
        picked = select_news(items, kind="close", trade_date=now.date(), now=now, limit=3)
        self.assertTrue(picked[0].title.startswith("9月30日收评"))
        self.assertNotIn("减持", " ".join(item.title for item in picked))


QQ_SH = (
    "1~上证指数~000001~3842.19~3830.45~3839.25~414560247~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~"
    "20260930161500~11.74~0.31~3851.22~3833.09~3842.19/414560247/679398992445~414560247~67939899"
)
QQ_US = (
    "200~道琼斯~.DJI~51349.92~51481.51~51416.96~386242184~0~0~51239.37~0~0~0~0~0~0~0~0~0~51502.79~0~0~0~0~0~0~0~0~0~~"
    "2026-09-29 16:38:50~-131.59~-0.26~51505.19~51129.18~USD"
)
QQ_HK = (
    "100~恒生指数~HSI~24613.270~24523.570~24393.880~1~0~0~24613.270~0~0~0~0~0~0~0~0~0~24613.270~0~0~0~0~0~0~0~0~0~0.0~"
    "2026/09/30 16:08:50~89.700~0.37~24637.650~24332.640"
)
QQ_FX = "310~美元人民币~USDCNY~6.7046~0~20260930210758~6.7065~6.7050~6.7055~6.7030~6.7046~6.7047~-0.0019~-0.03~0.06"
BOARD_MONEY = (
    '[{"name":"生物制药","avg_changeratio":"0.0245828","netamount":"5385744875.06","ts_name":"智飞生物","category":"new_swzz"},'
    '{"name":"电子器件","avg_changeratio":"-0.019066","netamount":"-1200000000","ts_name":"*ST测试","category":"new_dzqj"}]'
)


class FallbackTests(unittest.TestCase):
    def test_tencent_quotes(self) -> None:
        quote = parse_qq_quote("sh000001", QQ_SH, "上证指数", "cn")
        assert quote is not None
        self.assertAlmostEqual(quote.last, 3842.19, places=2)
        self.assertAlmostEqual(quote.pct or 0, 0.31, places=2)
        self.assertEqual(quote.trade_day, "2026-09-30")
        self.assertAlmostEqual(quote.amount or 0, 679398990000, places=-3)
        dow = parse_qq_quote("gb_dji", QQ_US, "道琼斯", "us")
        assert dow is not None
        self.assertAlmostEqual(dow.pct or 0, -0.26, places=2)
        self.assertEqual(dow.session, "09-29 收盘")
        hsi = parse_qq_quote("rt_hkHSI", QQ_HK, "恒生指数", "hk")
        assert hsi is not None
        self.assertAlmostEqual(hsi.last, 24613.27, places=2)
        self.assertIn("09-30", hsi.session)
        fx = parse_qq_fx("fx_susdcny", QQ_FX, "在岸人民币")
        assert fx is not None
        self.assertAlmostEqual(fx.last, 6.7046, places=4)
        self.assertAlmostEqual(fx.pct or 0, -0.03, places=2)
        closes = parse_qq_spark({"data": {"sh000001": {"day": [["2026-09-29", "1", "3830.45", "2", "3", "4"], ["2026-09-30", "1", "3842.19", "2", "3", "4"]]}}})
        self.assertEqual(closes, [3830.45, 3842.19])

    def test_board_money_and_source_choice(self) -> None:
        leaders, laggards, inflow, outflow = parse_sina_board_money(BOARD_MONEY)
        self.assertAlmostEqual(leaders[0].pct, 2.45828, places=3)
        self.assertEqual(leaders[0].leader, "智飞生物")
        self.assertEqual(laggards[0].leader, "")
        self.assertEqual(outflow[0].name, "电子器件")
        primary = [Quote("sh000001", "上证指数", 1, pct=0.1)]
        secondary = [Quote("sh000001", "上证指数", 9, pct=9), Quote("sh000688", "科创50", 2, pct=-1)]
        merged, status = combine_quotes(primary, secondary, INDEX_ORDER, required="上证指数")
        self.assertEqual(status, "partial")
        self.assertEqual(merged[0].last, 1)
        star = next(item for item in merged if item.name == "科创50")
        self.assertEqual(star.last, 2)
        fallback, fallback_status = combine_quotes([], secondary, INDEX_ORDER, required="上证指数")
        self.assertEqual(fallback_status, "fallback")
        self.assertEqual(fallback[0].name, "上证指数")
        missing, missing_status = combine_quotes([], [], INDEX_ORDER, required="上证指数")
        self.assertEqual(missing_status, "missing")
        capital = parse_qq_capital(
            "沪市",
            {"data": {"todayFundFlow": {"mainNetIn": "-100", "superFlow": "-40", "bigFlow": "-60", "normalFlow": "10", "smallFlow": "90"}}},
        )
        assert capital is not None
        self.assertAlmostEqual(capital.main, capital.super_order + capital.large)

    def test_missing_index_still_renders(self) -> None:
        now = datetime(2026, 9, 30, 20, 40, tzinfo=CST)
        brief = build_brief("close", MarketData(indices=[], news=[NewsItem(now, "央行今日开展8335亿元隔夜逆回购操作", "东财", 2)]), now=now)
        self.assertEqual(brief.narrative.style, "数据暂缺")
        self.assertIn("数据暂缺", social_copy(brief))
        with tempfile.TemporaryDirectory() as folder:
            path = render_png(brief, Path(folder) / "empty.png")
            self.assertGreater(path.stat().st_size, 10_000)


class RenderTests(unittest.TestCase):
    def test_posters_render(self) -> None:
        now = datetime(2026, 9, 30, 20, 40, tzinfo=CST)
        hero = parse_cn_index("sh000001", SHANGHAI)
        assert hero is not None
        chi_next = Quote("sz399006", "创业板指", 3135.28, pct=-0.23, change=-7.29)
        star = Quote("sh000688", "科创50", 1530.01, pct=-2.51, change=-39.33)
        sz50 = Quote("sh000016", "上证50", 2823.28, pct=0.47, change=13.16)
        data = MarketData(
            indices=[hero, chi_next, star, sz50],
            overseas=[parse_us_index("gb_dji", DOW, "道琼斯")],
            fx=[parse_fx("fx_susdcny", FX, "在岸人民币")],
            futures=[parse_cme_future("hf_ES", ES, "标普500期货")],
            news=[
                NewsItem(now.replace(hour=15), "9月30日收评：A股9月收官，双创指数回落", "见闻", 2),
                NewsItem(now.replace(hour=9, minute=22), "央行今日开展8335亿元隔夜逆回购操作", "东财", 2),
            ],
            spark=[3936.5, 3888.4, 3823.6, 3830.5, 3842.2],
        )
        data.breadth = parse_fenbu(FENBU, 52, 9)
        with tempfile.TemporaryDirectory() as folder:
            for kind in ("close", "morning"):
                brief = build_brief(kind, data, now=now)
                self.assertIn(brief.narrative.style, {"权重护盘", "结构分化", "普跌", "普涨", "成长占优"})
                path = render_png(brief, Path(folder) / f"{kind}.png")
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 20_000)
                copy = social_copy(brief)
                self.assertIn("不构成投资建议", copy)
                if kind == "close":
                    self.assertIn("A股收盘综述｜9月30日 周三", copy)
                    self.assertNotIn("盘后要闻", copy)
                else:
                    self.assertIn("A股早盘｜10月1日 周四", copy)
                    self.assertIn("昨日情绪", copy)
                    self.assertIn("今日关注", copy)


if __name__ == "__main__":
    unittest.main()
