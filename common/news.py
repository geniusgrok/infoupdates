from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite

from .http import fetch_text

CST = timezone(timedelta(hours=8))
WSCN = "https://wallstreetcn.com/"
KUAIXUN = "https://kuaixun.eastmoney.com/"


@dataclass
class NewsItem:
    published: datetime
    title: str
    source: str
    source_score: float = 1
    rank: float = 0


def news_key(title: str) -> str:
    # 仅忽略排版差异；保留数字、小数、正负号和百分号。
    return re.sub(r'[\s，,。；;：:！？!?、“”"\'（）()\[\]]', '', title).lower()


def _clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    value = html.unescape(text or "")
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _headline(title: str, body: str) -> str:
    title = _clean(title)
    if len(title) >= 8:
        return title
    text = _clean(body)
    if "。" in text:
        first = text.split("。", 1)[0].strip()
        if 8 <= len(first) <= 56:
            return first
    return text[:48].strip()


def _score(value: object, default: float = 1) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return score if isfinite(score) else default


def _data(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("新闻响应不是 JSON 对象")
    data = payload.get("data")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("新闻 data 不是 JSON 对象")
    return data


def _rows(data: dict, field: str) -> list:
    rows = data.get(field)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("新闻列表格式无效")
    return rows


def wscn_items(channel: str, pages: int = 3) -> list[NewsItem]:
    items: list[NewsItem] = []
    cursor = ""
    for _ in range(pages):
        url = f"https://api-one.wallstcn.com/apiv1/content/lives?channel={channel}&client=pc&limit=50"
        if cursor:
            url += f"&cursor={cursor}"
        payload = json.loads(fetch_text(url, WSCN))
        data = _data(payload)
        for raw in _rows(data, "items"):
            if not isinstance(raw, dict):
                continue
            title = _headline(raw.get("title") or "", raw.get("content_text") or raw.get("content") or "")
            if not title:
                continue
            try:
                published = datetime.fromtimestamp(float(raw["display_time"]), CST)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            items.append(
                NewsItem(
                    published=published,
                    title=title,
                    source="见闻",
                    source_score=_score(raw.get("score") or 1),
                )
            )
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break
    return items


def em_items(column: str) -> list[NewsItem]:
    url = (
        "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        f"?client=web&biz=web_724&fastColumn={column}&sortEnd=&pageSize=20&req_trace=1"
    )
    payload = json.loads(fetch_text(url, KUAIXUN))
    items: list[NewsItem] = []
    for raw in _rows(_data(payload), "fastNewsList"):
        if not isinstance(raw, dict):
            continue
        title = _headline(raw.get("title") or "", raw.get("summary") or "")
        if not title:
            continue
        try:
            published = datetime.strptime(raw["showTime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
        except (KeyError, TypeError, ValueError):
            continue
        score = _score(raw.get("titleColor") or 0, default=0)
        if column == "102" and score < 2:
            continue
        if score <= 0:
            score = 1.4
        items.append(NewsItem(published=published, title=title, source="东财", source_score=score))
    return items
