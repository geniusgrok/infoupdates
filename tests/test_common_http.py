from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

from common.http import fetch_bytes, fetch_text


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.com", code, "unavailable", {}, None)


class HttpTests(unittest.TestCase):
    def test_transient_failures_recover_with_bounded_retries(self):
        with patch("common.http.urllib.request.urlopen", side_effect=[
            urllib.error.URLError("connection lost"), http_error(503), io.BytesIO(b"ready"),
        ]) as request, patch("common.http.time.sleep") as sleep:
            self.assertEqual(fetch_bytes("https://example.com", "https://example.com"), b"ready")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertLessEqual(max(call.args[0] for call in sleep.call_args_list), 1.2)

    def test_permanent_http_error_is_not_retried(self):
        error = http_error(404)
        with patch("common.http.urllib.request.urlopen", side_effect=error) as request, \
                patch("common.http.time.sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                fetch_bytes("https://example.com", "https://example.com")
        self.assertIs(raised.exception, error)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_exhausted_retries_keep_original_exception(self):
        error = TimeoutError("timed out")
        with patch("common.http.urllib.request.urlopen", side_effect=error) as request, \
                patch("common.http.time.sleep") as sleep:
            with self.assertRaises(TimeoutError) as raised:
                fetch_bytes("https://example.com", "https://example.com", retries=1)
        self.assertIs(raised.exception, error)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_zero_retries_raises_server_error_immediately(self):
        with patch("common.http.urllib.request.urlopen", side_effect=http_error(503)) as request, \
                patch("common.http.time.sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                fetch_bytes("https://example.com", "https://example.com", retries=0)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_decode_preserves_valid_text_and_replaces_invalid_bytes(self):
        with patch("common.http.urllib.request.urlopen", return_value=io.BytesIO(b"market \xff")):
            self.assertEqual(fetch_text("https://example.com", "https://example.com"), "market \ufffd")

    def test_bad_retry_or_timeout_does_not_start_network_request(self):
        for options in ({"retries": -1}, {"retries": 0.5}, {"timeout": 0}, {"timeout": float("inf")}):
            with self.subTest(options=options), patch("common.http.urllib.request.urlopen") as request:
                with self.assertRaises(ValueError):
                    fetch_bytes("https://example.com", "https://example.com", **options)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
