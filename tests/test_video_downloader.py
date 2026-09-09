import unittest
from unittest.mock import patch

from url_safety import UnsafeURLError
from video_downloader import (
    DownloadError,
    VideoDownloader,
    _is_retryable,
    format_download_error,
)


class TestFormatDownloadError(unittest.TestCase):
    def test_instagram_empty_media_response(self):
        exc = Exception(
            "ERROR: [Instagram] DabyOp9CN8n: Instagram sent an empty media response."
        )
        msg = format_download_error(
            exc, "https://www.instagram.com/reel/DabyOp9CN8n/"
        )
        self.assertIn("Instagram blocked the download", msg)
        self.assertIn("cookies.txt", msg)

    def test_instagram_missing_impersonation(self):
        exc = Exception(
            "The extractor is attempting impersonation, but no impersonate target is available."
        )
        msg = format_download_error(
            exc, "https://www.instagram.com/reel/abc123/"
        )
        self.assertIn("curl-cffi", msg)

    def test_youtube_bot_check(self):
        exc = Exception("Sign in to confirm you're not a bot")
        msg = format_download_error(
            exc, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.assertIn("YouTube blocked the download", msg)
        self.assertIn("retry", msg.lower())

    def test_passthrough_for_unknown_errors(self):
        exc = Exception("Something else went wrong")
        msg = format_download_error(exc, "https://example.com/video")
        self.assertEqual("Something else went wrong", msg)

    def test_bot_check_is_not_retryable(self):
        self.assertFalse(
            _is_retryable(Exception("Sign in to confirm you're not a bot"))
        )

    def test_transient_errors_are_retryable(self):
        self.assertTrue(_is_retryable(Exception("HTTP Error 429: Too Many Requests")))
        self.assertTrue(_is_retryable(Exception("Unable to download webpage")))
        self.assertTrue(_is_retryable(Exception("Unexpected response from webpage")))

    def test_pin_failure_is_a_download_error(self):
        downloader = VideoDownloader("https://example.com/watch")
        with patch(
            "url_safety.pin_public_http_url",
            side_effect=UnsafeURLError("Host is not allowed"),
        ):
            with self.assertRaises(DownloadError) as ctx:
                downloader._pin_source_url()
        self.assertIn("not safe", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
