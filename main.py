import json
import os
from chef import Chef
from mealie import Mealie
from tiktok import Tiktok
from transcriber import Transcriber


def main(video_url: str):
    tiktok = Tiktok(video_url)
    item = tiktok._get_info()
    description = item.get("description", "No description available.")
    title = item.get("title", "Untitled")

    vid_id, video_path = tiktok._download_video()
    if os.path.exists(f"tmp/{vid_id}.txt"):
        print("Using cached transcription.")
        with open(f"tmp/{vid_id}.txt", "r") as f:
            transcription = f.read()
    else:
        transcriber = Transcriber(video_path)
        transcription = transcriber.transcribe()
        with open(f"tmp/{vid_id}.txt", "w") as f:
            f.write(transcription)
    return {
        "title": title,
        "description": description,
        "video_path": video_path,
        "transcription": transcription
    }


if __name__ == "__main__":
    url = "https://www.tiktok.com/@recipeincaption/video/7532985862854921477"
    results = main(url)
    chef = Chef(source_url=url, description=results["description"],
                transcription=results["transcription"])

    results = chef.create_recipe()
    # if results:
    #     mealie = Mealie()
    #     mealie_recipe = mealie.create_recipe(results)
    print(results)
