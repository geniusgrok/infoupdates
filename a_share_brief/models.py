from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


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
    limit_up: int
    limit_down: int
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


@dataclass
class NewsItem:
    published: datetime
    title: str
    source: str
    source_score: float = 1
    rank: float = 0


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

    @property
    def title(self) -> str:
        if self.kind == "morning":
            return "早盘"
        return "收盘综述"

    def index(self, name: str) -> Quote | None:
        for quote in self.indices:
            if quote.name == name:
                return quote
        if self.hero.name == name:
            return self.hero
        return None

    @property
    def main_net(self) -> float | None:
        if not self.capital:
            return None
        return sum(item.main for item in self.capital)
