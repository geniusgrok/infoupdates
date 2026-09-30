from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .client import fetch_text
from .models import Breadth, CapitalMix, CrossBorder, NewsItem, Quote, SectorFlow, SectorMove
from .parse import (
    parse_cme_future,
    parse_cn_index,
    parse_cross_border,
    parse_fenbu,
    parse_fflow_line,
    parse_fx,
    parse_hk_index,
    parse_industry_flows,
    parse_nikkei,
    parse_sina_bundle,
    parse_sina_industries,
    parse_spark_closes,
    parse_us_index,
)

CST = timezone(timedelta(hours=8))
SINA = "https://finance.sina.com.cn"
EM = "https://data.eastmoney.com/"
QUOTE = "https://quote.eastmoney.com/"
WSCN = "https://wallstreetcn.com/"
KUAIXUN = "https://kuaixun.eastmoney.com/"

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

EM_NEWS_COLUMNS = ("101", "110", "118", "119", "125")


@dataclass
class MarketData:
    indices: list[Quote]
    overseas: list[Quote] = field(default_factory=list)
    fx: list[Quote] = field(default_factory=list)
    futures: list[Quote] = field(default_factory=list)
    sectors_up: list[SectorMove] = field(default_factory=list)
    sectors_down: list[SectorMove] = field(default_factory=list)
    capital: list[CapitalMix] = field(default_factory=list)
    sector_in: list[SectorFlow] = field(default_factory=list)
    sector_out: list[SectorFlow] = field(default_factory=list)
    breadth: Breadth | None = None
    cross: CrossBorder | None = None
    spark: list[float] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _headline(title: str, body: str) -> str:
    title = _clean(title)
    if len(title) >= 8:
        return title
    text = _clean(body)
    if "。" in text:
        first = text.split("。", 1)[0].strip()
        if 8 <= len(first) <= 56:
            return first
    return text[:48].strip()


def _sina(symbols: str) -> dict[str, str]:
    url = "https://hq.sinajs.cn/list=" + symbols
    return parse_sina_bundle(fetch_text(url, SINA, encoding="gb18030"))


def fetch_indices() -> list[Quote]:
    bundle = _sina(",".join(INDEX_SYMBOLS))
    quotes: list[Quote] = []
    for symbol in INDEX_SYMBOLS:
        quote = parse_cn_index(symbol, bundle.get(symbol, ""))
        if quote:
            quotes.append(quote)
    if not any(quote.symbol == "sh000001" for quote in quotes):
        raise RuntimeError("没有取到上证指数")
    return quotes


def fetch_overseas() -> tuple[list[Quote], list[Quote], list[Quote]]:
    symbols = "gb_dji,gb_ixic,gb_inx,rt_hkHSI,rt_hkHSTECH,b_NKY,fx_susdcny,fx_susdcnh,hf_ES,hf_NQ"
    bundle = _sina(symbols)
    overseas = [
        parse_us_index("gb_dji", bundle.get("gb_dji", ""), "道琼斯"),
        parse_us_index("gb_ixic", bundle.get("gb_ixic", ""), "纳斯达克"),
        parse_us_index("gb_inx", bundle.get("gb_inx", ""), "标普500"),
        parse_hk_index("rt_hkHSI", bundle.get("rt_hkHSI", ""), "恒生指数"),
        parse_hk_index("rt_hkHSTECH", bundle.get("rt_hkHSTECH", ""), "恒生科技"),
        parse_nikkei("b_NKY", bundle.get("b_NKY", "")),
    ]
    fx = [
        parse_fx("fx_susdcny", bundle.get("fx_susdcny", ""), "在岸人民币"),
        parse_fx("fx_susdcnh", bundle.get("fx_susdcnh", ""), "离岸人民币"),
    ]
    futures = [
        parse_cme_future("hf_ES", bundle.get("hf_ES", ""), "标普500期货"),
        parse_cme_future("hf_NQ", bundle.get("hf_NQ", ""), "纳斯达克期货"),
    ]
    return (
        [quote for quote in overseas if quote],
        [quote for quote in fx if quote],
        [quote for quote in futures if quote],
    )


def fetch_sectors() -> tuple[list[SectorMove], list[SectorMove]]:
    text = fetch_text("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php", SINA, encoding="gbk")
    return parse_sina_industries(text)


def fetch_spark() -> list[float]:
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=24"
    )
    payload = json.loads(fetch_text(url, SINA, encoding="utf-8"))
    return parse_spark_closes(payload)


def fetch_capital() -> list[CapitalMix]:
    rows: list[CapitalMix] = []
    for secid, market in (("1.000001", "沪市"), ("0.399001", "深市")):
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            f"?lmt=1&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        )
        payload = json.loads(fetch_text(url, EM))
        lines = ((payload.get("data") or {}).get("klines")) or []
        if not lines:
            continue
        parsed = parse_fflow_line(market, lines[-1])
        if parsed:
            rows.append(parsed)
    return rows


def fetch_industry_flow() -> tuple[list[SectorFlow], list[SectorFlow]]:
    url = "https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&code=" + quote("m:90+s:4")
    payload = json.loads(fetch_text(url, EM))
    return parse_industry_flows(payload)


