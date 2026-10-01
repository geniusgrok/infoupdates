from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from common.archive import Archive
from common.events import load_events
from common.history import build_history
from review.events import track_events
from review.bls import collect_releases
from .compose import build_weekly, social_copy, week_start
from .render import render_png


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="用已归档数据生成1080×1620跨市场每周精选")
    parser.add_argument("--week", type=date.fromisoformat, help="交易周的周一日期；默认最近结束的一周")
    parser.add_argument("--archive", default="archive")
    parser.add_argument("--output", default="output")
    parser.add_argument("--backfill", action="store_true", help="先从免费历史日线补录本周与上周行情")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        start = week_start(now, args.week)
        archive = Archive(args.archive)
        if args.backfill:
            from .backfill import backfill
            for message in backfill(archive, start, now):
                print(message)
        # 日历覆盖下一周；日报仍然只采用未来三天的下一事件。
        events = load_events(now, days=10)
        track_events(archive, [], events, now)
        collect_releases(archive, now)
        brief = build_weekly(archive, now, week=start)
        capture_id = archive.capture("weekly", {key: value for key, value in brief.items() if key != "generated_at"}, now)
        stem = Path(args.output) / f"weekly-{brief['edition_date']}"
        image, copy = archive.publish("weekly", brief, capture_id, render_png, social_copy(brief), stem)
        print(copy.read_text(encoding="utf-8"))
        print(image.resolve())
        print(copy.resolve())
        print(build_history(archive).resolve())
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
