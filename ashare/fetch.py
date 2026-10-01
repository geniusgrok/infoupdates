from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

from brief_common.news import em_items as _em_items, wscn_items as _wscn_items

from .client import fetch_text
from .models import CST, Breadth, CapitalMix, CrossBorder, NewsItem, Quote, SectorFlow, SectorMove, TurnoverComparison
from .parse import (
    INDEX_ORDER,
    OVERSEAS_ORDER,
    combine_quotes,
    parse_cn_index,
    parse_cross_border,
    parse_fenbu,
    parse_fflow_line,
    parse_fx,
    parse_hk_index,
    parse_industry_flows,
    parse_nikkei,
    parse_qq_bundle,
    parse_qq_capital,
    parse_qq_fx,
    parse_qq_quote,
    parse_sina_board_money,
    parse_sina_bundle,
    parse_sina_industries,
    parse_sohu_turnover,
    parse_us_index,
    INDEX_NAMES,
)

SINA = "https://finance.sina.com.cn"
EM = "https://data.eastmoney.com/"
QUOTE = "https://quote.eastmoney.com/"

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
    sector_source: str = "新浪行业"
    flow_source: str = "东财行业"
    notes: list[str] = field(default_factory=list)
    turnover_comparison: TurnoverComparison | None = None


QQ = "https://gu.qq.com"
QQ_INDEXES = "sh000001,sz399001,sz399006,sh000300,sh000016,sh000905,sh000852,sh000688"
QQ_GLOBAL = "usDJI,usIXIC,usINX,hkHSI,hkHSTECH,whUSDCNY"
QQ_FILLABLE = ("道琼斯", "纳斯达克", "标普500", "恒生指数", "恒生科技")
FFLOW_HOST = "https://push2his.eastmoney.com"


def _try(label: str, fn):
    try:
        return fn()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError, TypeError) as exc:
        print(f"[warn] {label}: {exc}")
        return None


def _sina(symbols: str) -> dict[str, str]:
    url = "https://hq.sinajs.cn/list=" + symbols
    return parse_sina_bundle(fetch_text(url, SINA, encoding="gb18030"))


def _qq(symbols: str) -> dict[str, str]:
    text = fetch_text("https://qt.gtimg.cn/q=" + symbols, QQ, encoding="gbk", timeout=12, retries=0)
    return parse_qq_bundle(text)


def _indices_sina() -> list[Quote]:
    bundle = _sina(",".join(INDEX_SYMBOLS))
    quotes: list[Quote] = []
    for symbol in INDEX_SYMBOLS:
        quote = parse_cn_index(symbol, bundle.get(symbol, ""))
        if quote:
            quotes.append(quote)
    if not any(quote.name == "上证指数" and quote.last > 0 for quote in quotes):
        raise RuntimeError("新浪没有返回上证指数")
    return quotes


def _indices_qq() -> list[Quote]:
    bundle = _qq(QQ_INDEXES)
    quotes: list[Quote] = []
    for symbol in INDEX_SYMBOLS:
        quote = parse_qq_quote(symbol, bundle.get(symbol, ""), INDEX_NAMES[symbol], "cn")
        if quote:
            quotes.append(quote)
    if not any(quote.name == "上证指数" and quote.last > 0 for quote in quotes):
        raise RuntimeError("腾讯没有返回上证指数")
    return quotes


def _overseas_sina() -> tuple[list[Quote], list[Quote], list[Quote]]:
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
    return overseas, fx, []


def _overseas_qq() -> tuple[list[Quote], list[Quote]]:
    bundle = _qq(QQ_GLOBAL)
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
        if (quote := parse_qq_quote(canonical, bundle.get(symbol, ""), name, kind))
    ]
    fx = parse_qq_fx("fx_susdcny", bundle.get("whUSDCNY", ""), "在岸人民币")
    if not overseas and fx is None:
        raise RuntimeError("腾讯外盘为空")
    return overseas, [fx] if fx else []


