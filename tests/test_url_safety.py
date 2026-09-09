"""Tests for outbound URL SSRF guards."""

import os
import socket
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from url_safety import UnsafeURLError, assert_public_http_url, safe_get  # noqa: E402
from web_recipe_fetcher import download_image, fetch_web_recipe  # noqa: E402


def _public_addrinfo(host="example.com"):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]


class AssertPublicHttpUrlTests(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("file:///etc/passwd")
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("ftp://example.com/x")

    def test_rejects_credentials(self):
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("https://user:secret@example.com/recipe")

    def test_rejects_localhost_and_loopback(self):
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://localhost/admin")
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://127.0.0.1/")
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://[::1]/")

    def test_rejects_private_and_link_local(self):
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://192.168.1.10/recipe")
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://10.0.0.5/")
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("http://169.254.169.254/latest/meta-data")

    def test_rejects_non_standard_ports(self):
        with self.assertRaises(UnsafeURLError):
            assert_public_http_url("https://example.com:8443/recipe")

    def test_allows_public_https(self):
        with patch("url_safety.socket.getaddrinfo", return_value=_public_addrinfo()):
            self.assertEqual(
                assert_public_http_url("https://example.com/recipe"),
                "https://example.com/recipe",
            )

    def test_rejects_host_that_resolves_private(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.1.1", 0))]
        with patch("url_safety.socket.getaddrinfo", return_value=private):
            with self.assertRaises(UnsafeURLError):
                assert_public_http_url("https://internal.example.com/")


class SafeGetRedirectTests(unittest.TestCase):
    def test_refuses_redirect_to_loopback(self):
        first = MagicMock()
        first.is_redirect = True
        first.status_code = 302
        first.headers = {"Location": "http://127.0.0.1/secret"}
        session = MagicMock()
        session.get.return_value = first
        with patch("url_safety.socket.getaddrinfo", return_value=_public_addrinfo()):
            with self.assertRaises(UnsafeURLError):
                safe_get(session, "https://example.com/start", timeout=5)


class FetcherIntegrationTests(unittest.TestCase):
    def test_fetch_web_recipe_blocks_loopback(self):
        with self.assertRaises(UnsafeURLError):
            fetch_web_recipe("http://127.0.0.1/recipe")

    def test_pipeline_rejects_loopback_before_download(self):
        import sys
        from unittest.mock import MagicMock

        for name in (
            "faster_whisper", "yt_dlp", "yt_dlp.utils",
            "openai", "google.genai", "google.genai.types",
        ):
            sys.modules.setdefault(name, MagicMock())

        from pipeline import run_url_pipeline

        class Reporter:
            def is_cancelled(self):
                return False

            def update(self, *args, **kwargs):
                pass

        result = run_url_pipeline("http://127.0.0.1/video", Reporter())
        self.assertIsNotNone(result.error)
        self.assertRegex(result.error, r"non-public|not allowed")


class InventoryTests(unittest.TestCase):
    def test_harness_inventory_lists_prompts_and_guardrails(self):
        path = os.path.join(ROOT, "ai-inventory.yaml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("get_recipe_system_prompt", text)
        self.assertIn("get_web_recipe_system_prompt", text)
        self.assertIn("get_yield_nutrition_prompt", text)
        self.assertIn("ensure_is_recipe", text)
        self.assertIn("validate_public_http_url", text)
        self.assertIn("output_filter_nutrition_bounds", text)
        self.assertIn("tools: []", text)



if __name__ == "__main__":
    unittest.main()
