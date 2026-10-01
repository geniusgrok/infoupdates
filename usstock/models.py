from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from math import isfinite
from zoneinfo import ZoneInfo

from common.news import NewsItem

NY = ZoneInfo("America/New_York")

INDEX_NAMES = {"^DJI": "道琼斯", "^IXIC": "纳斯达克", "^GSPC": "标普500", "^RUT": "罗素2000"}
FUTURE_NAMES = {"ES=F": "标普期货", "NQ=F": "纳指期货", "YM=F": "道指期货", "RTY=F": "罗素期货"}
MEGA_NAMES = {
    "AAPL": "苹果", "MSFT": "微软", "NVDA": "英伟达", "AMZN": "亚马逊",
    "GOOGL": "谷歌", "META": "Meta", "TSLA": "特斯拉",
}
SECTOR_NAMES = {
    "XLK": "科技", "XLF": "金融", "XLE": "能源", "XLV": "医疗", "XLY": "可选消费",
    "XLP": "必需消费", "XLI": "工业", "XLB": "材料", "XLU": "公用事业",
    "XLRE": "房地产", "XLC": "通信服务",
}
MACRO_NAMES = {"^VIX": "VIX恐慌指数", "^TNX": "10年美债收益率", "DX-Y.NYB": "美元指数", "GC=F": "黄金", "CL=F": "原油"}


def new_york_time(moment: datetime) -> datetime:
    """无时区的调用方时间按美东当地时间解释；行情接口应提供 aware 时间。"""
    return moment.replace(tzinfo=NY) if moment.tzinfo is None else moment.astimezone(NY)


@dataclass
class Quote:
    symbol: str
    name: str
    last: float
    pct: float | None = None
    previous_close: float | None = None
    asof: datetime | None = None
    session: str = "regular"
    volume: float | None = None
    previous_volume: float | None = None
    previous_date: date | None = None
    source: str = "Yahoo Finance"
    unit: str = "USD"
    observed_at: datetime | None = None
    cached: bool = False
    delay_minutes: int | None = None

    @property
    def is_snapshot(self) -> bool:
        return self.asof is None and self.observed_at is not None

    @property
    def trade_date(self) -> date | None:
        return new_york_time(self.asof).date() if self.asof is not None else None


SNAPSHOT_MAX_AGE_SECONDS = 300

QUOTE_BAGS = ("quotes", "completed", "premarket", "postmarket", "overnight")


def finite_number(value: object, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and (not positive or number > 0) else None


def quote_clock(quote: Quote) -> datetime | None:
    """真实成交时间优先；snapshot仅返回原始采集时间，调用方必须明示区分。"""
    return quote.asof if quote.asof is not None else quote.observed_at


def clean_quote(quote: Quote, symbol: str, now: datetime, *, allow_snapshot: bool = False) -> Quote | None:
    """统一供应商记录边界，不跨源补价格、基准、股数或时间。"""
    if not isinstance(quote, Quote) or quote.symbol != symbol or finite_number(quote.last, positive=True) is None:
        return None
    if (not isinstance(quote.source, str) or not quote.source.strip()
            or not isinstance(quote.name, str) or not quote.name
            or not isinstance(quote.unit, str) or not quote.unit
            or not isinstance(quote.session, str)):
        return None
    stamp = quote.asof
    if stamp is None:
        if not allow_snapshot or quote.session != "overnight" or quote.source != "Webull":
            return None
        stamp = quote.observed_at
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    if stamp.timestamp() > new_york_time(now).timestamp():
        return None
    observed = quote.observed_at
    if observed is not None and (not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None
                                 or observed.timestamp() > new_york_time(now).timestamp()):
        return None
    volume = finite_number(quote.volume)
    previous_volume = finite_number(quote.previous_volume)
    delay = quote.delay_minutes
    if not isinstance(delay, int) or isinstance(delay, bool) or delay < 0:
        delay = None
    return replace(
        quote, last=finite_number(quote.last, positive=True),
        previous_date=quote.previous_date if isinstance(quote.previous_date, date) and not isinstance(quote.previous_date, datetime) else None,
        asof=new_york_time(quote.asof) if quote.asof is not None else None,
        observed_at=new_york_time(quote.observed_at) if quote.observed_at is not None else None,
        pct=finite_number(quote.pct), previous_close=finite_number(quote.previous_close, positive=True),
        volume=volume if volume is not None and volume >= 0 else None,
        previous_volume=previous_volume if previous_volume is not None and previous_volume >= 0 else None,
        delay_minutes=delay,
    )


@dataclass
class MarketData:
    quotes: dict[str, Quote] = field(default_factory=dict)
    premarket: dict[str, Quote] = field(default_factory=dict)
    postmarket: dict[str, Quote] = field(default_factory=dict)
    news: list[NewsItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    completed: dict[str, Quote] = field(default_factory=dict)
    overnight: dict[str, Quote] = field(default_factory=dict)


@dataclass
class Brief:
    kind: str
    generated_at: datetime
    edition_date: date
    reference_date: date | None = None
    indices: list[Quote] = field(default_factory=list)
    futures: list[Quote] = field(default_factory=list)
    stocks: list[Quote] = field(default_factory=list)
    sectors: list[Quote] = field(default_factory=list)
    references: list[Quote] = field(default_factory=list)
    activity: Quote | None = None
    news: list[NewsItem] = field(default_factory=list)
    headline: str = "数据暂缺"
    sentiment: str = "待确认"
    market_summary: str = "市场情绪待确认，量能待确认。"
    notes: list[str] = field(default_factory=list)
    stocks_label: str = ""
    complete: bool = False
    extended_stocks: list[Quote] = field(default_factory=list)

    @property
    def title(self) -> str:
        return "美股盘前精选" if self.kind == "premarket" else "美股盘后精选"
