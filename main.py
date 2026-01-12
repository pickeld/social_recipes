import json
import os
from chef import Chef
from config import config
from mealie import Mealie
from tiktok import Tiktok
from transcriber import Transcriber


def main(video_url: str):
    tiktok = Tiktok(video_url)
    item = tiktok._get_info()
    description = item.get("description", "No description available.")
    title = item.get("title", "Untitled")

    vid_id, video_path = tiktok._download_video()
    transcriber = Transcriber(video_path)
    lang = config.TARGET_LANGUAGE
    
    # Get audio transcription (cached with language in filename)
    audio_cache = f"tmp/{vid_id}_{lang}.txt"
    if os.path.exists(audio_cache):
        print(f"Using cached transcription ({lang}).")
        with open(audio_cache, "r") as f:
            transcription = f.read()
    else:
        transcription = transcriber.transcribe()
        with open(audio_cache, "w") as f:
            f.write(transcription)
    
    # Get visual text from video (cached with language in filename)
    visual_text = ""
    visual_cache = f"tmp/{vid_id}_{lang}_visual.txt"
    if os.path.exists(visual_cache):
        print(f"Using cached visual text ({lang}).")
        with open(visual_cache, "r") as f:
            visual_text = f.read()
    else:
        print(f"Extracting on-screen text from video using {config.LLM_PROVIDER} ({lang})...")
        try:
            visual_text = transcriber.extract_visual_text()
            with open(visual_cache, "w") as f:
                f.write(visual_text)
            print(f"Extracted {len(visual_text)} characters of visual text.")
        except Exception as e:
            print(f"Warning: Could not extract visual text: {e}")
    
    # Combine audio transcription and visual text
    combined_transcription = transcription
    if visual_text:
        combined_transcription = f"""=== AUDIO TRANSCRIPTION ===
{transcription}

=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===
{visual_text}"""
    
    return {
        "title": title,
        "description": description,
        "video_path": video_path,
        "transcription": combined_transcription
    }


if __name__ == "__main__":
    url = "https://www.tiktok.com/@recipeincaption/video/7532985862854921477"
    results = main(url)
    chef = Chef(source_url=url, description=results["description"],
                transcription=results["transcription"])

    results = chef.create_recipe()
    if not results:
        print("No recipe created.")
    else:
        if config.OUTPUT_TARGET == "tandoor":
            from tandoor import Tandoor
            tandoor = Tandoor()
            tandoor_recipe = tandoor.create_recipe(results)
        elif config.OUTPUT_TARGET == "mealie":
            mealie = Mealie()
            mealie_recipe = mealie.create_recipe(results)
    print(results)
