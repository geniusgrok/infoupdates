from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, time
from functools import partial
from pathlib import Path

from common.archive import Archive
from common.editorial import deprioritize_seen_news
from common.events import load_next_event
from common.history import build_history
from common.publication import InsufficientData
from common.render import DEFAULT_WATERMARK
from common.replay import dated_brief
from review.bls import collect_releases
from review.events import track_events

from .calendar import edition_date, is_trading_day
from .compose import build_brief
from .data import load_market
from .models import NY
from .render import render_png
from .social import social_copy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成 1620×1080 横版美股盘前和盘后精选信息图")
    parser.add_argument("session", nargs="?", default="all", choices=("all", "premarket", "postmarket"))
    parser.add_argument("--date", type=date.fromisoformat, help="指定美东版面交易日 YYYY-MM-DD；优先使用归档")
    parser.add_argument("--output", default="output", help="图片与文字输出目录")
    parser.add_argument("--archive", default="archive", help="持久归档目录；各模块应共用")
    parser.add_argument("--force", action="store_true", help="重新生成并覆盖；指定日期时优先重绘归档")
    parser.add_argument("--watermark", default=DEFAULT_WATERMARK, help="自定义图片水印；空文字关闭；更换已有图片需 --force")
    args = parser.parse_args(argv)
    render = partial(render_png, watermark=args.watermark)
    kinds = ('premarket', 'postmarket') if args.session == "all" else (args.session,)
    output = Path(args.output)
    try:
        archive = Archive(args.archive)
        with archive.run():
            now = datetime.now(NY)
            if args.date and not is_trading_day(args.date):
                raise ValueError(f"{args.date} 不是美股交易日，指定日期不会自动顺延")
            if args.date and args.date > now.date() and (kinds != ('premarket',) or args.date != edition_date('premarket', now)):
                raise ValueError("不能生成未来行情；盘前仅允许下一待交易日的参考")
            pending = []
            failures = []
            saved = {(row['session'], row['edition_date']): datetime.fromisoformat(row['generated_at'])
                     for row in archive.reports() if row['market'] == 'usstock'}
            for kind in kinds:
                day = args.date or edition_date(kind, now)
                stem = output / f"us-{kind}-{day}"
                stored_at = saved.get((kind, day.isoformat()))
                late = bool(args.date and stored_at and stored_at > datetime.combine(day, time.max, NY))
                if not args.force and not late and archive.reuse("usstock", kind, day, stem):
                    continue
                if args.date and (day < now.date() or (kind, day.isoformat()) in saved):
                    try:
                        brief, capture_id = dated_brief(archive, 'usstock', kind, day, now)
                        image, text = archive.publish('usstock', brief, capture_id, render,
                                                      social_copy(brief), stem, force=args.force or late)
                        print(text.read_text(encoding='utf-8'))
                        print(image.resolve())
                        print(text.resolve())
                    except (OSError, ValueError) as exc:
                        failures.append(str(exc))
                    continue
                if args.date and day != edition_date(kind, now):
                    failures.append(f"{day} {kind} 尚无对应时段行情")
                    continue
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
                    if args.date and brief.edition_date != args.date:
                        failures.append(f"实际行情版面日期为{brief.edition_date}，无法生成指定日期{args.date}")
                        continue
                    brief.event = event
                    brief.news = deprioritize_seen_news(brief.news, archive.previous_key_news(
                        "usstock", brief.kind, brief.edition_date, now))
                    stem = output / f"us-{brief.kind}-{brief.edition_date.isoformat()}"
                    try:
                        image, text = archive.publish("usstock", brief, capture_id, render,
                                                      social_copy(brief), stem, force=args.force)
                    except InsufficientData as exc:
                        failures.append(str(exc))
                        continue
                    print(text.read_text(encoding="utf-8"))
                    print(image.resolve())
                    print(text.resolve())
                    print()
            print(build_history(archive).resolve())
            if failures:
                raise InsufficientData("；".join(failures))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(f"运行失败：{exc}")


if __name__ == "__main__":
    main()
