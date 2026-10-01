from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _clock(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cache time must include a timezone")
    return value.astimezone(timezone.utc)


def _path(key: str, cache_dir: str | Path | None) -> Path:
    directory = Path(cache_dir or os.environ.get("BRIEF_CACHE_DIR", ".cache/briefs"))
    name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"
    return directory / name


def _invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON number: {value}")


def read(
    key: str,
    *,
    max_age: float,
    now: datetime | None = None,
    cache_dir: str | Path | None = None,
) -> dict | None:
    """Return a fresh JSON object; absent, invalid or future-dated entries are misses."""
    try:
        if not math.isfinite(max_age) or max_age < 0:
            return None
        current = _clock(now)
        record = json.loads(_path(key, cache_dir).read_text(encoding="utf-8"), parse_constant=_invalid_constant)
        saved_at = _clock(datetime.fromisoformat(record["saved_at"]))
        age = (current - saved_at).total_seconds()
        data = record["data"]
        if not 0 <= age <= max_age or not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def write(
    key: str,
    data: dict,
    *,
    now: datetime | None = None,
    cache_dir: str | Path | None = None,
) -> bool:
    """Atomically save a JSON object without letting cache failures interrupt a brief."""
    temporary: str | None = None
    try:
        if not isinstance(data, dict):
            return False
        payload = json.dumps(
            {"saved_at": _clock(now).isoformat(), "data": data},
            ensure_ascii=False,
            allow_nan=False,
        )
        path = _path(key, cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(payload)
        os.replace(temporary, path)
        return True
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return False
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
