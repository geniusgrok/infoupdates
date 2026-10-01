from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .calendar import edition_date, last_completed_session
from .compose import build_brief
from .fetch import load_market
from .models import NY
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 1080×1620 美股盘前和盘后精选信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "premarket", "postmarket"))
    parser.add_argument("--output", default="output", help="图片和配图文案输出目录")
    args = parser.parse_args(argv)

    kinds = ("premarket", "postmarket") if args.session == "all" else (args.session,)
    now = datetime.now(NY)
    try:
        for kind in kinds:
            edition_date(kind, now)
        last_completed_session(now)
    except ValueError as exc:
        parser.error(str(exc))

    data = load_market(now=now)
    try:
        briefs = [build_brief(kind, data, now=now) for kind in kinds]
    except ValueError as exc:
        parser.error(str(exc))

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for brief in briefs:
        stem = output / f"us-{brief.kind}-{brief.edition_date.isoformat()}"
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
