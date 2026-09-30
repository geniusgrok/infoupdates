from __future__ import annotations

import json
import re
from .format import million_to_ccy
from .models import Breadth, BreadthBucket, CapitalMix, CrossBorder, Quote, SectorFlow, SectorMove

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

BREADTH_GROUPS: list[tuple[str, str, range]] = [
    ("跌停", "down", range(-30, -10)),
    ("-10~-7", "down", range(-10, -6)),
    ("-6~-3", "down", range(-6, -2)),
    ("-2~-1", "down", range(-2, 0)),
    ("平", "flat", range(0, 1)),
    ("+1~2", "up", range(1, 3)),
    ("+3~6", "up", range(3, 7)),
    ("+7~10", "up", range(7, 11)),
    ("涨停", "up", range(11, 30)),
]


def parse_sina_bundle(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in SINA_RE.finditer(text)}


def _floats(parts: list[str], start: int, count: int) -> list[float | None]:
    values: list[float | None] = []
    for offset in range(count):
        index = start + offset
        if index >= len(parts) or parts[index] in {"", "--", "-"}:
            values.append(None)
            continue
        try:
            values.append(float(parts[index]))
        except ValueError:
            values.append(None)
    return values


def parse_cn_index(symbol: str, body: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 10:
        return None
    numbers = _floats(parts, 1, 9)
    open_, prev, last, high, low = numbers[:5]
    amount = numbers[8]
    if last is None or last <= 0:
        return None
    pct = ((last - prev) / prev * 100) if prev else None
    change = (last - prev) if prev is not None else None
    trade_day = ""
    for part in parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part):
            trade_day = part
            break
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
    )


