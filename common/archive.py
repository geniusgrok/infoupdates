from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .editorial import select_focus_news
from .news import NewsItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY, market TEXT NOT NULL, data TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(id),
    market TEXT NOT NULL, session TEXT NOT NULL, edition_date TEXT NOT NULL,
    generated_at TEXT NOT NULL, data TEXT NOT NULL, path TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS report_editions ON reports(market, session, edition_date);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, at TEXT NOT NULL, source TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
    kind TEXT NOT NULL CHECK(kind IN ('expectation', 'result', 'report')),
    published_at TEXT NOT NULL, recorded_at TEXT NOT NULL, data TEXT NOT NULL
);
"""


def utc(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("归档时间必须包含时区")
    return moment.astimezone(timezone.utc).isoformat()


def plain(value):
    """只序列化数据字段；不记录 Python 类名，不执行反序列化代码。"""
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return utc(value) if value.utcoffset() is not None else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def encode(value) -> str:
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(value) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Archive:
    def __init__(self, directory: str | Path = "archive"):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self, *, write: bool = False):
        db = sqlite3.connect(self.root / "market.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                # 写事务串行检查与发布，避免并发重复生成。
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def capture(self, market: str, data, now: datetime) -> str:
        if market not in {"ashare", "usstock", "weekly"}:
            raise ValueError("不支持的归档市场")
        payload, stamp = encode(data), utc(now)
        key = identity([market, json.loads(payload)])
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO captures VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "first_seen=MIN(first_seen, excluded.first_seen), last_seen=MAX(last_seen, excluded.last_seen)",
                (key, market, payload, stamp, stamp),
            )
        return key

    @contextmanager
    def run(self):
        """同一归档的命令串行运行；进程退出后系统自动释放锁。"""
        with (self.root / ".run.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("同一归档已有任务运行，等待完成后检查结果……")
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _result(db, market: str, session: str, day: str):
        return db.execute(
            "SELECT * FROM reports WHERE market=? AND session=? AND edition_date=? "
            "ORDER BY generated_at DESC, rowid DESC LIMIT 1", (market, session, day),
        ).fetchone()

    def _complete(self, row) -> bool:
        return row is not None and all((self.root / row["path"] / name).is_file()
                                       for name in ("image.png", "summary.txt", "data.json"))

    def _export(self, row, stem: Path) -> tuple[Path, Path]:
        folder = self.root / row["path"]
        image, text = stem.with_suffix(".png"), stem.with_suffix(".txt")
        atomic_write(image, (folder / "image.png").read_bytes())
        atomic_write(text, (folder / "summary.txt").read_bytes())
        return image, text

    def reuse(self, market: str, session: str, day: date, stem: Path) -> bool:
        """已有完整结果就恢复输出并提示；缺失产物按未完成处理。"""
        with self.connect(write=True) as db:
            row = self._result(db, market, session, day.isoformat())
            if not self._complete(row):
                return False
            image, text = self._export(row, stem)
        print(f"{market} / {session} / {day} 已有运行结果，跳过生成；使用 --force 可重新生成并覆盖。")
        print(image.resolve())
        print(text.resolve())
        return True

    def publish(self, market: str, brief, capture_id: str, render, text: str, stem: Path,
                *, force: bool = False) -> tuple[Path, Path]:
        data = plain(brief)
        if market == "ashare":
            data["intraday"] = brief.is_intraday
        session, day = data["kind"], data["edition_date"]
        valid = {"ashare": {"close", "morning"}, "usstock": {"premarket", "postmarket"}, "weekly": {"weekly"}}
        if session not in valid.get(market, set()):
            raise ValueError("不支持的归档版面")
        date.fromisoformat(day)
        stamp = utc(datetime.fromisoformat(data["generated_at"]))
        key = f"{market}-{session}-{day}"
        parent = self.root / "reports" / day / f"{market}-{session}"
        stage = None
        try:
            with self.connect(write=True) as db:
                row = self._result(db, market, session, day)
                if not force and self._complete(row):
                    print(f"{key} 已有运行结果，跳过生成；使用 --force 可重新生成并覆盖。")
                    return self._export(row, stem)
                capture = db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
                if capture is None or capture["market"] != market:
                    raise ValueError("归档缺少本次原始数据")
                parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
                render(brief, stage / "image.png")
                (stage / "summary.txt").write_text(text, encoding="utf-8")
                (stage / "data.json").write_text(encode({
                    "capture_id": capture_id, "captured_at": capture["first_seen"], "market": market,
                    "snapshot": json.loads(capture["data"]), "brief": data,
                }), encoding="utf-8")
                for artifact in stage.iterdir():
                    with artifact.open("rb") as handle:
                        os.fsync(handle.fileno())
                _sync_directory(stage)
                # 独立产物目录先完成，再切换数据库指向；覆盖失败不损坏原归档。
                folder = parent / stage.name.removeprefix(".pending-")
                os.replace(stage, folder)
                _sync_directory(parent)
                relative = folder.relative_to(self.root).as_posix()
                db.execute("DELETE FROM reports WHERE market=? AND session=? AND edition_date=?", (market, session, day))
                db.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (key, capture_id, market, session, day, stamp, encode(data), relative))
                paths = self._export({"path": relative}, stem)
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
        # 提交后清理被替换或中断留下的目录，当前结果始终保留。
        with self.connect(write=True) as db:
            used = {row["path"] for row in db.execute("SELECT path FROM reports")}
            for child in parent.iterdir():
                if child.is_dir() and child.relative_to(self.root).as_posix() not in used:
                    shutil.rmtree(child, ignore_errors=True)
        return paths

    def snapshots(self, *, through: datetime | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM captures ORDER BY first_seen, id").fetchall()
        cutoff = utc(through) if through is not None else None
        return [dict(row) | {"data": json.loads(row["data"])} for row in rows
                if cutoff is None or row["first_seen"] <= cutoff]

    def reports(self, *, through: datetime | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM reports AS r WHERE r.rowid=(SELECT rowid FROM reports "
                "WHERE market=r.market AND session=r.session AND edition_date=r.edition_date "
                "ORDER BY generated_at DESC, rowid DESC LIMIT 1) ORDER BY generated_at DESC, rowid DESC"
            ).fetchall()
        cutoff = utc(through) if through is not None else None
        return [dict(row) | {"data": json.loads(row["data"])} for row in rows
                if cutoff is None or row["generated_at"] <= cutoff]

    def previous_key_news(self, market: str, session: str, day: date, now: datetime) -> str:
        """只参考同市场最近96小时内上一份完整简报，强制覆盖排除本版。"""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM reports WHERE market=? AND generated_at BETWEEN ? AND ? "
                "AND NOT (session=? AND edition_date=?) ORDER BY generated_at DESC, rowid DESC",
                (market, utc(now - timedelta(hours=96)), utc(now), session, day.isoformat()),
            ).fetchall()
        for row in rows:
            if not self._complete(row):
                continue
            data = json.loads(row["data"])
            news = [NewsItem(**(item | {"published": datetime.fromisoformat(item["published"])}))
                    for item in data["news"]]
            item = select_focus_news(news, datetime.fromisoformat(row["generated_at"]))
            return item.title if item else ""
        return ""

    def add_event(self, event, now: datetime) -> str:
        data = plain(event)
        stamp = utc(datetime.fromisoformat(data["at"]))
        key = identity([data["title"], stamp])
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?)",
                       (key, data["title"], stamp, data["source"], utc(now)))
        return key

    def add_evidence(self, event_id: str, kind: str, published_at: datetime, data: dict, now: datetime) -> str:
        stamp, recorded = utc(published_at), utc(now)
        if stamp > recorded:
            raise ValueError("证据发布时间不能晚于采集时间")
        key = identity([event_id, kind, stamp, data])
        with self.connect(write=True) as db:
            event = db.execute("SELECT at FROM events WHERE id=?", (event_id,)).fetchone()
            if event is None:
                raise ValueError("事件不存在")
            if kind == "expectation" and not stamp < event["at"]:
                raise ValueError("事前预期必须在事件发布前公开")
            if kind == "result" and stamp < event["at"]:
                raise ValueError("发布结果不能早于事件时间")
            db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                       (key, event_id, kind, stamp, recorded, encode(data)))
        return key

    def events(self, *, through: datetime | None = None) -> list[dict]:
        cutoff = utc(through) if through is not None else "9999"
        with self.connect() as db:
            events = db.execute("SELECT * FROM events WHERE first_seen<=? ORDER BY at", (cutoff,)).fetchall()
            evidence = db.execute("SELECT * FROM evidence WHERE recorded_at<=? ORDER BY published_at, id", (cutoff,)).fetchall()
        return [dict(event) | {"evidence": [dict(item) | {"data": json.loads(item["data"])}
                  for item in evidence if item["event_id"] == event["id"]]} for event in events]
