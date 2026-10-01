from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from math import isfinite

from brief_common.news import NewsItem
from .calendar import previous_trading_day, session_close, session_open
from .models import Brief, Quote, new_york_time

US_TOPICS = (
    "美股", "美国", "美联储", "纳指", "标普", "道指", "道琼斯", "纳斯达克", "华尔街", "美债",
    "英伟达", "苹果", "微软", "亚马逊", "谷歌", "特斯拉", "鲍威尔", "FOMC", "Fed", "Meta",
)
BOOSTS = (("美联储", 26), ("FOMC", 26), ("CPI", 20), ("PCE", 20), ("非农", 18),
          ("财报", 16), ("美股", 14), ("纳指", 12), ("标普", 12), ("美债", 10), ("降息", 10))


def _news_time(moment: datetime) -> datetime:
    # 公共快讯来自中文数据源，无时区的手工项沿用北京时间约定。
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(timedelta(hours=8)))
    return new_york_time(moment)


def _news_key(title: str) -> str:
    # 保留数字、小数、正负号：上涨0.5%和下跌0.5%绝不能误去重。
    return re.sub(r'[\s，,。；;：:！？!?、“”"\'（）()\[\]]', '', title).lower()


def select_news(
    items: list[NewsItem], *, kind: str, reference_date: date | None,
    now: datetime, limit: int = 6,
) -> list[NewsItem]:
    if kind not in {"premarket", "postmarket"}:
        raise ValueError("kind 只能是 premarket 或 postmarket")
    if limit <= 0:
        return []
    now = new_york_time(now)
    cutoff = now - timedelta(hours=36)
    if reference_date is not None:
        reference_cutoff = session_close(reference_date) if kind == "premarket" else session_open(reference_date)
        # 周末/长假盘前保留前收后要闻，但不让陈旧行情无限扩大窗口。
        cutoff = max(reference_cutoff, now - timedelta(hours=96 if kind == "premarket" else 36))
    selected: dict[str, NewsItem] = {}
    for item in items:
        title = item.title.strip()
        published = _news_time(item.published)
        if not cutoff <= published <= now or len(title) < 8 or not re.search(r'[\u4e00-\u9fff]', title):
            continue
        if not any(topic.lower() in title.lower() for topic in US_TOPICS):
            if not re.search(r'(?<![A-Za-z0-9])(?:AAPL|MSFT|NVDA|AMZN|GOOGL|META|TSLA)(?![A-Za-z0-9])', title, re.I):
                continue
        rank = item.source_score * 10 + sum(weight for word, weight in BOOSTS if word.lower() in title.lower())
        scored = NewsItem(published=published, title=title, source=item.source, source_score=item.source_score, rank=rank)
        key = _news_key(title)
        previous = selected.get(key)
        if previous is None or (rank, published) > (previous.rank, previous.published):
            selected[key] = scored
    return sorted(selected.values(), key=lambda item: (-item.rank, -item.published.timestamp()))[:limit]


def _direction_headline(quotes: list[Quote], noun: str) -> str:
    values = [quote.pct for quote in quotes if quote.pct is not None and isfinite(quote.pct)]
    if len(values) < 3:
        return f"{noun}方向待确认"
    if all(abs(value) < 0.005 for value in values):
        return f"{noun}基本持平"
    if all(value > 0 for value in values):
        return f"{noun}集体走高"
    if all(value < 0 for value in values):
        return f"{noun}集体走低"
    return f"{noun}涨跌分化"


def _mood(quotes: list[Quote], sectors: list[Quote]) -> str:
    values = [quote.pct for quote in quotes if quote.pct is not None and isfinite(quote.pct)]
    if len(values) < 3:
        return "待确认"
    average = sum(values) / len(values)
    sector_values = [quote.pct for quote in sectors if quote.pct is not None and isfinite(quote.pct)]
    positive_ratio = sum(value > 0 for value in sector_values) / len(sector_values) if sector_values else 0.5
    if average >= 0.7 and positive_ratio >= 0.5:
        return "高涨"
    if average <= -0.7 and positive_ratio <= 0.5:
        return "低落"
    if average > 0.2:
        return "平淡偏强"
    if average < -0.2:
        return "平淡偏谨慎"
    return "平淡"


def _shares(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f}亿股"
    if value >= 1e4:
        return f"{value / 1e4:.1f}万股"
    return f"{value:,.0f}股"


def activity_summary(brief: Brief) -> str:
    if brief.kind == "premarket":
        return "量能待开盘确认"
    quote = brief.activity
    if quote is None or quote.volume is None or quote.previous_volume is None or quote.trade_date != brief.reference_date:
        return "SPY量能待确认"
    if not isfinite(quote.volume) or not isfinite(quote.previous_volume) or quote.volume < 0 or quote.previous_volume <= 0:
        return "SPY量能待确认"
    try:
        if quote.previous_date != previous_trading_day(brief.reference_date):
            return "SPY量能待确认"
    except (TypeError, ValueError):
        return "SPY量能待确认"
    delta = quote.volume - quote.previous_volume
    ratio = delta / quote.previous_volume
    volume_label = "放量" if ratio > 0.05 else "缩量" if ratio < -0.05 else "基本平量"
    if abs(delta) < 50:
        change = "成交量与上日基本持平"
    else:
        change = f"成交量较上日{'增加' if delta > 0 else '减少'}{_shares(abs(delta))}"
    return f"SPY{volume_label}（{change}）"


def build_narrative(brief: Brief) -> tuple[str, str, str]:
    core = [quote for quote in brief.indices if quote.symbol in {"^DJI", "^IXIC", "^GSPC"}]
    if brief.kind == "premarket":
        current_futures = [quote for quote in brief.futures if quote.trade_date == brief.edition_date]
        if brief.generated_at.date() == brief.edition_date and len(current_futures) >= 3:
            headline = _direction_headline(current_futures, "股指期货")
            sentiment = _mood(current_futures, [])
            mood = f"盘前情绪{sentiment}（股指期货参考）"
        else:
            headline = _direction_headline(core, "前收三大指数")
            sentiment = _mood(core, brief.sectors)
            mood = f"前收市场情绪{sentiment}（指数与板块ETF参考）"
        return headline, sentiment, f"{mood}，{activity_summary(brief)}。"
    headline = _direction_headline(core, "三大指数")
    sentiment = _mood(core, brief.sectors)
    scope = "市场情绪" if brief.reference_date == brief.edition_date else "参考收盘情绪"
    return headline, sentiment, f"{scope}{sentiment}（指数与板块ETF参考），{activity_summary(brief)}。"
