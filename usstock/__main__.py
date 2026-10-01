from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from common.archive import Archive
from common.editorial import deprioritize_seen_news
from common.events import load_next_event
from common.history import build_history
from review.bls import collect_releases
from review.events import track_events

from .calendar import edition_date
from .compose import build_brief
from .data import load_market
from .models import NY
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 1080×1620 美股盘前和盘后精选信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "premarket", "postmarket"))
    parser.add_argument("--output", default="output", help="图片与文字输出目录")
    parser.add_argument("--archive", default="archive", help="持久归档目录；各模块应共用")
    parser.add_argument("--force", action="store_true", help="重新采集和生成，成功后覆盖已有结果")
    args = parser.parse_args(argv)
    kinds = ('premarket', 'postmarket') if args.session == "all" else (args.session,)
    output = Path(args.output)
    try:
        archive = Archive(args.archive)
        with archive.run():
            now = datetime.now(NY)
            pending = []
            for kind in kinds:
                day = edition_date(kind, now)
                stem = output / f"us-{kind}-{day}"
                if args.force or not archive.reuse("usstock", kind, day, stem):
                    pending.append(kind)
            if pending:
                data = load_market()
                now = datetime.now(NY)
                # 原始采集先落盘，后续组装或绘图失败仍可回看。
                capture_id = archive.capture("usstock", data, now)
                briefs = [build_brief(kind, data, now=now) for kind in pending]
                event = load_next_event(now)
                track_events(archive, data.news, [event] if event else [], now)
                collect_releases(archive, now)
                for brief in briefs:
                    brief.event = event
                    brief.news = deprioritize_seen_news(brief.news, archive.previous_key_news(
                        "usstock", brief.kind, brief.edition_date, now))
                    stem = output / f"us-{brief.kind}-{brief.edition_date.isoformat()}"
                    image, text = archive.publish("usstock", brief, capture_id, render_png,
                                                  social_copy(brief), stem, force=args.force)
                    print(text.read_text(encoding="utf-8"))
                    print(image.resolve())
                    print(text.resolve())
                    print()
            print(build_history(archive).resolve())
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(f"运行失败：{exc}")


if __name__ == "__main__":
    main()
