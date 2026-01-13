import os
import yt_dlp


class VideoDownloader:
    """Downloads and extracts metadata from videos using yt-dlp.
    
    Supports multiple video sources including TikTok, YouTube, Instagram, and others
    supported by yt-dlp.
    """
    
    def __init__(self, url):
        self.url = url
        self.video_id = None

    def _get_info(self):
        """Fetch metadata (description, title, etc.) without downloading the video."""
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        self.video_id = info.get("id")
        return info

    def _download_video(self):
        """Download the video to /tmp/<video_id>/ folder."""
        dish_dir = os.path.join("/tmp", self.video_id)
        video_path = os.path.join(dish_dir, f"{self.video_id}.mp4")
        os.makedirs(dish_dir, exist_ok=True)
        if os.path.exists(video_path):
            print("Video already downloaded.")
        else:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "outtmpl": video_path,
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
        return self.video_id, video_path
