from __future__ import annotations

from datetime import date
from math import isfinite

WEEKDAYS = "一二三四五六日"
MILLION = 1_000_000


def weekday_cn(day: date) -> str:
    return "周" + WEEKDAYS[day.weekday()]


def fmt_px(value: float | None, digits: int | None = None) -> str:
    if value is None or not isfinite(value):
        return "—"
    if digits is None:
        digits = 4 if value < 20 else 2
    if digits == 2 and abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or not isfinite(value):
        return "—"
    if float(f"{abs(value):.{digits}f}") == 0:
        return f"{0:.{digits}f}%"
    return f"{value:+.{digits}f}%"


def fmt_yi(value: float | None, unit: str = "亿", signed: bool = False, digits: int = 1) -> str:
    if value is None or not isfinite(value):
        return "—"
    scaled = value / 1e8
    body = f"{abs(scaled):.{digits}f}" if abs(scaled) < 1000 else f"{abs(scaled):.0f}"
    if float(body) == 0:
        return f"{body}{unit}"
    if signed:
        return f"{'+' if scaled > 0 else '-'}{body}{unit}"
    sign = "-" if scaled < 0 else ""
    return f"{sign}{body}{unit}"


def fmt_amount(value: float | None) -> str:
    if value is None or not isfinite(value):
        return "—"
    yi = value / 1e8
    if yi >= 10000:
        return f"{yi / 10000:.2f}万亿"
    if yi >= 100:
        return f"{yi:.0f}亿"
    return f"{yi:.1f}亿"


def million_to_ccy(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return float(value) * MILLION
