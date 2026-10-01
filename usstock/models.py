from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from brief_common.news import NewsItem

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

    @property
    def trade_date(self) -> date | None:
        return new_york_time(self.asof).date() if self.asof is not None else None


@dataclass
class MarketData:
    quotes: dict[str, Quote] = field(default_factory=dict)
    premarket: dict[str, Quote] = field(default_factory=dict)
    postmarket: dict[str, Quote] = field(default_factory=dict)
    news: list[NewsItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    completed: dict[str, Quote] = field(default_factory=dict)


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
