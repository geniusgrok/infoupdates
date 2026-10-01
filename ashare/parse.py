from __future__ import annotations

import json
import re
from datetime import date
from math import isfinite

from common.format import million_to_ccy
from .calendar import previous_trading_day
from .models import Breadth, BreadthBucket, CapitalMix, CrossBorder, Quote, SectorFlow, SectorMove, TurnoverComparison

SINA_RE = re.compile(r'var hq_str_([A-Za-z0-9_]+)="([^"]*)"')

INDEX_NAMES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000016": "上证50",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "sh000688": "科创50",
}

SKIP_SECTORS = {"次新股", "新股", "参股券商", "参股银行"}
ROMAN = str.maketrans({"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V"})


def _clean_label(name: str) -> str:
    return name.translate(ROMAN).strip()


def _optional_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _date_stamp(value: object) -> str:
    stamp = str(value or "")[:10].replace("/", "-")
    try:
        return date.fromisoformat(stamp).isoformat()
    except ValueError:
        return ""

BREADTH_GROUPS: list[tuple[str, str, range]] = [
    ("<-10%", "down", range(-30, -10)),
    ("-10~-7", "down", range(-10, -6)),
    ("-6~-3", "down", range(-6, -2)),
    ("-2~-1", "down", range(-2, 0)),
    ("平", "flat", range(0, 1)),
    ("+1~2", "up", range(1, 3)),
    ("+3~6", "up", range(3, 7)),
    ("+7~10", "up", range(7, 11)),
    (">10%", "up", range(11, 30)),
]


def parse_sina_bundle(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in SINA_RE.finditer(text)}


def _floats(parts: list[str], start: int, count: int) -> list[float | None]:
    values: list[float | None] = []
    for offset in range(count):
        index = start + offset
        values.append(_optional_float(parts[index]) if index < len(parts) else None)
    return values