def parse_us_index(symbol: str, body: str, name: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 5:
        return None
    try:
        last = float(parts[1])
        pct = float(parts[2])
        change = float(parts[4])
    except ValueError:
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
    try:
        open_ = float(parts[2])
        prev = float(parts[3])
        high = float(parts[4])
        low = float(parts[5])
        last = float(parts[6])
        change = float(parts[7])
        pct = float(parts[8])
    except ValueError:
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
    try:
        last = float(parts[1])
        change = float(parts[2])
        pct = float(parts[3])
    except ValueError:
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
    try:
        last = float(parts[name_at - 1])
    except ValueError:
        return None
    pct = None
    if name_at + 1 < len(parts):
        try:
            pct = float(parts[name_at + 1])
        except ValueError:
            pct = None
    session = parts[0][:5] if re.match(r"\d{2}:\d{2}", parts[0]) else ""
    label = "在岸人民币" if "离岸" not in name and symbol.endswith("cny") else name
    if "离岸" in parts[name_at]:
        label = "离岸人民币"
    return Quote(symbol=symbol, name=label, last=last, pct=pct, session=session)


def parse_cme_future(symbol: str, body: str, fallback: str) -> Quote | None:
    if not body:
        return None
    parts = body.split(",")
    if len(parts) < 9:
        return None
    try:
        last = float(parts[0])
    except ValueError:
        return None
    prev = None
    for index in (8, 7):
        try:
            candidate = float(parts[index])
        except ValueError:
            continue
        if candidate > 0 and abs(candidate - last) / candidate < 0.2:
            prev = candidate
            break
    pct = ((last - prev) / prev * 100) if prev else None
    change = (last - prev) if prev is not None else None
    name = parts[13].strip() if len(parts) > 13 and parts[13].strip() else fallback
    clock = parts[6][:5] if len(parts) > 6 and re.match(r"\d{2}:\d{2}", parts[6]) else ""
    session = f"夜盘 {clock}".strip()
    return Quote(symbol=symbol, name=name, last=last, pct=pct, change=change, prev_close=prev, session=session)


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
        try:
            pct = float(parts[5])
        except ValueError:
            continue
        leader = parts[12] if len(parts) > 12 else ""
        if leader.startswith(("*ST", "ST")):
            leader = ""
        rows.append(SectorMove(name=_clean_label(parts[1]), pct=pct, leader=leader))
    rows.sort(key=lambda item: item.pct, reverse=True)
    if not rows:
        return [], []
    return rows[:limit], list(reversed(rows[-limit:]))


def parse_fflow_line(market: str, line: str) -> CapitalMix | None:
    parts = line.split(",")
    if len(parts) < 6:
        return None
    try:
        main, small, mid, large, super_order = (float(parts[index]) for index in range(1, 6))
    except ValueError:
        return None
    return CapitalMix(
        market=market,
        main=main,
        super_order=super_order,
        large=large,
        mid=mid,
        small=small,
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
        limit_up=limit_up if limit_up is not None else counts.get(11, 0),
        limit_down=limit_down if limit_down is not None else counts.get(-11, 0),
        buckets=buckets,
    )


def parse_industry_flows(payload: dict, limit: int = 5) -> tuple[list[SectorFlow], list[SectorFlow]]:
    diff = ((payload.get("data") or {}).get("diff")) or []
    rows: list[SectorFlow] = []
    for item in diff:
        try:
            net = float(item["f62"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(SectorFlow(code=str(item.get("f12") or ""), name=_clean_label(str(item.get("f14") or "")), net=net))
    rows.sort(key=lambda item: item.net, reverse=True)
    inflow = [item for item in rows if item.net > 0][:limit]
    outflow = [item for item in rows if item.net < 0][-limit:]
    outflow.sort(key=lambda item: item.net)
    return inflow, outflow


def parse_cross_border(north_row: dict | None, south_rows: dict[str, dict]) -> CrossBorder:
    north_turnover = None
    if north_row and north_row.get("NF_DEAL_AMT") is not None:
        north_turnover = million_to_ccy(float(north_row["NF_DEAL_AMT"]))

    def net_of(kind: str) -> float | None:
        row = south_rows.get(kind) or {}
        amount = row.get("NET_DEAL_AMT")
        if amount is None:
            return None
        return million_to_ccy(float(amount))

    return CrossBorder(
        north_turnover=north_turnover,
        south_net=net_of("006"),
        south_sh=net_of("002"),
        south_sz=net_of("004"),
    )


QQ_RE = re.compile(r'v_([A-Za-z0-9]+)="([^"]*)"')

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


def parse_qq_bundle(text: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in QQ_RE.finditer(text)}


def _stamp_index(parts: list[str]) -> int | None:
    for index, part in enumerate(parts):
        if re.fullmatch(r"\d{14}", part) or re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", part):
            return index
    return None


def _split_stamp(stamp: str, kind: str) -> tuple[str, str]:
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})\d{2}", stamp)
    if compact:
        day = f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}"
        clock = f"{compact.group(4)}:{compact.group(5)}"
        if kind == "us":
            return day, f"{compact.group(2)}-{compact.group(3)} 收盘"
        if kind == "hk":
            return day, f"{compact.group(2)}-{compact.group(3)} {clock}"
        if kind == "fx":
            return day, clock
        return day, ""
    match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+(\d{2}:\d{2}))?", stamp)
    if not match:
        return "", ""
    day = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
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
    return day, ""


def parse_qq_quote(symbol: str, body: str, name: str, kind: str = "cn") -> Quote | None:
    if not body:
        return None
    parts = body.split("~")
    if len(parts) < 6:
        return None
    try:
        last = float(parts[3])
    except ValueError:
        return None
    if last <= 0:
        return None
    prev = _optional_float(parts[4])
    open_ = _optional_float(parts[5])
    change = pct = high = low = None
    trade_day = ""
    session = ""
    stamp_at = _stamp_index(parts)
    if stamp_at is not None:
        trade_day, session = _split_stamp(parts[stamp_at], kind)
        numbers: list[float] = []
        for part in parts[stamp_at + 1 :]:
            try:
                numbers.append(float(part))
            except ValueError:
                if numbers:
                    break
                continue
            if len(numbers) >= 4:
                break
        if numbers:
            change = numbers[0]
        if len(numbers) > 1:
            pct = numbers[1]
        if len(numbers) > 2:
            high = numbers[2]
        if len(numbers) > 3:
            low = numbers[3]
    if pct is None and prev:
        pct = (last - prev) / prev * 100
    if change is None and prev is not None:
        change = last - prev
    amount = None
    if kind == "cn" and len(parts) > 37:
        wan = _optional_float(parts[37])
        if wan and wan > 1000:
            amount = wan * 10000
    if kind == "cn":
        session = ""
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


