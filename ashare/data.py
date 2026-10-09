from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime

from . import sources
from .calendar import HOLIDAY_RANGES, is_trading_day, latest_quote_date, previous_trading_day
from .models import CST, CapitalMix, MarketData, Quote, SectorFlow, SectorMove, TurnoverComparison, china_time
from .parse import INDEX_NAMES, INDEX_ORDER, OVERSEAS_ORDER, combine_quotes

TENCENT_OVERSEAS = ("道琼斯", "纳斯达克", "标普500", "恒生指数", "恒生科技")


def _note_for(status: str, fallback: str, missing: str) -> str | None:
    if status == "fallback":
        return fallback
    if status == "partial":
        return fallback.replace("改用", "部分改用")
    if status == "missing":
        return missing
    return None


def _daily_indices(day: date, observed_at: datetime) -> list[Quote]:
    """Completed session bars for the reference day when the live feed has already rolled."""
    from .history import load_daily

    try:
        start = previous_trading_day(day)
    except ValueError:
        start = day
    quotes: list[Quote] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(load_daily, symbol, INDEX_NAMES[symbol], start, day, observed_at, required_day=day)
            for symbol in sources.INDEX_SYMBOLS
        ]
        for future in futures:
            try:
                rows = future.result()
            except (OSError, ValueError, TypeError, RuntimeError):
                continue
            match = next((row for row in rows if row.trade_day == day.isoformat() and row.last > 0), None)
            if match:
                quotes.append(match)
    if not any(quote.name == "上证指数" for quote in quotes):
        raise RuntimeError("指数日线没有上证")
    return quotes


def load_indices(now: datetime | None = None) -> tuple[list[Quote], str | None]:
    now = china_time(now or datetime.now(CST))
    calendar_note = None
    try:
        expected = latest_quote_date(now)
    except ValueError as exc:
        expected, calendar_note = None, str(exc)

    def usable(quote: Quote) -> bool:
        try:
            day = date.fromisoformat(quote.trade_day)
            return day <= (expected or now.date()) and (day.year not in HOLIDAY_RANGES or is_trading_day(day))
        except ValueError:
            return False

    primary = [replace(quote, source="新浪") for quote in (sources.optional("新浪指数", sources.sina_indices) or []) if usable(quote)]
    secondary: list[Quote] = []
    hero = next((quote for quote in primary if quote.name == "上证指数"), None)
    trade_day = hero.trade_day if hero else ""
    if trade_day:
        primary = [quote for quote in primary if quote.trade_day == trade_day]
    names = {quote.name for quote in primary}
    if not trade_day or any(name not in names for name in INDEX_ORDER) or (expected and trade_day < expected.isoformat()):
        secondary = [replace(quote, source="腾讯") for quote in (sources.optional("腾讯指数", sources.tencent_indices) or []) if usable(quote)]
        hero = next((quote for quote in secondary if quote.name == "上证指数"), None)
        if hero and (not trade_day or hero.trade_day > trade_day):
            trade_day = hero.trade_day
    if trade_day:
        primary = [quote for quote in primary if quote.trade_day == trade_day]
        secondary = [quote for quote in secondary if quote.trade_day == trade_day]
    merged, status = combine_quotes(primary, secondary, INDEX_ORDER, required="上证指数")
    aligned = any(
        quote.name == "上证指数" and quote.last > 0 and (not expected or quote.trade_day == expected.isoformat())
        for quote in merged
    )
    if expected and not aligned:
        historical = sources.optional("指数日线", lambda: _daily_indices(expected, now))
        if historical:
            return historical, "；".join(note for note in ("指数改用日线", calendar_note) if note)
    notes = [_note_for(status, "指数改用腾讯行情", "指数暂缺"), calendar_note]
    if merged and expected and trade_day < expected.isoformat():
        notes.append(f"指数行情仅到{trade_day}，最新行情日{expected.isoformat()}暂缺")
    return merged, "；".join(note for note in notes if note) or None


def load_overseas() -> tuple[list[Quote], list[Quote], str | None]:
    primary = sources.optional("新浪外盘", sources.sina_overseas)
    overseas, fx = primary if primary else ([], [])
    overseas = [replace(quote, source="新浪") for quote in overseas]
    fx = [replace(quote, source="新浪") for quote in fx]
    tencent_overseas: list[Quote] = []
    tencent_fx: list[Quote] = []
    have = {quote.name for quote in overseas}
    fx_names = {quote.name for quote in fx}
    if any(name not in have for name in TENCENT_OVERSEAS) or "在岸人民币" not in fx_names:
        found = sources.optional("腾讯外盘", sources.tencent_overseas)
        if found:
            tencent_overseas, tencent_fx = found
            tencent_overseas = [replace(quote, source="腾讯") for quote in tencent_overseas]
            tencent_fx = [replace(quote, source="腾讯") for quote in tencent_fx]
    merged, status = combine_quotes(overseas, tencent_overseas, OVERSEAS_ORDER)
    fx_merged, fx_status = combine_quotes(fx, tencent_fx, ("在岸人民币", "离岸人民币"))
    note = _note_for(status, "外盘改用腾讯行情", "")
    if fx_status == "fallback" and not note:
        note = "汇率改用腾讯行情"
    if not merged and not fx_merged:
        note = "外盘暂缺"
    return merged, fx_merged, note or None


