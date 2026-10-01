from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timezone

from common import cache

from .calendar import (
    edition_date, extended_close, is_trading_day, last_completed_session,
    overnight_window, session_close, session_open,
)
from .models import (
    FUTURE_NAMES, INDEX_NAMES, MACRO_NAMES, MEGA_NAMES, QUOTE_BAGS, SECTOR_NAMES,
    MarketData, Quote, clean_quote, finite_number, new_york_time, quote_clock,
)

NAMES = INDEX_NAMES | FUTURE_NAMES | MEGA_NAMES | SECTOR_NAMES | MACRO_NAMES | {"SPY": "标普500ETF"}
TTL = {"quotes": 900, "completed": 345600, "premarket": 57600, "postmarket": 57600, "overnight": 300}
LABELS = {"quotes": "报价", "completed": "收盘", "premarket": "盘前", "postmarket": "盘后", "overnight": "夜盘"}


def _record(quote: Quote) -> dict:
    record = asdict(quote)
    for field in ("asof", "observed_at"):
        value = record[field]
        record[field] = value.astimezone(timezone.utc).isoformat() if value is not None else None
    value = record["previous_date"]
    record["previous_date"] = value.isoformat() if value is not None else None
    return record


def _restore(record: dict, symbol: str, bag: str, now: datetime) -> Quote | None:
    try:
        values = dict(record)
        for field in ("asof", "observed_at"):
            value = values.get(field)
            values[field] = datetime.fromisoformat(value) if value is not None else None
            stamp = values[field]
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None or stamp.timestamp() > now.timestamp()):
                return None
        value = values.get("previous_date")
        values["previous_date"] = date.fromisoformat(value) if value is not None else None
        for field in ("last", "pct", "previous_close", "volume", "previous_volume"):
            value = values.get(field)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or finite_number(value) is None
                or (field in {"last", "previous_close"} and value <= 0)
                or (field in {"volume", "previous_volume"} and value < 0)
            ):
                return None
        quote = Quote(**values)
        if not isinstance(quote.name, str) or not quote.name or not isinstance(quote.unit, str):
            return None
        if not isinstance(quote.cached, bool) or (quote.delay_minutes is not None and (
            not isinstance(quote.delay_minutes, int) or isinstance(quote.delay_minutes, bool) or quote.delay_minutes < 0
        )):
            return None
        quote = clean_quote(quote, symbol, now, allow_snapshot=bag == "overnight")
        if quote is None:
            return None
        quote.cached = True
        return quote
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def _current(quote: Quote, bag: str, now: datetime) -> bool:
    stamp = quote_clock(quote)
    if stamp is None:
        return False
    day = quote.trade_date
    latest = last_completed_session(now)
    if bag == "completed":
        return quote.session == "regular" and day == latest and stamp >= session_close(latest)
    if bag == "premarket":
        return (
            quote.session == bag and day == now.date() and is_trading_day(now.date())
            and time(4) <= stamp.time().replace(tzinfo=None) < time(9, 30)
            and now < session_open(now.date())
        )
    if bag == "postmarket":
        ending_bar = quote.source == "Futu" and stamp == extended_close(latest)
        return (
            quote.session == bag and day == latest
            and session_close(latest) <= stamp and (stamp < extended_close(latest) or ending_bar)
            and 0 <= now.timestamp() - stamp.timestamp() <= TTL[bag]
        )
    if bag == "overnight":
        start, end = overnight_window(edition_date("premarket", now))
        return (
            quote.session == bag and start.timestamp() <= stamp.timestamp() <= now.timestamp() < end.timestamp()
            and now.timestamp() - stamp.timestamp() <= TTL[bag]
        )
    if quote.session == "regular":
        return day == latest or (day == now.date() and is_trading_day(day) and now < session_close(day))
    return quote.session in {"futures", "reference"}


def apply_cache(data: MarketData, now: datetime) -> MarketData:
    """Mutate data after live fallback: save live quotes and fill only missing records."""
    now = new_york_time(now)
    for bag in QUOTE_BAGS:
        quotes = getattr(data, bag)
        restored: dict[bool, list[Quote]] = {}
        for symbol in NAMES:
            key = f"usstock:{bag}:{symbol}"
            live = quotes.get(symbol)
            if live is not None:
                if not live.cached:
                    try:
                        cache.write(key, _record(live), now=now)
                    except (TypeError, ValueError, AttributeError, OverflowError):
                        pass
                continue
            record = cache.read(key, max_age=TTL[bag], now=now)
            quote = _restore(record, symbol, bag, now) if record is not None else None
            try:
                usable = quote is not None and _current(quote, bag, now)
            except ValueError:
                usable = False
            if usable:
                quotes[symbol] = quote
                restored.setdefault(quote.is_snapshot, []).append(quote)
        for snapshot, records in restored.items():
            clocks = [quote_clock(quote) for quote in records]
            start, end = min(clocks, key=datetime.timestamp), max(clocks, key=datetime.timestamp)
            span = start.strftime("%m-%d %H:%M")
            if start != end:
                span += "～" + end.strftime("%m-%d %H:%M")
            data.notes.append(
                f"缓存{LABELS[bag]}：{'、'.join(quote.symbol for quote in records)}；"
                f"{'采集时间' if snapshot else '数据时间'} {span} ET"
            )
    return data
