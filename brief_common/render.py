from __future__ import annotations

import re
from functools import lru_cache
from math import isfinite
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1080, 1620
BG = (8, 10, 13)
CARD = (18, 22, 27)
LINE = (46, 54, 64)
HAIR = (34, 40, 48)
AMBER = (232, 176, 74)
TEXT = (242, 244, 246)
MUTED = (164, 172, 182)
RED = (255, 92, 92)
GREEN = (38, 196, 146)
FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def _font_file(weight: str) -> Path:
    names = {"regular": "NotoSansSC-Regular.ttf", "medium": "NotoSansSC-Medium.ttf", "bold": "NotoSansSC-Bold.ttf"}
    path = FONT_DIR / names[weight]
    return path if path.exists() else Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


@lru_cache(maxsize=256)
def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_font_file(weight)), size)


def _width(value: str, face: ImageFont.FreeTypeFont) -> float:
    bounds = face.getbbox(value, anchor="lt")
    return max(face.getlength(value), bounds[2] - bounds[0])


def _ellipsize(value: str, face: ImageFont.FreeTypeFont, width: float) -> str:
    if _width(value, face) <= width:
        return value
    while value and _width(value + "…", face) > width:
        value = value[:-1]
    return value + "…" if _width("…", face) <= width else ""


class Canvas:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.image)

    def text(self, x: float, y: float, value: str, size: int = 32,
             color: tuple[int, int, int] = TEXT, weight: str = "regular",
             align: str = "left", max_width: float | None = None,
             min_size: int | None = None) -> tuple[float, float, float, float]:
        if max_width is not None:
            floor = min_size if min_size is not None else size
            while size > floor and _width(value, _font(size, weight)) > max_width:
                size -= 1
            value = _ellipsize(value, _font(size, weight), max_width)
        face = _font(size, weight)
        if align == "right":
            x -= _width(value, face)
        elif align == "center":
            x -= _width(value, face) / 2
        bounds = self.draw.textbbox((x, y), value, font=face, anchor="lt")
        self.draw.text((x, y), value, font=face, fill=color, anchor="lt")
        return bounds

    def pair(self, x: float, y: float, width: float, label: str, value: str,
             size: int = 30, value_size: int | None = None,
             color: tuple[int, int, int] = TEXT, label_color: tuple[int, int, int] = TEXT,
             weight: str = "regular", gap: int = 12) -> None:
        value_size = value_size or size
        label_size = size
        while label_size > 22 and _width(label, _font(label_size)) + _width(value, _font(value_size, weight)) + gap > width:
            label_size -= 1
        while value_size > 22 and _width(value, _font(value_size, weight)) + gap + 22 > width:
            value_size -= 1
        value_width = _width(value, _font(value_size, weight))
        self.text(x, y + max(0, (value_size - label_size) / 2), label, label_size, label_color,
                  max_width=width - value_width - gap)
        self.text(x + width, y, value, value_size, color, weight, "right", max_width=width)

    def paragraph(self, x: float, y: float, value: str, width: float,
                  size: int, lines: int, color: tuple[int, int, int] = TEXT,
                  weight: str = "regular", pitch: int | None = None) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        face = _font(size, weight)
        tokens = re.findall(r"[+-]?\d+(?:[.,]\d+)*(?:%|万人|亿元|亿港元|亿|个月)?|[A-Za-z][A-Za-z0-9._/-]*|.", value)
        tokens = [part for token in tokens for part in (list(token) if _width(token, face) > width else [token])]
        start = 0
        for row in range(lines):
            if start >= len(tokens):
                break
            end = start
            while end < len(tokens) and _width("".join(tokens[start:end + 1]), face) <= width:
                end += 1
            if row == lines - 1:
                line = _ellipsize("".join(tokens[start:]), face, width)
            else:
                line = "".join(tokens[start:end])
            self.text(x, y + row * (pitch or size + 5), line, size, color, weight)
            start = end

    def card(self, x: float, y: float, width: float, height: float, fill=CARD, radius: int = 19) -> None:
        self.draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=fill, outline=LINE, width=1)

    def line(self, x: float, y: float, right: float, bottom: float | None = None, color=HAIR, width: int = 1) -> None:
        self.draw.line((x, y, right, y if bottom is None else bottom), fill=color, width=width)

    def heading(self, x: float, y: float, label: str) -> None:
        for i, height in enumerate((14, 23, 32)):
            self.draw.rounded_rectangle((x + i * 12, y + 32 - height, x + i * 12 + 8, y + 32), radius=1, fill=AMBER)
        self.text(x + 50, y - 1, label, 36, TEXT, "bold")


def _tone(value: float | None):
    if value is None or not isfinite(value) or abs(value) < 0.005:
        return MUTED
    return RED if value > 0 else GREEN