def _sectors_sina() -> tuple[list[SectorMove], list[SectorMove]]:
    text = fetch_text("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php", SINA, encoding="gbk")
    leaders, laggards = parse_sina_industries(text)
    if not leaders and not laggards:
        raise RuntimeError("新浪行业为空")
    return leaders, laggards


def _board_money() -> tuple[list[SectorMove], list[SectorMove], list[SectorFlow], list[SectorFlow]]:
    url = (
        "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "MoneyFlow.ssl_bkzj_bk?page=1&num=80&sort=netamount&asc=0&fenlei=0"
    )
    text = fetch_text(url, SINA, encoding="utf-8", timeout=12, retries=0)
    leaders, laggards, inflow, outflow = parse_sina_board_money(text)
    if not leaders and not inflow:
        raise RuntimeError("新浪行业资金为空")
    return leaders, laggards, inflow, outflow


def _capital_em(secid: str, market: str) -> CapitalMix:
    url = (
        f"{FFLOW_HOST}/api/qt/stock/fflow/daykline/get"
        f"?lmt=1&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
    )
    payload = json.loads(fetch_text(url, EM, timeout=12, retries=1))
    lines = ((payload.get("data") or {}).get("klines")) or []
    if not lines:
        raise RuntimeError(f"{market}资金为空")
    parsed = parse_fflow_line(market, lines[-1])
    if parsed is None:
        raise RuntimeError(f"{market}资金无法解析")
    return parsed


def _capital_qq(code: str, market: str) -> CapitalMix:
    url = f"https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab?code={code}"
    payload = json.loads(fetch_text(url, QQ, timeout=12, retries=0))
    parsed = parse_qq_capital(market, payload)
    if parsed is None:
        raise RuntimeError(f"{market}腾讯资金为空")
    return parsed


def _industry_em() -> tuple[list[SectorFlow], list[SectorFlow]]:
    url = "https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&code=" + quote("m:90+s:4")
    payload = json.loads(fetch_text(url, EM))
    inflow, outflow = parse_industry_flows(payload)
    if not inflow and not outflow:
        raise RuntimeError("东财行业资金为空")
    return inflow, outflow


def _breadth(day: str) -> Breadth:
    fenbu_url = (
        "https://push2ex.eastmoney.com/getTopicZDFenBu"
        f"?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&date={day}"
    )
    fenbu = json.loads(fetch_text(fenbu_url, QUOTE))
    items = ((fenbu.get("data") or {}).get("fenbu")) or []
    if not items:
        raise RuntimeError("涨跌分布为空")

    def pool_count(path: str) -> int | None:
        url = (
            f"https://push2ex.eastmoney.com/{path}?ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={day}"
        )
        try:
            payload = json.loads(fetch_text(url, QUOTE, timeout=12, retries=0))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        data = payload.get("data") or {}
        if data.get("tc") is None:
            return None
        return int(data["tc"])

    return parse_fenbu(items, limit_up=pool_count("getTopicZTPool"), limit_down=pool_count("getTopicDTPool"))


def _first_row(payload: dict) -> dict | None:
    rows = ((payload.get("result") or {}).get("data")) or []
    return rows[0] if rows else None


def _cross_row(url: str) -> dict | None:
    return _first_row(json.loads(fetch_text(url, EM, timeout=12, retries=0)))


def fetch_cross_border(trade_day: str = "") -> CrossBorder:
    day_filter = "&filter=" + quote(f"(TRADE_DATE='{trade_day}')") if trade_day else ""
    north_url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEALAMT"
        "&columns=ALL&pageNumber=1&pageSize=1&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB"
    ) + day_filter
    north = _try("北向成交", lambda: _cross_row(north_url))
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
        row = _try(f"南向{kind}", lambda url=url: _cross_row(url))
        if row and (not trade_day or str(row.get("TRADE_DATE") or "")[:10] == trade_day):
            south_rows[kind] = row
    result = parse_cross_border(north, south_rows)
    if result.north_turnover is None and result.south_net is None:
        raise RuntimeError("跨境资金为空")
    return result


