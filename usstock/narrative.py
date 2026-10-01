from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from math import isfinite

from common.news import NewsItem, news_key
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


def _roundup_title(title: str) -> bool:
    """排除仅栏目名和日期的汇总标题；有事件正文的标题继续保留。"""
    compact = re.sub(r"(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{4}[./-]\d{1,2}[./-]\d{1,2}|[12]\d{7}", "", title)
    compact = re.sub(r"(?:星期|周)[一二三四五六日天]", "", compact)
    compact = re.sub(r"[\s|｜:：·—\-()（）]", "", compact)
    return re.fullmatch(r"(?:华尔街见闻|华尔街)?(?:美股|美国股市)?(?:早餐|早报|晚报)", compact) is not None


def select_news(
    items: list[NewsItem], *, kind: str, reference_date: date | None,
    now: datetime, limit: int = 6,
) -> list[NewsItem]:
    if kind not in {"premarket", "postmarket"}:
        raise ValueError("kind 只能是 premarket 或 postmarket")
    if limit <= 0:
        return []
    now = new_york_time(now)
    now_stamp = now.timestamp()
    cutoff_stamp = now_stamp - 36 * 3600
    if reference_date is not None:
        reference_cutoff = session_close(reference_date) if kind == "premarket" else session_open(reference_date)
        reference_stamp = reference_cutoff.timestamp()
        if reference_stamp > now_stamp:
            reference_stamp = cutoff_stamp
        # 周末/长假盘前保留前收后要闻，但不让陈旧行情无限扩大窗口。
        cutoff_stamp = max(reference_stamp, now_stamp - (96 if kind == "premarket" else 36) * 3600)
    selected: dict[str, NewsItem] = {}
    for item in items:
        title = item.title.strip()
        published = _news_time(item.published)
        if not cutoff_stamp <= published.timestamp() <= now_stamp or len(title) < 8 or not re.search(r'[\u4e00-\u9fff]', title):
            continue
        if kind == "premarket" and _roundup_title(title):
            continue
        if not any(topic.lower() in title.lower() for topic in US_TOPICS):
            if not re.search(r'(?<![A-Za-z0-9])(?:AAPL|MSFT|NVDA|AMZN|GOOGL|META|TSLA)(?![A-Za-z0-9])', title, re.I):
                continue
        rank = item.source_score * 10 + sum(weight for word, weight in BOOSTS if word.lower() in title.lower())
        scored = NewsItem(published=published, title=title, source=item.source, source_score=item.source_score, rank=rank)
        key = news_key(title)
        previous = selected.get(key)
        priority = (published.timestamp(), rank) if kind == "premarket" else (rank, published.timestamp())
        previous_priority = ((previous.published.timestamp(), previous.rank) if kind == "premarket"
                             else (previous.rank, previous.published.timestamp())) if previous is not None else None
        if previous is None or priority > previous_priority:
            selected[key] = scored
    order = ((lambda item: (-item.published.timestamp(), -item.rank)) if kind == "premarket"
             else (lambda item: (-item.rank, -item.published.timestamp())))
    return sorted(selected.values(), key=order)[:limit]


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
        return "SPY披露日线股数代理待确认"
    if not isfinite(quote.volume) or not isfinite(quote.previous_volume) or quote.volume < 0 or quote.previous_volume <= 0:
        return "SPY披露日线股数代理待确认"
    try:
        if quote.previous_date != previous_trading_day(brief.reference_date):
            return "SPY披露日线股数代理待确认"
    except (TypeError, ValueError):
        return "SPY披露日线股数代理待确认"
    delta = quote.volume - quote.previous_volume
    ratio = delta / quote.previous_volume
    volume_label = "放量" if ratio > 0.05 else "缩量" if ratio < -0.05 else "基本平量"
    if abs(delta) < 50:
        change = "成交量与上日基本持平"
    else:
        change = f"成交量较上日{'增加' if delta > 0 else '减少'}{_shares(abs(delta))}"
    return f"SPY披露日线股数代理{volume_label}（{change}）"


def fresh_futures(quotes: list[Quote], now: datetime, target: date) -> list[Quote]:
    """盘前只使用上一常规场结束后、六小时内的真实股指期货报价。"""
    now = new_york_time(now)
    after = session_close(previous_trading_day(target))
    return [quote for quote in quotes
            if quote.session == "futures" and quote.asof is not None
            and after <= new_york_time(quote.asof) <= now
            and 0 <= now.timestamp() - new_york_time(quote.asof).timestamp() <= 6 * 3600]


