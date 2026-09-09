import os
import time
from helpers import setup_logger

logger = setup_logger(__name__)

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover - exercised in unit tests without yt-dlp
    yt_dlp = None

    class DownloadError(Exception):
        """Raised when a video cannot be downloaded."""

logger = setup_logger(__name__)

INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com")

# Transient extractor/network flakes worth retrying. YouTube's bot-check is not
# in this list — cookies are required, extra attempts will not help.
_RETRYABLE_FRAGMENTS = (
    "universal data for rehydration",
    "unexpected response from webpage",
    "unable to extract challenge data",
    "unable to download webpage",
    "http error 429",
    "too many requests",
    "the page didn't exist",
)

_TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com")


def _is_instagram_url(url: str) -> bool:
    """Return True if the URL points to Instagram."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return host in INSTAGRAM_HOSTS or host.endswith(".instagram.com")
    except Exception:
        return "instagram.com" in (url or "").lower()


def _is_tiktok_url(url: str) -> bool:
    """Return True if the URL points to TikTok."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return host in _TIKTOK_HOSTS or host.endswith(".tiktok.com")
    except Exception:
        return "tiktok.com" in (url or "").lower()


def _is_youtube_bot_check(exc: Exception) -> bool:
    lower = str(exc).lower()
    return "sign in to confirm" in lower or "not a bot" in lower


def _is_retryable(exc: Exception) -> bool:
    """Return True if a DownloadError is a transient failure worth retrying."""
    if _is_youtube_bot_check(exc):
        return False
    msg = str(exc).lower()
    if "429" in msg:
        return True
    return any(fragment in msg for fragment in _RETRYABLE_FRAGMENTS)


def format_download_error(exc: Exception, url: str) -> str:
    """Turn yt-dlp errors into actionable messages for the UI."""
    message = str(exc)
    lower = message.lower()

    if _is_instagram_url(url):
        if "empty media response" in lower or "not granting access" in lower:
            return (
                "Instagram blocked the download. Public reels need browser impersonation "
                "(curl-cffi); private or age-restricted posts also need cookies. "
                "Update to the latest Pick-a-Recipe image, then in Settings upload a "
                "cookies.txt exported while logged into instagram.com. "
                "Then retry from Tasks or the job page."
            )
        if "impersonation" in lower and "no impersonate target" in lower:
            return (
                "Instagram downloads require the curl-cffi dependency. "
                "Update Pick-a-Recipe or reinstall with: pip install \"yt-dlp[curl-cffi]\""
            )

    if _is_youtube_bot_check(exc):
        return (
            "YouTube blocked the download. Upload a cookies.txt file in Settings "
            "(exported while logged into YouTube), then retry from Tasks or the job page."
        )

    if "cookies" in lower and ("login" in lower or "authentication" in lower):
        return (
            "This video requires authentication. Upload a cookies.txt file in Settings "
            "for the relevant site, then retry from Tasks or the job page."
        )

    return message


class VideoDownloader:
    """Downloads and extracts metadata from videos using yt-dlp.

    Supports multiple video sources including TikTok, YouTube, Instagram, and others
    supported by yt-dlp.
    """

    _MAX_RETRIES = 3
    _RETRY_DELAY = 2  # seconds between attempts

    def __init__(self, url):
        self.url = url
        self.video_id = None
        logger.debug(f"VideoDownloader initialized with URL: {url}")

    def _pin_source_url(self):
        """Reject private/non-http video URLs. CDN hops stay yt-dlp's job."""
        from url_safety import UnsafeURLError, pin_public_http_url

        try:
            pin_public_http_url(self.url)
        except UnsafeURLError as exc:
            raise DownloadError(
                f"This video URL is not safe to fetch: {exc}"
            ) from exc

    def _get_cookie_options(self):
        """Get yt-dlp cookie options from configuration.

        Returns a dict with cookie options if configured, empty dict otherwise.
        Supports two modes:
        1. cookies_file: Path to a Netscape-format cookies.txt file
        2. cookies_browser: Browser name to extract cookies from (e.g., 'chrome', 'firefox')
        """
        from config import config
        config.reload()  # Ensure we have latest config

        cookie_opts = {}

        # Priority: cookies file > cookies from browser
        cookies_file = config.YT_DLP_COOKIES_FILE
        cookies_browser = config.YT_DLP_COOKIES_BROWSER

        if cookies_file and os.path.exists(cookies_file):
            cookie_opts['cookiefile'] = cookies_file
            logger.debug(f"Using cookies file: {cookies_file}")
        elif cookies_browser:
            cookie_opts['cookiesfrombrowser'] = (cookies_browser,)
            logger.debug(f"Using cookies from browser: {cookies_browser}")

        return cookie_opts

    def _base_ydl_opts(self):
        """Shared yt-dlp options for info extraction and download."""
        return {
            "quiet": True,
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **self._get_cookie_options(),
        }

    def _require_ydl(self):
        if yt_dlp is None:
            raise DownloadError("yt-dlp is not installed")
        return yt_dlp

    def _run_with_retries(self, operation, *, action: str):
        last_exc = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return operation()
            except DownloadError as exc:
                last_exc = exc
                if _is_retryable(exc) and attempt < self._MAX_RETRIES:
                    logger.warning(
                        f"{action} attempt {attempt}/{self._MAX_RETRIES} failed "
                        f"({exc}); retrying in {self._RETRY_DELAY}s…"
                    )
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise DownloadError(format_download_error(exc, self.url)) from exc
        raise DownloadError(format_download_error(last_exc, self.url)) from last_exc

    def _get_info(self):
        """Fetch metadata (description, title, etc.) without downloading the video."""
        logger.debug(f"Fetching video info for: {self.url}")
        self._pin_source_url()
        ydl_opts = {
            **self._base_ydl_opts(),
            "skip_download": True,
        }

        def _extract():
            ydl_mod = self._require_ydl()
            with ydl_mod.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(self.url, download=False)

        info = self._run_with_retries(_extract, action="Info extract")
        self.video_id = info.get("id")
        logger.debug(f"Video ID extracted: {self.video_id}")
        return info

    def _download_video(self):
        """Download the video to /tmp/<video_id>/ folder."""
        dish_dir = os.path.join("/tmp", self.video_id)
        video_path = os.path.join(dish_dir, f"{self.video_id}.mp4")
        os.makedirs(dish_dir, exist_ok=True)
        logger.debug(f"Downloading video to: {video_path}")
        if os.path.exists(video_path):
            logger.info("Video already downloaded.")
        else:
            logger.debug(f"Starting download from: {self.url}")
            self._pin_source_url()
            ydl_opts = {
                **self._base_ydl_opts(),
                "outtmpl": video_path,
                # Prefer a single merged file with audio; fall back to mux or best available.
                "format": "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
                "merge_output_format": "mp4",
            }

            def _download():
                ydl_mod = self._require_ydl()
                with ydl_mod.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([self.url])

            self._run_with_retries(_download, action="Download")
            logger.debug(f"Download completed: {video_path}")
        return self.video_id, video_path
