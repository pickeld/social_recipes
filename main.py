import argparse
import json
import os
from chef import Chef
from config import config
from mealie import Mealie
from video_downloader import VideoDownloader
from transcriber import Transcriber
from image_extractor import extract_dish_image


def main(video_url: str):
    downloader = VideoDownloader(video_url)
    item = downloader._get_info()
    description = item.get("description", "No description available.")
    title = item.get("title", "Untitled")

    vid_id, video_path = downloader._download_video()
    dish_dir = os.path.join("tmp", vid_id)
    transcriber = Transcriber(video_path)
    lang = config.TARGET_LANGUAGE
    
    # Get audio transcription (cached with language in filename)
    audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")
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
    visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
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
    
    # Extract best dish image from video (cached)
    image_path = None
    image_cache = os.path.join(dish_dir, "dish.jpg")
    if os.path.exists(image_cache):
        print("Using cached dish image.")
        image_path = image_cache
    else:
        print("Extracting best dish image from video...")
        try:
            image_path = extract_dish_image(video_path)
            if image_path:
                print(f"Dish image extracted: {image_path}")
        except Exception as e:
            print(f"Warning: Could not extract dish image: {e}")
    
    return {
        "title": title,
        "description": description,
        "video_path": video_path,
        "transcription": combined_transcription,
        "image_path": image_path
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract recipe from video (supports TikTok, YouTube, Instagram, etc.)")
    parser.add_argument("url", nargs="?",
                        default="https://www.tiktok.com/@recipeincaption/video/7532985862854921477",
                        help="Video URL (TikTok, YouTube, Instagram, etc.)")
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip uploading to recipe manager (for testing)")
    args = parser.parse_args()
    
    url = args.url
    video_results = main(url)
    chef = Chef(source_url=url, description=video_results["description"],
                transcription=video_results["transcription"])

    recipe_data = chef.create_recipe()
    if not recipe_data:
        print("No recipe created.")
    else:
        image_path = video_results.get("image_path")
        
        if not args.no_upload:
            if config.OUTPUT_TARGET == "tandoor":
                from tandoor import Tandoor
                tandoor = Tandoor()
                tandoor_recipe = tandoor.create_recipe(recipe_data)
                
                # Upload dish image if available
                if image_path and tandoor_recipe.get("id"):
                    print("Uploading dish image to Tandoor...")
                    tandoor.upload_image(tandoor_recipe["id"], image_path)
                    
            elif config.OUTPUT_TARGET == "mealie":
                mealie = Mealie()
                mealie_recipe = mealie.create_recipe(recipe_data)
                
                # Upload dish image if available
                recipe_slug = mealie_recipe.get("slug") or mealie_recipe.get("id")
                if image_path and recipe_slug:
                    print("Uploading dish image to Mealie...")
                    mealie.upload_image(recipe_slug, image_path)
                
    print(json.dumps(recipe_data, ensure_ascii=False, indent=2))