def _news() -> list[NewsItem]:
    items: list[NewsItem] = []
    for channel, pages in (("global-channel", 2), ("a-stock-channel", 3)):
        found = _try(f"见闻{channel}", lambda channel=channel, pages=pages: _wscn_items(channel, pages))
        if found:
            items.extend(found)
    for column in EM_NEWS_COLUMNS:
        found = _try(f"东财快讯{column}", lambda column=column: _em_items(column))
        if found:
            items.extend(found)
    if not items:
        raise RuntimeError("快讯为空")
    return items


def _note_for(status: str, fallback: str, missing: str) -> str | None:
    if status == "fallback":
        return fallback
    if status == "partial":
        return fallback.replace("改用", "部分改用")
    if status == "missing":
        return missing
    return None


def load_indices() -> tuple[list[Quote], str | None]:
    primary = _try("新浪指数", _indices_sina) or []
    secondary: list[Quote] = []
    hero = next((quote for quote in primary if quote.name == "上证指数"), None)
    trade_day = hero.trade_day if hero else ""
    if trade_day:
        primary = [quote for quote in primary if quote.trade_day == trade_day]
    names = {quote.name for quote in primary}
    if not trade_day or any(name not in names for name in INDEX_ORDER):
        secondary = _try("腾讯指数", _indices_qq) or []
    if not trade_day:
        hero = next((quote for quote in secondary if quote.name == "上证指数"), None)
        trade_day = hero.trade_day if hero else ""
    if trade_day:
        primary = [quote for quote in primary if quote.trade_day == trade_day]
        secondary = [quote for quote in secondary if quote.trade_day == trade_day]
    merged, status = combine_quotes(primary, secondary, INDEX_ORDER, required="上证指数")
    return merged, _note_for(status, "指数改用腾讯行情", "指数暂缺")


def load_overseas() -> tuple[list[Quote], list[Quote], list[Quote], str | None]:
    primary = _try("新浪外盘", _overseas_sina)
    overseas, fx, futures = primary if primary else ([], [], [])
    qq_overseas: list[Quote] = []
    qq_fx: list[Quote] = []
    have = {quote.name for quote in overseas}
    fx_names = {quote.name for quote in fx}
    if any(name not in have for name in QQ_FILLABLE) or "在岸人民币" not in fx_names:
        found = _try("腾讯外盘", _overseas_qq)
        if found:
            qq_overseas, qq_fx = found
    merged, status = combine_quotes(overseas, qq_overseas, OVERSEAS_ORDER)
    fx_merged, fx_status = combine_quotes(fx, qq_fx, ("在岸人民币", "离岸人民币"))
    note = _note_for(status, "外盘改用腾讯行情", "")
    if fx_status == "fallback" and not note:
        note = "汇率改用腾讯行情"
    if not merged and not fx_merged:
        note = "外盘暂缺"
    return merged, fx_merged, futures, note or None


def load_sectors() -> tuple[list[SectorMove], list[SectorMove], str, str | None]:
    found = _try("新浪行业", _sectors_sina)
    if found:
        leaders, laggards = found
        return leaders, laggards, "新浪行业", None
    money = _try("新浪行业资金", _board_money)
    if money:
        leaders, laggards, _, _ = money
        return leaders, laggards, "新浪资金涨跌", "板块涨跌改用新浪资金接口"
    return [], [], "新浪行业", "板块涨跌暂缺"


def load_flows() -> tuple[list[SectorFlow], list[SectorFlow], str, str | None]:
    found = _try("东财行业资金", _industry_em)
    if found:
        return found[0], found[1], "东财行业", None
    money = _try("新浪行业资金", _board_money)
    if money:
        _, _, inflow, outflow = money
        return inflow, outflow, "新浪行业", "行业资金改用新浪"
    return [], [], "东财行业", "行业资金暂缺"


