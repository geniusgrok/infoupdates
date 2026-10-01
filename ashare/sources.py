from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote

from common.http import fetch_text
from common.news import NewsItem, em_items, wscn_items

from .models import Breadth, CapitalMix, CrossBorder, Quote, SectorFlow, SectorMove, TurnoverComparison
from .parse import (
    INDEX_NAMES, parse_cn_index, parse_cross_border, parse_fenbu, parse_fflow_line,
    parse_fx, parse_hk_index, parse_industry_flows, parse_nikkei, parse_tencent_bundle,
    parse_tencent_capital, parse_tencent_fx, parse_tencent_quote, parse_sina_board_money,
    parse_sina_bundle, parse_sina_industries, parse_sohu_turnover, parse_eastmoney_turnover, parse_us_index,
)

SINA = "https://finance.sina.com.cn"
EASTMONEY = "https://data.eastmoney.com/"
EASTMONEY_QUOTE = "https://quote.eastmoney.com/"

INDEX_SYMBOLS = (
    "sh000001",
    "sz399001",
    "sz399006",
    "sh000300",
    "sh000016",
    "sh000905",
    "sh000852",
    "sh000688",
)

NEWS_COLUMNS = ("101", "110", "118", "119", "125")


TENCENT = "https://gu.qq.com"
TENCENT_INDEXES = ",".join(INDEX_SYMBOLS)
TENCENT_GLOBAL = "usDJI,usIXIC,usINX,hkHSI,hkHSTECH,whUSDCNY"
EASTMONEY_HISTORY = "https://push2his.eastmoney.com"


def optional(label: str, fn):
    try:
        return fn()
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        print(f"[warn] {label}: {exc}")
        return None


def _json(url: str, referer: str, **request) -> dict:
    payload = json.loads(fetch_text(url, referer, **request))
    if not isinstance(payload, dict):
        raise ValueError("行情响应不是 JSON 对象")
    return payload


def _sina(symbols: str) -> dict[str, str]:
    url = "https://hq.sinajs.cn/list=" + symbols
    return parse_sina_bundle(fetch_text(url, SINA, encoding="gb18030"))


def _tencent(symbols: str) -> dict[str, str]:
    text = fetch_text("https://qt.gtimg.cn/q=" + symbols, TENCENT, encoding="gbk", timeout=12, retries=0)
    return parse_tencent_bundle(text)


def sina_indices() -> list[Quote]:
    bundle = _sina(",".join(INDEX_SYMBOLS))
    quotes: list[Quote] = []
    for symbol in INDEX_SYMBOLS:
        quote = parse_cn_index(symbol, bundle.get(symbol, ""))
        if quote:
            quotes.append(quote)
    if not any(quote.name == "上证指数" and quote.last > 0 for quote in quotes):
        raise RuntimeError("新浪没有返回上证指数")
    return quotes


def tencent_indices() -> list[Quote]:
    bundle = _tencent(TENCENT_INDEXES)
    quotes: list[Quote] = []
    for symbol in INDEX_SYMBOLS:
        quote = parse_tencent_quote(symbol, bundle.get(symbol, ""), INDEX_NAMES[symbol], "cn")
        if quote:
            quotes.append(quote)
    if not any(quote.name == "上证指数" and quote.last > 0 for quote in quotes):
        raise RuntimeError("腾讯没有返回上证指数")
    return quotes


