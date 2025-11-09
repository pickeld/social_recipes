import os
import yt_dlp


class Tiktok:
    def __init__(self, url):
        self.url = url
        self.video_id = None

    def _get_info(self):
        """Fetch metadata (description, title, etc.) without downloading the video."""
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        self.video_id = info.get("id")
        return info

    def _download_video(self):
        """Download the TikTok video."""
        video_path = os.path.join("tmp", f"{self.video_id}.mp4")
        os.makedirs("tmp", exist_ok=True)
        if os.path.exists(video_path):
            print("Video already downloaded.")
        else:
            ydl_opts = {
                "quiet": True,
                "outtmpl": os.path.join("tmp", f"{self.video_id}.mp4"),
                "format": "mp4",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
        return video_path
