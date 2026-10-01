from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

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
CREATE INDEX IF NOT EXISTS report_editions ON reports(market, session, edition_date, generated_at);
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


@lru_cache(maxsize=1)
def code_version() -> str:
    """程序和字体变更也生成新版本，避免幂等复用阻止排版更新。"""
    root = Path(__file__).resolve().parents[1]
    paths = [path for package in ("common", "ashare", "usstock", "weekly", "review")
             for path in (root / package).rglob("*.py")]
    paths += list((root / "assets" / "fonts").glob("*.ttf"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
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
                # ponytail: 串行发布确保并发幂等；高频发布时再改成独立发布队列。
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

    def publish(self, market: str, brief, capture_id: str, render, text: str, stem: Path) -> tuple[Path, Path]:
        data = plain(brief)
        if market == "ashare":
            data["intraday"] = brief.is_intraday
        session, day = data["kind"], data["edition_date"]
        valid = {"ashare": {"close", "morning"}, "usstock": {"premarket", "postmarket"}, "weekly": {"weekly"}}
        if session not in valid.get(market, set()):
            raise ValueError("不支持的归档版面")
        date.fromisoformat(day)
        stamp = utc(datetime.fromisoformat(data["generated_at"]))
        stable = {key: value for key, value in data.items() if key != "generated_at"}
        version = code_version()
        key = identity([market, capture_id, version, stable])
        relative = Path("reports") / day / f"{market}-{session}" / key
        folder = self.root / relative
        with self.connect(write=True) as db:
            capture = db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
            if capture is None or capture["market"] != market:
                raise ValueError("归档缺少本次原始数据")
            existing = db.execute("SELECT id FROM reports WHERE id=?", (key,)).fetchone()
            if existing is None:
                folder.parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=folder.parent))
                try:
                    render(brief, stage / "image.png")
                    (stage / "summary.txt").write_text(text, encoding="utf-8")
                    (stage / "data.json").write_text(encode({
                        "schema": 1, "capture_id": capture_id, "revision_id": key, "code_version": version,
                        "captured_at": capture["first_seen"], "market": market,
                        "snapshot": json.loads(capture["data"]), "brief": data,
                    }), encoding="utf-8")
                    for artifact in stage.iterdir():
                        with artifact.open("rb") as handle:
                            os.fsync(handle.fileno())
                    _sync_directory(stage)
                    # 提交失败可能留下完整孤立目录；重试采用本次完整产物。
                    if folder.exists():
                        shutil.rmtree(folder)
                    os.replace(stage, folder)
                    _sync_directory(folder.parent)
                    db.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (key, capture_id, market, session, day, stamp, encode(data), relative.as_posix()))
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
            else:
                if not all((folder / name).is_file() for name in ("image.png", "summary.txt", "data.json")):
                    raise OSError(f"归档产物缺失，请从备份恢复：{folder}")
            # 较早任务迟到时保留最新版本；同一输入重试沿用首次生成时间。
            head = db.execute(
                "SELECT path FROM reports WHERE market=? AND session=? AND edition_date=? "
                "ORDER BY generated_at DESC, rowid DESC LIMIT 1", (market, session, day),
            ).fetchone()
            source = self.root / head["path"]
            image, copy = stem.with_suffix(".png"), stem.with_suffix(".txt")
            atomic_write(image, (source / "image.png").read_bytes())
            atomic_write(copy, (source / "summary.txt").read_bytes())
        return image, copy

    def snapshots(self, *, through: datetime | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM captures ORDER BY first_seen, id").fetchall()
        cutoff = utc(through) if through is not None else None
        return [dict(row) | {"data": json.loads(row["data"])} for row in rows
                if cutoff is None or row["first_seen"] <= cutoff]

    def reports(self, *, through: datetime | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM reports ORDER BY generated_at DESC, rowid DESC").fetchall()
        cutoff = utc(through) if through is not None else None
        return [dict(row) | {"data": json.loads(row["data"])} for row in rows
                if cutoff is None or row["generated_at"] <= cutoff]

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