def _optional_float(value: str) -> float | None:
    if value in {"", "--", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_qq_fx(symbol: str, body: str, name: str) -> Quote | None:
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
        small: list[float] = []
        for part in parts[stamp_at + 1 :]:
            value = _optional_float(part)
            if value is None:
                continue
            if abs(value) < 2:
                small.append(value)
            if len(small) >= 2:
                break
        if len(small) >= 2:
            pct = small[1]
        elif small:
            pct = small[0]
    return Quote(symbol=symbol, name=name, last=last, pct=pct, session=session, trade_day=trade_day)


def parse_qq_capital(market: str, payload: dict) -> CapitalMix | None:
    flow = ((payload.get("data") or {}).get("todayFundFlow")) or payload.get("todayFundFlow") or {}
    try:
        return CapitalMix(
            market=market,
            main=float(flow["mainNetIn"]),
            super_order=float(flow["superFlow"]),
            large=float(flow["bigFlow"]),
            mid=float(flow["normalFlow"]),
            small=float(flow["smallFlow"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_qq_spark(payload: dict) -> list[float]:
    node = ((payload.get("data") or {}).get("sh000001")) or {}
    rows = node.get("day") or node.get("qfqday") or []
    closes: list[float] = []
    for row in rows:
        try:
            closes.append(float(row[2]))
        except (IndexError, TypeError, ValueError):
            continue
    return closes


def parse_sina_board_money(text: str, limit: int = 5) -> tuple[list[SectorMove], list[SectorMove], list[SectorFlow], list[SectorFlow]]:
    payload = json.loads(text)
    moves: list[SectorMove] = []
    flows: list[SectorFlow] = []
    for row in payload:
        name = _clean_label(str(row.get("name") or ""))
        if not name or name in SKIP_SECTORS:
            continue
        try:
            pct = float(row["avg_changeratio"]) * 100
            net = float(row["netamount"])
        except (KeyError, TypeError, ValueError):
            continue
        leader = str(row.get("ts_name") or "")
        if leader.startswith(("*ST", "ST")):
            leader = ""
        moves.append(SectorMove(name=name, pct=pct, leader=leader))
        flows.append(SectorFlow(code=str(row.get("category") or ""), name=name, net=net))
    moves.sort(key=lambda item: item.pct, reverse=True)
    ranked = sorted(flows, key=lambda item: item.net, reverse=True)
    inflow = [item for item in ranked if item.net > 0][:limit]
    outflow = sorted((item for item in ranked if item.net < 0), key=lambda item: item.net)[:limit]
    leaders = moves[:limit]
    laggards = list(reversed(moves[-limit:])) if moves else []
    return leaders, laggards, inflow, outflow


def merge_quotes(primary: list[Quote], secondary: list[Quote], order: tuple[str, ...] = ()) -> list[Quote]:
    chosen: dict[str, Quote] = {}
    for quote in secondary:
        if quote.last > 0:
            chosen[quote.name] = quote
    for quote in primary:
        if quote.last > 0:
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


def parse_spark_closes(payload: list[dict]) -> list[float]:
    closes: list[float] = []
    for row in payload:
        try:
            closes.append(float(row["close"]))
        except (KeyError, TypeError, ValueError):
            continue
    return closes