def sina_overseas() -> tuple[list[Quote], list[Quote]]:
    symbols = "gb_dji,gb_ixic,gb_inx,rt_hkHSI,rt_hkHSTECH,b_NKY,b_KOSPI,b_KOSDAQ,fx_susdcny,fx_susdcnh"
    bundle = _sina(symbols)
    overseas = [
        item
        for item in (
            parse_us_index("gb_dji", bundle.get("gb_dji", ""), "道琼斯"),
            parse_us_index("gb_ixic", bundle.get("gb_ixic", ""), "纳斯达克"),
            parse_us_index("gb_inx", bundle.get("gb_inx", ""), "标普500"),
            parse_hk_index("rt_hkHSI", bundle.get("rt_hkHSI", ""), "恒生指数"),
            parse_hk_index("rt_hkHSTECH", bundle.get("rt_hkHSTECH", ""), "恒生科技"),
            parse_nikkei("b_NKY", bundle.get("b_NKY", "")),
            parse_nikkei("b_KOSPI", bundle.get("b_KOSPI", ""), "韩国KOSPI"),
            parse_nikkei("b_KOSDAQ", bundle.get("b_KOSDAQ", ""), "韩国KOSDAQ"),
        )
        if item
    ]
    fx = [
        item
        for item in (
            parse_fx("fx_susdcny", bundle.get("fx_susdcny", ""), "在岸人民币"),
            parse_fx("fx_susdcnh", bundle.get("fx_susdcnh", ""), "离岸人民币"),
        )
        if item
    ]
    if not overseas and not fx:
        raise RuntimeError("新浪外盘为空")
    return overseas, fx


def tencent_overseas() -> tuple[list[Quote], list[Quote]]:
    bundle = _tencent(TENCENT_GLOBAL)
    mapping = (
        ("usDJI", "gb_dji", "道琼斯", "us"),
        ("usIXIC", "gb_ixic", "纳斯达克", "us"),
        ("usINX", "gb_inx", "标普500", "us"),
        ("hkHSI", "rt_hkHSI", "恒生指数", "hk"),
        ("hkHSTECH", "rt_hkHSTECH", "恒生科技", "hk"),
    )
    overseas = [
        quote
        for symbol, canonical, name, kind in mapping
        if (quote := parse_tencent_quote(canonical, bundle.get(symbol, ""), name, kind))
    ]
    fx = parse_tencent_fx("fx_susdcny", bundle.get("whUSDCNY", ""), "在岸人民币")
    if not overseas and fx is None:
        raise RuntimeError("腾讯外盘为空")
    return overseas, [fx] if fx else []


def sina_sectors() -> tuple[list[SectorMove], list[SectorMove]]:
    text = fetch_text("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php", SINA, encoding="gbk")
    leaders, laggards = parse_sina_industries(text)
    if not leaders and not laggards:
        raise RuntimeError("新浪行业为空")
    return leaders, laggards


def sina_sector_money() -> tuple[list[SectorMove], list[SectorMove], list[SectorFlow], list[SectorFlow]]:
    url = (
        "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "MoneyFlow.ssl_bkzj_bk?page=1&num=80&sort=netamount&asc=0&fenlei=0"
    )
    text = fetch_text(url, SINA, encoding="utf-8", timeout=12, retries=0)
    leaders, laggards, inflow, outflow = parse_sina_board_money(text)
    if not leaders and not inflow:
        raise RuntimeError("新浪行业资金为空")
    return leaders, laggards, inflow, outflow


def eastmoney_capital(secid: str, market: str) -> CapitalMix:
    url = (
        f"{EASTMONEY_HISTORY}/api/qt/stock/fflow/daykline/get"
        f"?lmt=1&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
    )
    payload = _json(url, EASTMONEY, timeout=12, retries=1)
    data = payload.get("data")
    lines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(lines, list) or not lines or not isinstance(lines[-1], str):
        raise RuntimeError(f"{market}资金为空")
    parsed = parse_fflow_line(market, lines[-1])
    if parsed is None:
        raise RuntimeError(f"{market}资金无法解析")
    return parsed


def tencent_capital(code: str, market: str) -> CapitalMix:
    url = f"https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab?code={code}"
    payload = _json(url, TENCENT, timeout=12, retries=0)
    parsed = parse_tencent_capital(market, payload)
    if parsed is None:
        raise RuntimeError(f"{market}腾讯资金为空")
    return parsed


def eastmoney_flows() -> tuple[list[SectorFlow], list[SectorFlow]]:
    url = "https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&code=" + quote("m:90+s:4")
    payload = _json(url, EASTMONEY)
    inflow, outflow = parse_industry_flows(payload)
    if not inflow and not outflow:
        raise RuntimeError("东财行业资金为空")
    return inflow, outflow


