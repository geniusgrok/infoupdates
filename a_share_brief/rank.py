from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

from .models import NewsItem

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
    if item.published.date() == trade_date:
        score += 6
    elif item.published.date() < trade_date - timedelta(days=1):
        score -= 20
    if kind == "morning" and any(word in title for word in ("央行", "美联储", "PCE", "ADP", "统计局", "GDP", "美股", "日本", "韩国", "日经")):
        score += 8
    if kind == "morning" and any(word in title for word in ("收评", "午评")):
        score -= 40
    return score


def _compact(title: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", title)


def _duplicate(left: str, right: str) -> bool:
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return False
    if a == b or a[:14] == b[:14]:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 10 and short in long


def select_news(
    items: list[NewsItem],
    *,
    kind: str,
    trade_date: date,
    now: datetime,
    limit: int = 7,
) -> list[NewsItem]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone(timedelta(hours=8)))
    if kind == "morning":
        fresh_after = datetime.combine(trade_date, time(15, 0), tzinfo=now.tzinfo)
    else:
        fresh_after = now - timedelta(hours=36)
    pool: list[NewsItem] = []
    for item in items:
        if item.published < fresh_after:
            continue
        if kind == "morning" and any(word in item.title for word in ("收评", "午评")):
            continue
        if len(item.title) < 8:
            continue
        scored = NewsItem(
            published=item.published,
            title=item.title.strip(),
            source=item.source,
            source_score=item.source_score,
        )
        scored.rank = score_news(scored, kind=kind, trade_date=trade_date)
        pool.append(scored)
    pool.sort(key=lambda item: (-item.rank, -item.published.timestamp()))
    preferred = [item for item in pool if item.rank >= 16]
    ranked = preferred or [item for item in pool if item.rank >= 0]
    chosen: list[NewsItem] = []
    fed_count = 0
    for item in ranked:
        if any(_duplicate(item.title, kept.title) for kept in chosen):
            continue
        if "美联储" in item.title:
            if fed_count >= 2:
                continue
            fed_count += 1
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen
