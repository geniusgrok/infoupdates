from __future__ import annotations

import argparse
from datetime import datetime, time
from pathlib import Path

from .compose import build_brief
from .fetch import CST, load_market
from .models import is_trading_day, next_trading_day
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 1080×1620 A 股早盘和收盘精选信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "close", "morning"))
    parser.add_argument("--output", default="output", help="图片输出目录")
    args = parser.parse_args(argv)

    kinds = ("close", "morning") if args.session == "all" else (args.session,)
    now = datetime.now(CST)
    if "morning" in kinds:
        try:
            if not is_trading_day(now.date()) or now.time() >= time(15, 0):
                next_trading_day(now.date())
        except ValueError as exc:
            parser.error(str(exc))
    data = load_market()
    try:
        briefs = [build_brief(kind, data, now=now) for kind in kinds]
        editions = [(brief, brief.edition_date()) for brief in briefs]
    except ValueError as exc:
        parser.error(str(exc))
    output = Path(args.output)
    for brief, shown in editions:
        stem = output / f"{brief.kind}-{shown.isoformat()}"
        image = render_png(brief, stem.with_suffix(".png"))
        text = social_copy(brief)
        copy_path = stem.with_suffix(".txt")
        copy_path.write_text(text, encoding="utf-8")
        print(text)
        print(image.resolve())
        print(copy_path.resolve())
        print()


if __name__ == "__main__":
    main()
