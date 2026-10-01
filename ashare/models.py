from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite

from brief_common.news import NewsItem

CST = timezone(timedelta(hours=8))

# 上交所年度休市通知；调休的周末仍不开市。
# 2025: https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml
# 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
HOLIDAY_RANGES = {
    2025: (("01-01", "01-01"), ("01-28", "02-04"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("05-31", "06-02"), ("10-01", "10-08")),
    2026: (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07")),
}


def china_time(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CST)
    return moment.astimezone(CST)


def is_trading_day(day: date) -> bool:
    if day.year not in HOLIDAY_RANGES:
        raise ValueError(f"交易日历尚未覆盖{day.year}年，请按交易所公告更新休市安排")
    month_day = day.strftime("%m-%d")
    return day.weekday() < 5 and not any(start <= month_day <= end for start, end in HOLIDAY_RANGES[day.year])


def next_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day


def previous_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


@dataclass
class TurnoverComparison:
    trade_date: date
    previous_date: date
    current: float  # 成交额，人民币元
    previous: float  # 上个交易日的全日成交额，人民币元
    source: str = "搜狐"


@dataclass
class Quote:
    symbol: str
    name: str
    last: float
    pct: float | None = None
    change: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    amount: float | None = None
    session: str = ""
    trade_day: str = ""


@dataclass
class SectorMove:
    name: str
    pct: float
    leader: str = ""


@dataclass
class SectorFlow:
    code: str
    name: str
    net: float


@dataclass
class CapitalMix:
    market: str
    main: float
    super_order: float
    large: float
    mid: float
    small: float
    trade_day: str = ""


@dataclass
class BreadthBucket:
    label: str
    count: int
    side: str  # up, down, flat


@dataclass
class Breadth:
    up: int
    down: int
    flat: int
    limit_up: int | None
    limit_down: int | None
    buckets: list[BreadthBucket] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.up + self.down + self.flat

    @property
    def adv_ratio(self) -> float | None:
        base = self.up + self.down
        if base <= 0:
            return None
        return self.up / base


@dataclass
class CrossBorder:
    north_turnover: float | None = None
    south_net: float | None = None
    south_sh: float | None = None
    south_sz: float | None = None
    trade_day: str = ""


@dataclass
class Narrative:
    style: str
    sentiment: str
    summary: str
    watch: list[str]


@dataclass
class Brief:
    kind: str
    generated_at: datetime
    trade_date: date
    preview: bool
    hero: Quote
    indices: list[Quote]
    spark: list[float]
    sectors_up: list[SectorMove]
    sectors_down: list[SectorMove]
    capital: list[CapitalMix]
    sector_in: list[SectorFlow]
    sector_out: list[SectorFlow]
    breadth: Breadth | None
    cross: CrossBorder | None
    overseas: list[Quote]
    fx: list[Quote]
    futures: list[Quote]
    news: list[NewsItem]
    narrative: Narrative
    turnover: float | None
    sector_source: str = "新浪行业"
    flow_source: str = "东财行业"
    notes: list[str] = field(default_factory=list)
    turnover_comparison: TurnoverComparison | None = None

    @property
    def title(self) -> str:
        if self.kind == "morning":
            return "早盘"
        if self.is_intraday:
            return "盘中快照"
        return "收盘综述"

    def index(self, name: str) -> Quote | None:
        for quote in self.indices:
            if quote.name == name:
                return quote
        if self.hero.name == name:
            return self.hero
        return None

    def edition_date(self) -> date:
        if self.kind != "morning":
            return self.trade_date
        now = china_time(self.generated_at)
        if is_trading_day(now.date()) and now.time() < time(15, 0):
            return now.date()
        return next_trading_day(now.date())

    def session_label(self) -> str:
        if self.trade_date == self.edition_date() - timedelta(days=1):
            return "昨日"
        return f"{self.trade_date.month}月{self.trade_date.day}日"

    @property
    def is_intraday(self) -> bool:
        if self.hero.last <= 0:
            return False
        now = china_time(self.generated_at)
        if self.trade_date > now.date():
            return True
        if self.trade_date == now.date() and now.time() < time(15, 0):
            return True
        # 接口在15:00后仍可能返回未完成的盘中快照。
        stamp = self.hero.session
        if stamp:
            try:
                return time.fromisoformat(stamp) < time(15, 0)
            except ValueError:
                pass
        return False

    @property
    def main_net(self) -> float | None:
        markets = {item.market: item.main for item in self.capital}
        if len(self.capital) != 2 or set(markets) != {"沪市", "深市"}:
            return None
        if not all(isfinite(value) for value in markets.values()):
            return None
        return sum(markets.values())
