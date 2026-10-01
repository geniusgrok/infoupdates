from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3

from common.archive import Archive
from common.events import load_next_event
from common.history import build_history
from review.events import track_events
from review.bls import collect_releases

from .calendar import edition_date, last_completed_session
from .compose import build_brief
from .data import load_market
from .models import NY
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 1080×1620 美股盘前和盘后精选信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "premarket", "postmarket"))
    parser.add_argument("--output", default="output", help="图片和配图文案输出目录")
    parser.add_argument("--archive", default="archive", help="持久化数据与历史产物目录")
    args = parser.parse_args(argv)

    kinds = ("premarket", "postmarket") if args.session == "all" else (args.session,)
    now = datetime.now(NY)
    try:
        for kind in kinds:
            edition_date(kind, now)
        last_completed_session(now)
    except ValueError as exc:
        parser.error(str(exc))

    data = load_market()
    now = datetime.now(NY)
    try:
        archive = Archive(args.archive)
        capture_id = archive.capture("usstock", data, now)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(f"数据归档失败：{exc}")
    try:
        briefs = [build_brief(kind, data, now=now) for kind in kinds]
    except ValueError as exc:
        parser.error(str(exc))

    event = load_next_event(now)
    for brief in briefs:
        brief.event = event
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        track_events(archive, data.news, [event] if event else [], now)
        collect_releases(archive, now)
        for brief in briefs:
            stem = output / f"us-{brief.kind}-{brief.edition_date.isoformat()}"
            image, copy_path = archive.publish("usstock", brief, capture_id, render_png, social_copy(brief), stem)
            print(copy_path.read_text(encoding="utf-8"))
            print(image.resolve())
            print(copy_path.resolve())
            print()
        print(build_history(archive).resolve())
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(f"归档或发布失败：{exc}")


if __name__ == "__main__":
    main()
