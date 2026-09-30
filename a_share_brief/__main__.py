from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .compose import build_brief
from .fetch import CST, load_market
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 A 股早盘前瞻和收盘综述信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "close", "morning"))
    parser.add_argument("--output", default="output", help="图片输出目录")
    args = parser.parse_args(argv)

    kinds = ("close", "morning") if args.session == "all" else (args.session,)
    data = load_market()
    now = datetime.now(CST)
    output = Path(args.output)
    for kind in kinds:
        brief = build_brief(kind, data, now=now)
        stem = output / f"{kind}-{brief.trade_date.isoformat()}"
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