def available_quotes(quotes: list[Quote]) -> dict[str, Quote]:
    return {quote.symbol: quote for quote in quotes if isfinite(quote.last) and quote.last > 0}


def current_quotes(quotes: list[Quote], brief: Brief) -> dict[str, Quote]:
    available = available_quotes(quotes)
    if brief.kind == "premarket":
        return {symbol: quote for symbol, quote in available.items()
                if quote.session in {"overnight", "premarket", "postmarket"} and (quote.asof is not None or quote.is_snapshot)}
    return available


def afterhours_quotes(brief: Brief) -> dict[str, Quote]:
    if brief.kind != "postmarket":
        return {}
    return {symbol: quote for symbol, quote in available_quotes(brief.extended_stocks).items()
            if quote.session == "postmarket" and quote.trade_date == brief.edition_date
            and quote.asof is not None and new_york_time(quote.asof) <= new_york_time(brief.generated_at)}


def selected_stocks(brief: Brief) -> list[Quote]:
    """图片与文案共同选取消息相关、当期波动较大的三家公司。"""
    quotes = list(current_quotes(brief.stocks, brief).values())
    news = " ".join(item.title.lower() for item in brief.news
                    if new_york_time(item.published) <= new_york_time(brief.generated_at))
    after = afterhours_quotes(brief)

    def priority(quote: Quote) -> tuple[bool, float]:
        mentioned = bool(re.search(rf"\b{re.escape(quote.symbol.lower())}\b", news)) or quote.name.lower() in news
        changes = [item.pct for item in (quote, after.get(quote.symbol))
                   if item is not None and item.pct is not None and isfinite(item.pct)]
        return mentioned, round(max((abs(value) for value in changes), default=0), 6)

    return sorted(quotes, key=priority, reverse=True)[:3]


def sector_leaders(brief: Brief) -> tuple[list[Quote], list[Quote]]:
    values = [quote for quote in current_quotes(brief.sectors, brief).values()
              if quote.pct is not None and isfinite(quote.pct)]
    ordered = sorted(values, key=lambda quote: quote.pct, reverse=True)
    strongest = ordered[:2]
    symbols = {quote.symbol for quote in strongest}
    weakest = [quote for quote in reversed(ordered) if quote.symbol not in symbols][:2]
    return strongest, weakest


def build_narrative(brief: Brief) -> tuple[str, str, str]:
    core = [quote for quote in brief.indices if quote.symbol in {"^DJI", "^IXIC", "^GSPC"}]
    if brief.kind == "premarket":
        current_futures = fresh_futures(brief.futures, brief.generated_at, brief.edition_date)
        usable_futures = [quote for quote in current_futures if quote.pct is not None and isfinite(quote.pct)]
        if len(usable_futures) >= 3:
            headline = _direction_headline(usable_futures, "股指期货")
            sentiment = _mood(usable_futures, [])
            mood = f"盘前情绪{sentiment}（最新股指期货参考）"
        else:
            current_stocks = [quote for quote in brief.stocks
                              if quote.session in {"overnight", "premarket"}]
            usable_stocks = [quote for quote in current_stocks if quote.pct is not None and isfinite(quote.pct)]
            headline = _direction_headline(usable_stocks, "最新大型科技股")
            sentiment = _mood(usable_stocks, [])
            mood = f"盘前情绪{sentiment}（最新大型科技股参考）" if len(usable_stocks) >= 3 else "盘前情绪待确认（最新延长交易行情不足）"
        return headline, sentiment, f"{mood}，{activity_summary(brief)}。"
    headline = _direction_headline(core, "三大指数")
    sentiment = _mood(core, brief.sectors)
    scope = "情绪" if brief.reference_date == brief.edition_date else "参考收盘情绪"
    activity = activity_summary(brief).replace("SPY披露日线股数代理", "SPY股数代理")
    activity = activity.replace("（成交量较上日", " · 较上日").replace("（成交量与上日基本持平", " · 与上日持平").removesuffix("）")
    return headline, sentiment, f"{scope}{sentiment} · 指数/ETF参考 · {activity}"
