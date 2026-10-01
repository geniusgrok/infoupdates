from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone

from common.archive import Archive
from common.history import build_history
from .events import comparisons, reactions, record_metric
from .bls import collect_releases


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="历史回看与重点事件跟踪")
    parser.add_argument("command", choices=("history", "events", "expectation", "result"))
    parser.add_argument("event_id", nargs="?")
    parser.add_argument("--archive", default="archive")
    parser.add_argument("--metric")
    parser.add_argument("--period", help="统计月份 YYYY-MM")
    parser.add_argument("--value", type=float)
    parser.add_argument("--previous", type=float)
    parser.add_argument("--unit")
    parser.add_argument("--source")
    parser.add_argument("--url")
    parser.add_argument("--published-at", help="带时区的 ISO 发布时间")
    parser.add_argument("--verified", action="store_true", help="确认已人工核验来源正文；复盘采用该记录且保留旧证据")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        archive = Archive(args.archive)
        if args.command in {"expectation", "result"}:
            if not args.event_id or not args.published_at:
                parser.error("录入证据需要事件编号及 --published-at")
            data = {field: getattr(args, field) for field in ("metric", "period", "value", "previous", "unit", "source", "url")}
            data["verified"] = args.verified
            record_metric(archive, args.event_id, args.command, data, datetime.fromisoformat(args.published_at), now)
        if args.command == "events":
            collect_releases(archive, now)
            for event in archive.events(through=now):
                print(f"{event['title']} · {event['at']}\n编号 {event['id']}")
                for line in comparisons(event) or ["等待发布" if event["at"] > now.isoformat() else "发布结果待核实"]:
                    print("  " + line)
                for line in reactions(archive, event, now):
                    print("  行情观察：" + line)
                print()
        print(build_history(archive).resolve())
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