def load_capital(trade_day: str | None = None) -> tuple[list[CapitalMix], str | None]:
    eastmoney: list[CapitalMix] = []
    complete = True
    for secid, market in (("1.000001", "沪市"), ("0.399001", "深市")):
        parsed = _try(f"{market}东财资金", lambda secid=secid, market=market: _capital_em(secid, market))
        if parsed and (not trade_day or parsed.trade_day == trade_day):
            eastmoney.append(parsed)
        else:
            complete = False
    if complete and len(eastmoney) == 2:
        return eastmoney, None
    tencent: list[CapitalMix] = []
    for code, market in (("sh000001", "沪市"), ("sz399001", "深市")):
        parsed = _try(f"{market}腾讯资金", lambda code=code, market=market: _capital_qq(code, market))
        if parsed and (not trade_day or parsed.trade_day == trade_day):
            tencent.append(parsed)
    if len(tencent) == 2:
        return tencent, "主力资金改用腾讯行情"
    if eastmoney:
        missing = " / ".join(market for market in ("沪市", "深市") if not any(item.market == market for item in eastmoney))
        return eastmoney, f"{missing}主力资金暂缺"
    if tencent:
        missing = " / ".join(market for market in ("沪市", "深市") if not any(item.market == market for item in tencent))
        return tencent, f"主力资金改用腾讯行情；{missing}主力资金暂缺"
    return [], "主力资金暂缺"


def load_turnover_comparison(trade_date: date) -> TurnoverComparison | None:
    start = (trade_date - timedelta(days=30)).strftime("%Y%m%d")
    end = trade_date.strftime("%Y%m%d")
    url = f"https://q.stock.sohu.com/hisHq?code=zs_000001&start={start}&end={end}&stat=1&order=D&period=d"
    return _try("沪市成交额比较", lambda: parse_sohu_turnover(
        json.loads(fetch_text(url, "https://q.stock.sohu.com/", encoding="gb18030", timeout=12, retries=0)),
        trade_date,
    ))


def load_market() -> MarketData:
    indices, index_note = load_indices()
    notes = [index_note] if index_note else []
    hero = next((quote for quote in indices if quote.name == "上证指数"), None)
    try:
        trade_date = date.fromisoformat(hero.trade_day) if hero and hero.trade_day else None
    except ValueError:
        trade_date = None
    day = trade_date.strftime("%Y%m%d") if trade_date else ""

    jobs = {
        "overseas": load_overseas,
        "sectors": load_sectors,
        "flows": load_flows,
        "capital": lambda: load_capital(trade_date.isoformat()) if trade_date else ([], "主力资金暂缺"),
        "breadth": lambda: _try("涨跌分布", lambda: _breadth(day)) if day else None,
        "cross": lambda: _try("跨境资金", lambda: fetch_cross_border(trade_date.isoformat())) if trade_date else None,
        "news": lambda: _try("快讯", _news),
        "turnover_comparison": lambda: load_turnover_comparison(trade_date) if trade_date else None,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        results = {}
        for future, name in futures.items():
            try:
                results[name] = future.result()
            except Exception as exc:
                print(f"[warn] {name}: {exc}")
                results[name] = None

    overseas = results.get("overseas") or ([], [], [], "外盘暂缺")
    sectors = results.get("sectors") or ([], [], "新浪行业", "板块涨跌暂缺")
    flows = results.get("flows") or ([], [], "东财行业", "行业资金暂缺")
    capital = results.get("capital") or ([], "主力资金暂缺")
    for note in (overseas[3], sectors[3], flows[3], capital[1]):
        if note:
            notes.append(note)
    if results.get("breadth") is None:
        notes.append("情绪暂缺")
    if results.get("cross") is None:
        notes.append("跨境资金暂缺")
    if not results.get("news"):
        notes.append("要闻暂缺")
    if results.get("turnover_comparison") is None:
        notes.append("沪市成交额比较暂缺")

    return MarketData(
        indices=indices,
        overseas=overseas[0],
        fx=overseas[1],
        futures=overseas[2],
        sectors_up=sectors[0],
        sectors_down=sectors[1],
        sector_source=sectors[2],
        capital=capital[0],
        sector_in=flows[0],
        sector_out=flows[1],
        flow_source=flows[2],
        breadth=results.get("breadth"),
        cross=results.get("cross"),
        news=results.get("news") or [],
        notes=notes,
        turnover_comparison=results.get("turnover_comparison"),
    )
