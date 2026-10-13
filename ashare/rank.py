from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from common.news import news_key

from .calendar import previous_trading_day
from .models import CST, NewsItem, china_time

BOOSTS: tuple[tuple[str, int], ...] = (
    ("收评", 36),
    ("午评", 18),
    ("央行", 16),
    ("证监会", 16),
    ("国务院", 14),
    ("美联储", 14),
    ("统计局", 12),
    ("降准", 14),
    ("降息", 14),
    ("南向", 12),
    ("北向", 12),
    ("PCE", 12),
    ("ADP", 10),
    ("PMI", 10),
    ("CPI", 8),
    ("GDP", 8),
    ("A股", 8),
    ("沪指", 8),
    ("创业板", 6),
    ("科创", 6),
)

NOISE = (
    "减持",
    "辞职",
    "聘任",
    "司法拍卖",
    "公开招募",
    "停牌",
    "复牌",
    "质押",
    "协议转让",
    "离职",
)

KEEP = ("收评", "午评", "央行", "证监会", "国务院", "美联储", "南向", "统计局", "降准", "降息")


def score_news(item: NewsItem, *, kind: str, trade_date: date) -> float:
    title = item.title
    score = item.source_score * 10
    for word, points in BOOSTS:
        if word in title:
            score += points
    noisy = any(word in title for word in NOISE)
    if noisy and not any(word in title for word in KEEP):
        score -= 30
    published_date = china_time(item.published).date()
    if published_date == trade_date:
        score += 6
    elif published_date < trade_date - timedelta(days=1):
        score -= 20
    if kind == "morning" and any(word in title for word in ("央行", "美联储", "PCE", "ADP", "统计局", "GDP", "美股", "日本", "韩国", "日经")):
        score += 8
    if kind == "morning" and any(word in title for word in ("收评", "午评")):
        score -= 40
    return score


_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?%?")


def _numbers(title: str) -> tuple[str, ...]:
    return tuple(_NUMBER.findall(title))


def _relation(kept: NewsItem, candidate: NewsItem) -> str:
    """相同数字和符号才算同一事实。旧标题是新标题的前缀时，不丢掉较新的一条。"""
    left, right = news_key(kept.title), news_key(candidate.title)
    if not left or not right or _numbers(kept.title) != _numbers(candidate.title):
        return ""
    if left == right:
        return "same"
    if len(left) >= 10 and left in right and candidate.published >= kept.published:
        return "older-prefix"
    if len(right) >= 10 and right in left:
        return "same"
    return ""


def select_news(
    items: list[NewsItem],
    *,
    kind: str,
    trade_date: date,
    now: datetime,
    limit: int = 7,
) -> list[NewsItem]:
    if kind not in {"close", "morning"}:
        raise ValueError("kind 只能是 close 或 morning")
    if limit <= 0:
        return []
    now = china_time(now)
    if kind == "morning":
        base_date = trade_date
        if base_date >= now.date() and now.time() < time(15, 0):
            try:
                base_date = previous_trading_day(now.date())
            except ValueError:
                # 仅用有限新闻窗口降级；晨报标题仍由交易日历严格校验。
                base_date = now.date() - timedelta(days=1)
        fresh_after = datetime.combine(base_date, time(15, 0), tzinfo=CST)
    else:
        fresh_after = now - timedelta(hours=36)
    pool: list[NewsItem] = []
    for item in items:
        published = china_time(item.published)
        title = item.title.strip()
        if published < fresh_after or published > now:
            continue
        if kind == "morning" and any(word in title for word in ("收评", "午评")):
            continue
        if len(title) < 8:
            continue
        if any(word in title for word in NOISE) and not any(word in title for word in KEEP):
            continue
        scored = NewsItem(
            published=published,
            title=title,
            source=item.source,
            source_score=item.source_score,
            url=item.url,
            external_id=item.external_id,
        )
        scored.rank = score_news(scored, kind=kind, trade_date=trade_date)
        pool.append(scored)
    pool.sort(key=lambda item: (-item.rank, -item.published.timestamp()))
    preferred = [item for item in pool if item.rank >= 16]
    ranked = preferred or [item for item in pool if item.rank >= 0]
    chosen: list[NewsItem] = []
    for item in ranked:
        skip = False
        for index, kept in enumerate(chosen):
            relation = _relation(kept, item)
            if relation == "older-prefix":
                chosen[index] = item
                skip = True
                break
            if relation == "same":
                skip = True
                break
        if skip:
            continue
        if "美联储" in item.title and sum("美联储" in kept.title for kept in chosen) >= 2:
            continue
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen
