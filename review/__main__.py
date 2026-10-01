from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone

from common.archive import Archive
from common.history import build_history
from .bls import collect_releases
from .events import comparisons, reactions, record_metric


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="历史回看与重点事件跟踪")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("history", "更新历史页面"), ("events", "更新并查看事件结果"),
                            ("expectation", "录入事前预期"), ("result", "录入发布结果")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--archive", default="archive", help="持久归档目录；各模块应共用")
        if name in {"expectation", "result"}:
            command.add_argument("event_id", help="review events 输出的事件编号")
            command.add_argument("--metric", required=True, help="指标名称")
            command.add_argument("--period", required=True, help="统计月份 YYYY-MM")
            command.add_argument("--value", type=float, required=True, help="数值")
            command.add_argument("--previous", type=float, help="同一报道的前值")
            command.add_argument("--unit", required=True, help="万人、万个或 %%")
            command.add_argument("--source", required=True, help="数值来源名称")
            command.add_argument("--url", required=True, help="来源正文的 HTTP(S) 地址")
            command.add_argument("--published-at", type=datetime.fromisoformat, required=True, help="带时区的 ISO 发布时间")
            command.add_argument("--verified", action="store_true", help="已人工核验来源正文；采用该结果并保留证据")
    args = parser.parse_args(argv)
    try:
        archive = Archive(args.archive)
        with archive.run():
            now = datetime.now(timezone.utc)
            if args.command in {"expectation", "result"}:
                data = {field: getattr(args, field) for field in ("metric", "period", "value", "previous", "unit", "source", "url", "verified")}
                record_metric(archive, args.event_id, args.command, data, args.published_at, now)
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
