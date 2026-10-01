from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite

from common.events import CalendarEvent
from common.news import NewsItem

CST = timezone(timedelta(hours=8))


def china_time(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CST)
    return moment.astimezone(CST)


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
class MarketData:
    indices: list[Quote]
    overseas: list[Quote] = field(default_factory=list)
    fx: list[Quote] = field(default_factory=list)
    sectors_up: list[SectorMove] = field(default_factory=list)
    sectors_down: list[SectorMove] = field(default_factory=list)
    capital: list[CapitalMix] = field(default_factory=list)
    sector_in: list[SectorFlow] = field(default_factory=list)
    sector_out: list[SectorFlow] = field(default_factory=list)
    breadth: Breadth | None = None
    cross: CrossBorder | None = None
    news: list[NewsItem] = field(default_factory=list)
    sector_source: str = "新浪行业"
    flow_source: str = "东财行业"
    notes: list[str] = field(default_factory=list)
    turnover_comparison: TurnoverComparison | None = None


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
    edition_date: date
    preview: bool
    hero: Quote
    indices: list[Quote]
    sectors_up: list[SectorMove]
    sectors_down: list[SectorMove]
    capital: list[CapitalMix]
    sector_in: list[SectorFlow]
    sector_out: list[SectorFlow]
    breadth: Breadth | None
    cross: CrossBorder | None
    overseas: list[Quote]
    fx: list[Quote]
    news: list[NewsItem]
    narrative: Narrative
    turnover: float | None
    sector_source: str = "新浪行业"
    flow_source: str = "东财行业"
    notes: list[str] = field(default_factory=list)
    turnover_comparison: TurnoverComparison | None = None
    event: CalendarEvent | None = None

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

    def session_label(self) -> str:
        if self.trade_date == self.edition_date - timedelta(days=1):
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