def fetch_breadth(day: str) -> Breadth:
    fenbu_url = (
        "https://push2ex.eastmoney.com/getTopicZDFenBu"
        f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&date={day}"
    )
    fenbu = json.loads(fetch_text(fenbu_url, QUOTE))
    items = ((fenbu.get("data") or {}).get("fenbu")) or []

    def pool_count(path: str) -> int | None:
        url = (
            f"https://push2ex.eastmoney.com/{path}?ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={day}"
        )
        payload = json.loads(fetch_text(url, QUOTE))
        data = payload.get("data") or {}
        if data.get("tc") is None:
            return None
        return int(data["tc"])

    return parse_fenbu(items, limit_up=pool_count("getTopicZTPool"), limit_down=pool_count("getTopicDTPool"))


def _first_row(payload: dict) -> dict | None:
    rows = ((payload.get("result") or {}).get("data")) or []
    return rows[0] if rows else None


def fetch_cross_border() -> CrossBorder:
    north_url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEALAMT"
        "&columns=ALL&pageNumber=1&pageSize=1&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB"
    )
    north = _first_row(json.loads(fetch_text(north_url, EM)))
    south_rows: dict[str, dict] = {}
    for kind in ("002", "004", "006"):
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEAL_HISTORY"
            "&columns=TRADE_DATE,NET_DEAL_AMT,MUTUAL_TYPE"
            f"&filter=(MUTUAL_TYPE%3D%22{kind}%22)&pageNumber=1&pageSize=1"
            "&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB"
        )
        row = _first_row(json.loads(fetch_text(url, EM)))
        if row:
            south_rows[kind] = row
    return parse_cross_border(north, south_rows)


def _wscn_items(channel: str, pages: int = 3) -> list[NewsItem]:
    items: list[NewsItem] = []
    cursor = ""
    for _ in range(pages):
        url = f"https://api-one.wallstcn.com/apiv1/content/lives?channel={channel}&client=pc&limit=50"
        if cursor:
            url += f"&cursor={cursor}"
        payload = json.loads(fetch_text(url, WSCN))
        data = payload.get("data") or {}
        for raw in data.get("items") or []:
            title = _headline(raw.get("title") or "", raw.get("content_text") or raw.get("content") or "")
            if not title:
                continue
            published = datetime.fromtimestamp(int(raw.get("display_time") or 0), CST)
            items.append(
                NewsItem(
                    published=published,
                    title=title,
                    source="见闻",
                    source_score=float(raw.get("score") or 1),
                )
            )
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break
    return items


def _em_items(column: str) -> list[NewsItem]:
    url = (
        "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        f"?client=web&biz=web_724&fastColumn={column}&sortEnd=&pageSize=20&req_trace=1"
    )
    payload = json.loads(fetch_text(url, KUAIXUN))
    items: list[NewsItem] = []
    for raw in ((payload.get("data") or {}).get("fastNewsList")) or []:
        title = _headline(raw.get("title") or "", raw.get("summary") or "")
        if not title:
            continue
        published = datetime.strptime(raw["showTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
        score = float(raw.get("titleColor") or 0)
        if column == "102" and score < 2:
            continue
        if score <= 0:
            score = 1.4
        items.append(NewsItem(published=published, title=title, source="东财", source_score=score))
    return items


def fetch_news() -> list[NewsItem]:
    items: list[NewsItem] = []
    items.extend(_wscn_items("global-channel", pages=2))
    items.extend(_wscn_items("a-stock-channel", pages=3))
    for column in EM_NEWS_COLUMNS:
        try:
            items.extend(_em_items(column))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return items


def _run(label: str, fn, notes: list[str]):
    try:
        return fn()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        notes.append(f"{label}暂缺")
        print(f"[warn] {label}: {exc}")
        return None


def load_market() -> MarketData:
    notes: list[str] = []
    indices = fetch_indices()
    hero = next(quote for quote in indices if quote.symbol == "sh000001")
    day = hero.trade_day.replace("-", "")
    data = MarketData(indices=indices, notes=notes)

    jobs = {
        "overseas": fetch_overseas,
        "sectors": fetch_sectors,
        "spark": fetch_spark,
        "capital": fetch_capital,
        "industry": fetch_industry_flow,
        "breadth": lambda: fetch_breadth(day),
        "cross": fetch_cross_border,
        "news": fetch_news,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, label, fn, notes): label for label, fn in jobs.items()}
        results = {label: future.result() for future, label in futures.items()}

    overseas = results.get("overseas")
    if overseas:
        data.overseas, data.fx, data.futures = overseas
    sectors = results.get("sectors")
    if sectors:
        data.sectors_up, data.sectors_down = sectors
    data.spark = results.get("spark") or []
    data.capital = results.get("capital") or []
    industry = results.get("industry")
    if industry:
        data.sector_in, data.sector_out = industry
    data.breadth = results.get("breadth")
    data.cross = results.get("cross")
    data.news = results.get("news") or []
    return data
