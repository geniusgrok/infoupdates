from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

from common.archive import Archive
from common.events import load_events
from common.history import build_history
from common.render import DEFAULT_WATERMARK
from review.events import track_events
from review.bls import collect_releases
from .compose import build_weekly, social_copy, week_start
from .render import render_png


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="用已归档数据生成1620×1080横版跨市场每周精选")
    parser.add_argument("--week", type=date.fromisoformat, help="交易周的周一日期；默认最近结束的一周")
    parser.add_argument("--archive", default="archive", help="持久归档目录；各模块应共用")
    parser.add_argument("--output", default="output", help="图片与文字输出目录")
    parser.add_argument("--backfill", action="store_true", help="先从免费历史日线补录本周与上周行情")
    parser.add_argument("--force", action="store_true", help="重新统计和生成，成功后覆盖已有结果")
    parser.add_argument("--watermark", default=DEFAULT_WATERMARK, help="自定义图片水印；空文字关闭；更换已有图片需 --force")
    args = parser.parse_args(argv)
    render = partial(render_png, watermark=args.watermark)
    try:
        archive = Archive(args.archive)
        with archive.run():
            now = datetime.now(timezone.utc)
            start = week_start(now, args.week)
            day = start + timedelta(days=5)
            stem = Path(args.output) / f"weekly-{day}"
            if not args.force and archive.reuse("weekly", "weekly", day, stem):
                print(build_history(archive).resolve())
                return
            if args.backfill:
                from .backfill import backfill
                for message in backfill(archive, start, now):
                    print(message)
            # 周报日历覆盖下一周；日报仍只采用未来三天的下一事件。
            events = load_events(now, days=10)
            track_events(archive, [], events, now)
            collect_releases(archive, now)
            brief = build_weekly(archive, now, week=start)
            capture_id = archive.capture("weekly", {key: value for key, value in brief.items() if key != "generated_at"}, now)
            image, text = archive.publish("weekly", brief, capture_id, render, social_copy(brief), stem, force=args.force)
            print(text.read_text(encoding="utf-8"))
            print(image.resolve())
            print(text.resolve())
            print(build_history(archive).resolve())
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