def load_sectors() -> tuple[list[SectorMove], list[SectorMove], str, str | None]:
    found = sources.optional("新浪行业", sources.sina_sectors)
    if found:
        leaders, laggards = found
        return leaders, laggards, "新浪行业", None
    money = sources.optional("新浪行业资金", sources.sina_sector_money)
    if money:
        leaders, laggards, _, _ = money
        return leaders, laggards, "新浪资金涨跌", "板块涨跌改用新浪资金接口"
    return [], [], "新浪行业", "板块涨跌暂缺"


def load_flows() -> tuple[list[SectorFlow], list[SectorFlow], str, str | None]:
    found = sources.optional("东财行业资金", sources.eastmoney_flows)
    if found:
        return found[0], found[1], "东财行业", None
    money = sources.optional("新浪行业资金", sources.sina_sector_money)
    if money:
        _, _, inflow, outflow = money
        return inflow, outflow, "新浪行业", "行业资金改用新浪"
    tencent = sources.optional("腾讯行业资金", sources.tencent_flows)
    if tencent:
        return tencent[0], tencent[1], "腾讯行业", "行业资金改用腾讯"
    return [], [], "东财行业", "行业资金暂缺"


def load_capital(trade_day: str | None = None) -> tuple[list[CapitalMix], str | None]:
    eastmoney: list[CapitalMix] = []
    complete = True
    for secid, market in (("1.000001", "沪市"), ("0.399001", "深市")):
        parsed = sources.optional(
            f"{market}东财资金",
            lambda secid=secid, market=market: sources.eastmoney_capital(secid, market, trade_day or ""),
        )
        if parsed and (not trade_day or parsed.trade_day == trade_day):
            eastmoney.append(parsed)
        else:
            complete = False
    if complete and len(eastmoney) == 2:
        return eastmoney, None
    tencent: list[CapitalMix] = []
    for code, market in (("sh000001", "沪市"), ("sz399001", "深市")):
        parsed = sources.optional(
            f"{market}腾讯资金",
            lambda code=code, market=market: sources.tencent_capital(code, market, trade_day or ""),
        )
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
    for label, loader in (
        ("搜狐成交额", sources.sohu_turnover),
        ("东财成交额", sources.eastmoney_turnover),
        ("腾讯成交额", sources.tencent_turnover),
    ):
        comparison = sources.optional(label, lambda loader=loader: loader(trade_date))
        if comparison is not None:
            return comparison
    return None


def _anchor_day(hero: Quote | None, now: datetime) -> date | None:
    if hero and hero.trade_day:
        try:
            return date.fromisoformat(hero.trade_day)
        except ValueError:
            pass
    try:
        return latest_quote_date(now)
    except ValueError:
        return None


def load_market(now: datetime | None = None) -> MarketData:
    now = china_time(now or datetime.now(CST))
    indices, index_note = load_indices(now)
    notes = [index_note] if index_note else []
    hero = next((quote for quote in indices if quote.name == "上证指数"), None)
    trade_date = _anchor_day(hero, now)
    day = trade_date.strftime("%Y%m%d") if trade_date else ""

    jobs = {
        "overseas": load_overseas,
        "sectors": load_sectors,
        "flows": load_flows,
        "capital": lambda: load_capital(trade_date.isoformat()) if trade_date else ([], "主力资金暂缺"),
        "breadth": lambda: sources.optional("涨跌分布", lambda: sources.eastmoney_breadth(day)) if day else None,
        "cross": lambda: sources.optional("跨境资金", lambda: sources.eastmoney_cross_border(trade_date.isoformat())) if trade_date else None,
        "news": lambda: sources.optional("快讯", sources.news),
        "turnover_comparison": lambda: load_turnover_comparison(trade_date) if trade_date else None,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {name: pool.submit(sources.optional, name, fn) for name, fn in jobs.items()}
        results = {}
        for name, future in pending.items():
            try:
                results[name] = future.result()
            except Exception as exc:
                # Optional sources must not prevent the remaining report from rendering.
                results[name] = None
                notes.append(f"{name}数据暂缺（{type(exc).__name__}）")

    overseas = results.get("overseas") or ([], [], "外盘暂缺")
    sectors = results.get("sectors") or ([], [], "新浪行业", "板块涨跌暂缺")
    flows = results.get("flows") or ([], [], "东财行业", "行业资金暂缺")
    capital = results.get("capital") or ([], "主力资金暂缺")
    for note in (overseas[2], sectors[3], flows[3], capital[1]):
        if note:
            notes.append(note)
    if results.get("breadth") is None:
        notes.append("情绪暂缺")
    if results.get("cross") is None:
        notes.append("跨境资金暂缺")
    if not results.get("news"):
        notes.append("要闻暂缺")
    comparison = results.get("turnover_comparison")
    if comparison is None:
        notes.append("沪市成交额比较暂缺")
    elif comparison.source != "搜狐":
        notes.append(f"沪市成交额比较改用{comparison.source}")

    return MarketData(
        indices=indices,
        overseas=overseas[0],
        fx=overseas[1],
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
