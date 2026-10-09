from __future__ import annotations

import http.client
import math
import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 429/408 and gateway errors are the ones these quote hosts actually recover from.
RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }


def _backoff(attempt: int) -> None:
    time.sleep(min(0.5 * (2 ** attempt), 2.0))


def fetch_bytes(url: str, referer: str, timeout: float = 20, retries: int = 2) -> bytes:
    if not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a nonnegative integer")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    headers = _headers(referer)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            if not body:
                raise urllib.error.URLError("empty response")
            return body
        except urllib.error.HTTPError as exc:
            last_error = exc
            exc.close()
            if exc.code not in RETRYABLE_HTTP or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            last_error = exc
            if attempt == retries:
                raise
        _backoff(attempt)
    raise RuntimeError(f"fetch failed: {url}") from last_error


def fetch_text(
    url: str,
    referer: str,
    encoding: str = "utf-8",
    timeout: float = 15,
    retries: int = 2,
) -> str:
    raw = fetch_bytes(url, referer=referer, timeout=timeout, retries=retries)
    return raw.decode(encoding, errors="replace")