def parse_cn_index(symbol: str, body: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 10:
        return None
    numbers = _floats(parts, 1, 9)
    open_, prev, last, high, low = numbers[:5]
    amount = numbers[8] if numbers[8] is not None and numbers[8] >= 0 else None
    if last is None or last <= 0:
        return None
    pct = ((last - prev) / prev * 100) if prev else None
    change = (last - prev) if prev is not None else None
    trade_day = ""
    session = ""
    for part in parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            trade_day = _date_stamp(part)
            break
    session = next((part for part in parts if re.fullmatch(r"\d{2}:\d{2}:\d{2}", part)), "")
    return Quote(
        symbol=symbol,
        name=INDEX_NAMES.get(symbol, parts[0] or symbol),
        last=last,
        pct=pct,
        change=change,
        open=open_,
        high=high,
        low=low,
        prev_close=prev,
        amount=amount,
        trade_day=trade_day,
        session=session,
    )


def parse_us_index(symbol: str, body: str, name: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 5:
        return None
    last = _optional_float(parts[1])
    pct = _optional_float(parts[2])
    change = _optional_float(parts[4])
    if last is None or last <= 0:
        return None
    session = next((part for part in parts if "EDT" in part or "EST" in part), parts[3])
    session = _short_us_session(session)
    return Quote(symbol=symbol, name=name, last=last, pct=pct, change=change, session=session)


def _short_us_session(raw: str) -> str:
    match = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", raw)
    if not match:
        return "美股收盘"
    month = {
        "Jan": "01",
        "Feb": "02",
        "Mar": "03",
        "Apr": "04",
        "May": "05",
        "Jun": "06",
        "Jul": "07",
        "Aug": "08",
        "Sep": "09",
        "Oct": "10",
        "Nov": "11",
        "Dec": "12",
    }.get(match.group(1), "")
    if not month:
        return "美股收盘"
    return f"{month}-{int(match.group(2)):02d} 收盘"


def parse_hk_index(symbol: str, body: str, name: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 9:
        return None
    open_, prev, high, low, last, change, pct = _floats(parts, 2, 7)
    if last is None or last <= 0:
        return None
    session = ""
    for part in parts:
        if re.fullmatch(r"\d{4}/\d{2}/\d{2}", part):
            session = part[5:].replace("/", "-")
        elif session and re.fullmatch(r"\d{2}:\d{2}:\d{2}", part):
            session = f"{session} {part[:5]}"
            break
    return Quote(
        symbol=symbol,
        name=name,
        last=last,
        pct=pct,
        change=change,
        open=open_,
        high=high,
        low=low,
        prev_close=prev,
        session=session,
    )


def parse_nikkei(symbol: str, body: str, name: str = "日经225") -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 4:
        return None
    last, change, pct = _floats(parts, 1, 3)
    if last is None or last <= 0:
        return None
    session = ""
    for part in parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            session = part[5:]
            break
    return Quote(symbol=symbol, name=name, last=last, pct=pct, change=change, session=session)


def parse_fx(symbol: str, body: str, name: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    try:
        name_at = next(index for index, part in enumerate(parts) if name in part or "人民币" in part)
    except StopIteration:
        return None
    if name_at < 1:
        return None
    last = _optional_float(parts[name_at - 1])
    if last is None or last <= 0:
        return None
    pct = _optional_float(parts[name_at + 1]) if name_at + 1 < len(parts) else None
    session = parts[0][:5] if re.match(r"\d{2}:\d{2}", parts[0]) else ""
    label = "在岸人民币" if "离岸" not in name and symbol.endswith("cny") else name
    if "离岸" in parts[name_at]:
        label = "离岸人民币"
    return Quote(symbol=symbol, name=label, last=last, pct=pct, session=session)


def parse_sina_industries(text: str, limit: int = 5) -> tuple[list[SectorMove], list[SectorMove]]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return [], []
    payload = json.loads(match.group(0))
    rows: list[SectorMove] = []
    for raw in payload.values():
        parts = str(raw).split(",")
        if len(parts) < 6 or parts[1] in SKIP_SECTORS:
            continue
        pct = _optional_float(parts[5])
        if pct is None:
            continue
        leader = parts[12] if len(parts) > 12 else ""
        if leader.startswith(("*ST", "ST")):
            leader = ""
        rows.append(SectorMove(name=_clean_label(parts[1]), pct=pct, leader=leader))
    return _split_moves(rows, limit)


def parse_fflow_line(market: str, line: str) -> CapitalMix | None:
    parts = line.split(",")
    if len(parts) < 6:
        return None
    main, small, mid, large, super_order = _floats(parts, 1, 5)
    if any(value is None for value in (main, small, mid, large, super_order)):
        return None
    return CapitalMix(
        market=market,
        main=main,
        super_order=super_order,
        large=large,
        mid=mid,
        small=small,
        trade_day=_date_stamp(parts[0]),
    )


def parse_fenbu(items: list[dict], limit_up: int | None = None, limit_down: int | None = None) -> Breadth:
    counts: dict[int, int] = {}
    for item in items:
        for key, value in item.items():
            counts[int(key)] = counts.get(int(key), 0) + int(value)
    up = sum(value for key, value in counts.items() if key > 0)
    down = sum(value for key, value in counts.items() if key < 0)
    flat = counts.get(0, 0)
    buckets: list[BreadthBucket] = []
    for label, side, span in BREADTH_GROUPS:
        buckets.append(BreadthBucket(label, sum(counts.get(key, 0) for key in span), side))
    return Breadth(
        up=up,
        down=down,
        flat=flat,
        limit_up=limit_up,
        limit_down=limit_down,
        buckets=buckets,
    )


def parse_industry_flows(payload: dict, limit: int = 5) -> tuple[list[SectorFlow], list[SectorFlow]]:
    data = payload.get("data")
    diff = data.get("diff") if isinstance(data, dict) else []
    if not isinstance(diff, list):
        return [], []
    rows: list[SectorFlow] = []
    for item in diff:
        if not isinstance(item, dict):
            continue
        net = _optional_float(item.get("f62"))
        if net is None:
            continue
        rows.append(SectorFlow(code=str(item.get("f12") or ""), name=_clean_label(str(item.get("f14") or "")), net=net))
    rows.sort(key=lambda item: item.net, reverse=True)
    inflow = [item for item in rows if item.net > 0][:limit]
    outflow = [item for item in rows if item.net < 0][-limit:]
    outflow.sort(key=lambda item: item.net)
    return inflow, outflow


def parse_cross_border(north_row: dict | None, south_rows: dict[str, dict]) -> CrossBorder:
    north_turnover = None
    north_day = _date_stamp((north_row or {}).get("TRADE_DATE"))
    south_day = _date_stamp((south_rows.get("006") or {}).get("TRADE_DATE"))
    trade_day = south_day or north_day
    if north_row:
        amount = _optional_float(north_row.get("NF_DEAL_AMT"))
        if amount is not None and amount >= 0 and (not trade_day or north_day == trade_day):
            north_turnover = million_to_ccy(amount)

    def net_of(kind: str) -> float | None:
        row = south_rows.get(kind) or {}
        if south_day and _date_stamp(row.get("TRADE_DATE")) != south_day:
            return None
        return million_to_ccy(_optional_float(row.get("NET_DEAL_AMT")))

    return CrossBorder(
        north_turnover=north_turnover,
        south_net=net_of("006"),
        south_sh=net_of("002"),
        south_sz=net_of("004"),
        trade_day=trade_day,
    )


TENCENT_RE = re.compile(r'v_([A-Za-z0-9]+)="([^"]*)"')

INDEX_ORDER = (
    "上证指数",
    "深证成指",
    "创业板指",
    "沪深300",
    "上证50",
    "中证500",
    "中证1000",
    "科创50",
)
OVERSEAS_ORDER = ("道琼斯", "纳斯达克", "标普500", "日经225", "韩国KOSPI", "韩国KOSDAQ", "恒生指数", "恒生科技")


def parse_tencent_bundle(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in TENCENT_RE.finditer(text)}


def _stamp_index(parts: list[str]) -> int | None:
    for index, part in enumerate(parts):
        if re.fullmatch(r"\d{14}", part) or re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", part):
            return index
    return None


def _split_stamp(stamp: str, kind: str) -> tuple[str, str]:
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})\d{2}", stamp)
    if compact:
        day = _date_stamp(f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}")
        if not day:
            return "", ""
        clock = f"{compact.group(4)}:{compact.group(5)}"
        if kind == "us":
            return day, f"{compact.group(2)}-{compact.group(3)} 收盘"
        if kind == "hk":
            return day, f"{compact.group(2)}-{compact.group(3)} {clock}"
        if kind == "fx":
            return day, clock
        return day, clock
    match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+(\d{2}:\d{2}))?", stamp)
    if not match:
        return "", ""
    day = _date_stamp(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    if not day:
        return "", ""
    clock = match.group(4) or ""
    if kind == "us":
        return day, f"{match.group(2)}-{match.group(3)} 收盘"
    if kind == "hk":
        label = f"{match.group(2)}-{match.group(3)}"
        if clock:
            label += f" {clock}"
        return day, label
    if kind == "fx":
        return day, clock
    return day, clock


def parse_tencent_quote(symbol: str, body: str, name: str, kind: str = "cn") -> Quote | None:
    if not body:
        return None
    parts = body.split("~")
    if len(parts) < 6:
        return None
    last = _optional_float(parts[3])
    if last is None or last <= 0:
        return None
    prev = _optional_float(parts[4])
    open_ = _optional_float(parts[5])
    change = pct = high = low = None
    trade_day = ""
    session = ""
    stamp_at = _stamp_index(parts)
    if stamp_at is not None:
        trade_day, session = _split_stamp(parts[stamp_at], kind)
        change, pct, high, low = _floats(parts, stamp_at + 1, 4)
    if pct is None and prev:
        pct = (last - prev) / prev * 100
    if change is None and prev is not None:
        change = last - prev
    amount = None
    if kind == "cn" and len(parts) > 37:
        wan = _optional_float(parts[37])
        if wan is not None and wan >= 0:
            amount = wan * 10000
    return Quote(
        symbol=symbol,
        name=name,
        last=last,
        pct=pct,
        change=change,
        open=open_,
        high=high,
        low=low,
        prev_close=prev,
        amount=amount,
        session=session,
        trade_day=trade_day,
    )


def parse_tencent_fx(symbol: str, body: str, name: str) -> Quote | None:
    if not body:
        return None
    parts = body.split("~")
    if len(parts) < 8:
        return None
    last = _optional_float(parts[3])
    if last is None or last <= 0:
        return None
    stamp_at = _stamp_index(parts)
    pct = None
    session = ""
    trade_day = ""
    if stamp_at is not None:
        trade_day, session = _split_stamp(parts[stamp_at], "fx")
        # Tencent FX puts the price change and percentage at offsets 7 and 8
        # after the timestamp; missing fields must not shift later metrics.
        pct = _floats(parts, stamp_at + 8, 1)[0]
    return Quote(symbol=symbol, name=name, last=last, pct=pct, session=session, trade_day=trade_day)


def parse_tencent_capital(market: str, payload: dict) -> CapitalMix | None:
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    flow = root.get("todayFundFlow")
    if not isinstance(flow, dict):
        return None
    values = [_optional_float(flow.get(key)) for key in ("mainNetIn", "superFlow", "bigFlow", "normalFlow", "smallFlow")]
    if any(value is None for value in values):
        return None
    trend = root.get("todayFundTrend")
    minutes = trend.get("minList") if isinstance(trend, dict) else []
    if not isinstance(minutes, list):
        minutes = []
    stamps = [str(row.get("time") or "") for row in minutes if isinstance(row, dict)]
    days = [_date_stamp(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}") for stamp in stamps if re.fullmatch(r"\d{12,14}", stamp)]
    trade_day = max((day for day in days if day), default="")
    return CapitalMix(market, *values, trade_day=trade_day)


def parse_sina_board_money(text: str, limit: int = 5) -> tuple[list[SectorMove], list[SectorMove], list[SectorFlow], list[SectorFlow]]:
    payload = json.loads(text)
    moves: list[SectorMove] = []
    flows: list[SectorFlow] = []
    if not isinstance(payload, list):
        return [], [], [], []
    for row in payload:
        if not isinstance(row, dict):
            continue
        name = _clean_label(str(row.get("name") or ""))
        if not name or name in SKIP_SECTORS:
            continue
        pct = _optional_float(row.get("avg_changeratio"))
        net = _optional_float(row.get("netamount"))
        if pct is None or net is None:
            continue
        pct *= 100
        leader = str(row.get("ts_name") or "")
        if leader.startswith(("*ST", "ST")):
            leader = ""
        moves.append(SectorMove(name=name, pct=pct, leader=leader))
        flows.append(SectorFlow(code=str(row.get("category") or ""), name=name, net=net))
    ranked = sorted(flows, key=lambda item: item.net, reverse=True)
    inflow = [item for item in ranked if item.net > 0][:limit]
    outflow = sorted((item for item in ranked if item.net < 0), key=lambda item: item.net)[:limit]
    leaders, laggards = _split_moves(moves, limit)
    return leaders, laggards, inflow, outflow


def _split_moves(rows: list[SectorMove], limit: int) -> tuple[list[SectorMove], list[SectorMove]]:
    ordered = sorted(rows, key=lambda item: item.pct, reverse=True)
    leaders = [item for item in ordered if item.pct > 0][:limit]
    laggards = [item for item in reversed(ordered) if item.pct < 0][:limit]
    return leaders, laggards


def merge_quotes(primary: list[Quote], secondary: list[Quote], order: tuple[str, ...] = ()) -> list[Quote]:
    chosen: dict[str, Quote] = {}
    for quote in secondary:
        if isfinite(quote.last) and quote.last > 0:
            chosen[quote.name] = quote
    for quote in primary:
        if isfinite(quote.last) and quote.last > 0:
            chosen[quote.name] = quote
    if not order:
        return list(chosen.values())
    ordered = [chosen[name] for name in order if name in chosen]
    ordered.extend(quote for name, quote in chosen.items() if name not in order)
    return ordered


def combine_quotes(
    primary: list[Quote] | None,
    secondary: list[Quote] | None,
    order: tuple[str, ...] = (),
    required: str | None = None,
) -> tuple[list[Quote], str]:
    first = primary or []
    second = secondary or []
    merged = merge_quotes(first, second, order)
    if required and not any(item.name == required and item.last > 0 for item in merged):
        return merged, "missing"
    if not first and second:
        return merged, "fallback"
    names = {item.name for item in first}
    if any(item.name not in names for item in merged):
        return merged, "partial"
    return merged, "primary"


def parse_sohu_turnover(payload: object, trade_date: date) -> TurnoverComparison | None:
    """Read the Shanghai index's two consecutive sessions from one Sohu response.

    Sohu column 7 is volume in lots; column 8 is RMB amount in ten thousands.
    Keep missing amounts attached to their dates so an older session cannot be
    silently substituted for the preceding trading day.
    """
    if not isinstance(payload, list):
        return None
    series = next((item for item in payload if isinstance(item, dict) and item.get("code") == "zs_000001"), None)
    if not series or series.get("status") != 0 or not isinstance(series.get("hq"), list):
        return None
    amounts: dict[date, float | None] = {}
    for row in series["hq"]:
        if not isinstance(row, list) or not row:
            continue
        stamp = _date_stamp(row[0])
        if not stamp:
            continue
        day = date.fromisoformat(stamp)
        amount = _optional_float(row[8]) if len(row) > 8 else None
        if amount is not None and amount <= 0:
            amount = None
        if day in amounts and amounts[day] != amount:
            return None
        amounts[day] = amount
    return _turnover_comparison(amounts, trade_date, "搜狐", scale=10_000)


def parse_eastmoney_turnover(payload: object, trade_date: date) -> TurnoverComparison | None:
    """Eastmoney daily K-line column 6 is RMB amount; column 5 is volume."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("code") != "000001" or data.get("market") != 1:
        return None
    rows = data.get("klines")
    if not isinstance(rows, list):
        return None
    amounts: dict[date, float | None] = {}
    for row in rows:
        if not isinstance(row, str):
            continue
        fields = row.split(",")
        stamp = _date_stamp(fields[0])
        if not stamp:
            continue
        day = date.fromisoformat(stamp)
        amount = _optional_float(fields[6]) if len(fields) > 6 else None
        if amount is not None and amount <= 0:
            amount = None
        if day in amounts and amounts[day] != amount:
            return None
        amounts[day] = amount
    return _turnover_comparison(amounts, trade_date, "东财")


def _turnover_comparison(amounts: dict[date, float | None], trade_date: date,
                         source: str, scale: float = 1) -> TurnoverComparison | None:
    try:
        previous_date = previous_trading_day(trade_date)
    except ValueError:
        return None
    current, previous = amounts.get(trade_date), amounts.get(previous_date)
    if current is None or previous is None:
        return None
    return TurnoverComparison(trade_date, previous_date, current * scale, previous * scale, source)
