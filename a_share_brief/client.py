from __future__ import annotations

import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def fetch_bytes(url: str, referer: str, timeout: float = 20, retries: int = 2) -> bytes:
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "*/*",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt == retries:
                raise
        time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}") from last_error


def fetch_text(
    url: str,
    referer: str,
    encoding: str = "utf-8",
    timeout: float = 15,
    retries: int = 1,
) -> str:
    raw = fetch_bytes(url, referer=referer, timeout=timeout, retries=retries)
    return raw.decode(encoding, errors="replace")
