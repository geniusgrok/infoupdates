from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .news import NewsItem

if TYPE_CHECKING:
    from .events import CalendarEvent

CST = timezone(timedelta(hours=8))
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class FocusItem:
    label: str
    title: str
    context: str
    stamp: str


def _context(title: str) -> str:
    if any(word in title for word in ("非农", "就业", "失业", "Employment", "JOLTS")):
        return "关注就业数据对利率预期的影响。"
    if any(word in title for word in ("CPI", "PCE", "PPI", "通胀", "物价", "Consumer Price", "Producer Price")):
        return "关注通胀变化及随后债券收益率的反应。"
    if any(word in title for word in ("PMI", "GDP", "制造业")):
        return "关注增长变化与企业盈利预期。"
    if any(word in title for word in ("美联储", "美债", "国债", "利率", "降息", "降准", "央行")):
        return "关注利率预期变化能否传导至股市。"
    if any(word in title for word in ("石油", "原油", "油价", "柴油", "能源")):
        return "关注油价变化对通胀和行业成本的影响。"
    if any(word in title for word in ("财报", "盈利", "业绩", "营收")):
        return "关注业绩与指引能否支持当前估值。"
    if any(word in title for word in ("证监会", "国务院", "政策", "关税")):
        return "关注政策落地节奏与相关板块的反应。"
    return "关注消息公布后，价格与资金是否同步响应。"


def _priority(item: NewsItem) -> tuple[float, float]:
    title = item.title
    impact = max((weight for words, weight in (
        (("央行", "证监会", "国务院", "FOMC", "降准", "降息"), 60),
        (("CPI", "PCE", "非农", "GDP", "PMI", "通胀", "美联储"), 50),
        (("油价", "原油", "柴油", "国债", "美债", "利率"), 40),
        (("财报", "业绩", "营收", "关税", "油价", "国债"), 35),
    ) if any(word in title for word in words)), default=0)
    rank = item.rank if isfinite(item.rank) else 0
    published = item.published.replace(tzinfo=CST) if item.published.tzinfo is None else item.published
    return impact + rank / 2, published.timestamp()


def _headline(title: str) -> str:
    # 只取原文完整首句；不删除观点归属，也不自行改写数字或因果。
    value = re.sub(r"\s+", " ", title).strip()
    value = re.sub(r"(?<![A-Za-z])(CPI|PCE|PPI|PMI|GDP)数据", r"\1", value)
    first = re.split(r"[。！？]\s*", value, maxsplit=1)[0]
    return first if len(first) >= 8 else value


def build_focus(
    news: list[NewsItem], now: datetime, *, market: str,
    watch: str = "", event: CalendarEvent | None = None,
) -> list[FocusItem]:
    """图片只留一条关键消息和一条已确认日程；缺少日程时列观察条件。"""
    if market not in {"ashare", "usstock"}:
        raise ValueError("market 必须是 ashare 或 usstock")
    display_zone = CST if market == "ashare" else NY
    now = now.replace(tzinfo=display_zone) if now.tzinfo is None else now
    candidates = [item for item in news if item.title.strip()
                  and (item.published.replace(tzinfo=CST) if item.published.tzinfo is None else item.published) <= now]
    result: list[FocusItem] = []
    if candidates:
        item = max(candidates, key=_priority)
        published = item.published.replace(tzinfo=CST) if item.published.tzinfo is None else item.published
        published = published.astimezone(display_zone)
        stamp = published.strftime("%m-%d %H:%M")
        zone_label = "CST" if market == "ashare" else published.strftime("%Z")
        result.append(FocusItem("关键消息", _headline(item.title), _context(item.title),
                                f"{stamp} {zone_label} · {item.source}"))
    else:
        result.append(FocusItem("关键消息", "暂无可核实的重要消息", "等待公开信息更新。", ""))
    if event is not None and event.at.tzinfo is not None and now < event.at <= now + timedelta(days=3):
        at = event.at.astimezone(display_zone)
        stamp = at.strftime("%m-%d %H:%M")
        zone_label = "CST" if market == "ashare" else at.strftime("%Z")
        result.append(FocusItem("下一事件", event.title, _context(event.title),
                                f"计划 {stamp} {zone_label} · {event.source}"))
    else:
        result.append(FocusItem("继续观察", watch or "观察领涨方向能否扩散，量能能否配合。",
                                "以随后行情验证，避免仅凭一条消息判断。", ""))
    return result
