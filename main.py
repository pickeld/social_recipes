import yt_dlp
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from faster_whisper import WhisperModel

from openai_recipie import Chef
from tiktok import Tiktok
from transcriber import Transcriber


def main(video_url: str):

    tiktok = Tiktok(video_url)
    info = tiktok._get_info()
    description = info.get("description", "No description available.")
    title = info.get("title", "Untitled")

    video_path = tiktok._download_video()
    transcriber = Transcriber(video_path)
    transcription_result = transcriber.transcribe()
    return {
        "title": title,
        "description": description,
        "video_path": video_path,
        "transcription": transcription_result
    }


if __name__ == "__main__":
    tiktok_url = "https://www.tiktok.com/@kobiedri/video/7558063455425678599"
    results = main(tiktok_url)
    chef = Chef(description=results["description"],
                transcription=results["transcription"])
    chef.create_recipe()
