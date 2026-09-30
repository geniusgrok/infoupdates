from __future__ import annotations

from datetime import date, datetime

WEEKDAYS = "一二三四五六日"
MILLION = 1_000_000


def weekday_cn(day: date) -> str:
    return "周" + WEEKDAYS[day.weekday()]


def fmt_px(value: float, digits: int | None = None) -> str:
    if digits is None:
        digits = 4 if value < 20 else 2
    if digits == 2 and abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.005:
        return f"{0:.{digits}f}%"
    return f"{value:+.{digits}f}%"


def fmt_pts(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.005:
        return "0.00"
    return f"{value:+,.2f}"


def fmt_yi(value: float | None, unit: str = "亿", signed: bool = False, digits: int = 1) -> str:
    if value is None:
        return "—"
    scaled = value / 1e8
    number = f"{abs(scaled):.{digits}f}" if abs(scaled) < 1000 else f"{abs(scaled):.0f}"
    if abs(scaled) < 0.05:
        body = "0.0" if digits else "0"
    else:
        body = number
    if signed:
        if scaled > 0.05:
            return f"+{body}{unit}"
        if scaled < -0.05:
            return f"-{body}{unit}"
        return f"0.0{unit}"
    sign = "-" if scaled < -0.05 else ""
    return f"{sign}{body}{unit}"


def fmt_amount(value: float | None) -> str:
    if value is None:
        return "—"
    yi = value / 1e8
    if yi >= 10000:
        return f"{yi / 10000:.2f}万亿"
    if yi >= 100:
        return f"{yi:.0f}亿"
    return f"{yi:.1f}亿"


def million_to_ccy(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) * MILLION


def clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def day_label(moment: datetime) -> str:
    return moment.strftime("%Y.%m.%d")
