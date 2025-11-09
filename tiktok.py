import yt_dlp


class Tiktok:
    def __init__(self, url):
        self.url = url

    def _get_info(self):
        """Fetch metadata (description, title, etc.) without downloading the video."""
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        return info

    def _download_video(self, output_path="tiktok_video.mp4"):
        """Download the TikTok video."""
        ydl_opts = {
            "quiet": True,
            "outtmpl": output_path,
            "format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.url])
        return output_path
