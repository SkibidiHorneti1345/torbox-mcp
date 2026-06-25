import asyncio
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import prowlarr
import torbox


class FakeProwlarrClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def get(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


class HardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_localhost_prowlarr_url_is_normalized_to_configured_host(self):
        original = torbox.PROWLARR_URL
        torbox.PROWLARR_URL = "http://prowlarr:9696"
        try:
            normalized = torbox._normalize_prowlarr_url(
                "http://localhost:9696/1/download?apikey=secret&file=test"
            )
        finally:
            torbox.PROWLARR_URL = original

        self.assertEqual(
            normalized,
            "http://prowlarr:9696/1/download?apikey=secret&file=test",
        )

    def test_non_prowlarr_http_url_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Only magnet links"):
            torbox._normalize_prowlarr_url("https://example.com/file.torrent")

    def test_redacts_sensitive_url_query_values(self):
        redacted = torbox._redact_url(
            "http://prowlarr:9696/1/download?apikey=secret&token=abc&file=x"
        )
        self.assertIn("apikey=REDACTED", redacted)
        self.assertIn("token=REDACTED", redacted)
        self.assertIn("file=x", redacted)
        self.assertNotIn("secret", redacted)

    async def test_prowlarr_malformed_response_has_non_empty_error(self):
        response = httpx.Response(
            200,
            json={"unexpected": True},
            request=httpx.Request("GET", prowlarr.SEARCH_ENDPOINT),
        )

        with self.assertRaisesRegex(RuntimeError, "Expected a list response"):
            await prowlarr._query_prowlarr(FakeProwlarrClient(response=response), {}, {})

    async def test_prowlarr_timeout_has_non_empty_error(self):
        request = httpx.Request("GET", prowlarr.SEARCH_ENDPOINT)
        client = FakeProwlarrClient(error=httpx.ReadTimeout("", request=request))

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            await prowlarr._query_prowlarr(client, {}, {})


if __name__ == "__main__":
    unittest.main()
