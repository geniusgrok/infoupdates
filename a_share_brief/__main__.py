from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .compose import brief_text, build_brief
from .fetch import CST, load_market
from .render import render_png


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
        path = render_png(brief, output / f"{kind}-{brief.trade_date.isoformat()}.png")
        print(brief_text(brief))
        print()
        print(path.resolve())
        print()


if __name__ == "__main__":
    main()