def eastmoney_breadth(day: str) -> Breadth:
    fenbu_url = (
        "https://push2ex.eastmoney.com/getTopicZDFenBu"
        f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&date={day}"
    )
    fenbu = _json(fenbu_url, EASTMONEY_QUOTE)
    data = fenbu.get("data")
    items = data.get("fenbu") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("涨跌分布为空")

    def pool_count(path: str) -> int | None:
        url = (
            f"https://push2ex.eastmoney.com/{path}?ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={day}"
        )
        try:
            payload = _json(url, EASTMONEY_QUOTE, timeout=12, retries=0)
        except (OSError, ValueError):
            return None
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("tc") is None:
            return None
        try:
            return int(data["tc"])
        except (ValueError, TypeError, OverflowError):
            return None

    return parse_fenbu(items, limit_up=pool_count("getTopicZTPool"), limit_down=pool_count("getTopicDTPool"))


def _first_row(payload: dict) -> dict | None:
    result = payload.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None


def _cross_row(url: str) -> dict | None:
    return _first_row(_json(url, EASTMONEY, timeout=12, retries=0))


def eastmoney_cross_border(trade_day: str = "") -> CrossBorder:
    day_filter = "&filter=" + quote(f"(TRADE_DATE='{trade_day}')") if trade_day else ""
    north_url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEALAMT"
        "&columns=ALL&pageNumber=1&pageSize=1&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB"
    ) + day_filter
    north = optional("北向成交", lambda: _cross_row(north_url))
    if north and trade_day and str(north.get("TRADE_DATE") or "")[:10] != trade_day:
        north = None
    south_rows: dict[str, dict] = {}
    for kind in ("002", "004", "006"):
        filters = f'(MUTUAL_TYPE="{kind}")'
        if trade_day:
            filters += f"(TRADE_DATE='{trade_day}')"
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEAL_HISTORY"
            "&columns=TRADE_DATE,NET_DEAL_AMT,MUTUAL_TYPE"
            f"&filter={quote(filters)}&pageNumber=1&pageSize=1"
            "&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB"
        )
        row = optional(f"南向{kind}", lambda url=url: _cross_row(url))
        if row and (not trade_day or str(row.get("TRADE_DATE") or "")[:10] == trade_day):
            south_rows[kind] = row
    result = parse_cross_border(north, south_rows)
    if result.north_turnover is None and result.south_net is None:
        raise RuntimeError("跨境资金为空")
    return result


def news() -> list[NewsItem]:
    items: list[NewsItem] = []
    for channel, pages in (("global-channel", 2), ("a-stock-channel", 3)):
        found = optional(f"见闻{channel}", lambda channel=channel, pages=pages: wscn_items(channel, pages))
        if found:
            items.extend(found)
    for column in NEWS_COLUMNS:
        found = optional(f"东财快讯{column}", lambda column=column: em_items(column))
        if found:
            items.extend(found)
    if not items:
        raise RuntimeError("快讯为空")
    return items


def sohu_turnover(trade_date: date) -> TurnoverComparison | None:
    start = (trade_date - timedelta(days=30)).strftime("%Y%m%d")
    end = trade_date.strftime("%Y%m%d")
    url = f"https://q.stock.sohu.com/hisHq?code=zs_000001&start={start}&end={end}&stat=1&order=D&period=d"
    return parse_sohu_turnover(
        json.loads(fetch_text(url, "https://q.stock.sohu.com/", encoding="gb18030", timeout=12, retries=0)),
        trade_date,
    )


def eastmoney_turnover(trade_date: date) -> TurnoverComparison | None:
    url = (
        f"{EASTMONEY_HISTORY}/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=40"
        f"&end={trade_date:%Y%m%d}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    payload = _json(url, EASTMONEY, timeout=12, retries=0)
    return parse_eastmoney_turnover(payload, trade_date)
