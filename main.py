import argparse
import json
import os
from chef import Chef
from config import config
from helpers import setup_logger
from mealie import Mealie
from video_downloader import VideoDownloader
from transcriber import Transcriber
from image_extractor import extract_dish_image

logger = setup_logger(__name__)


def main(video_url: str):
    # Step: Get Info
    logger.info("[Get Info] Fetching video metadata...")
    downloader = VideoDownloader(video_url)
    item = downloader._get_info()
    description = item.get("description", "No description available.")
    title = item.get("title", "Untitled")
    logger.info(f"[Get Info] Complete. Title: {title}")

    # Step: Download
    logger.info("[Download] Downloading video...")
    vid_id, video_path = downloader._download_video()
    logger.info(f"[Download] Complete. Video saved to: {video_path}")
    
    dish_dir = os.path.join("tmp", vid_id)
    transcriber = Transcriber(video_path)
    lang = config.TARGET_LANGUAGE
    
    # Step: Transcribe - Get audio transcription (cached with language in filename)
    logger.info("[Transcribe] Starting audio transcription...")
    audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")
    if os.path.exists(audio_cache):
        logger.info(f"[Transcribe] Using cached transcription ({lang}).")
        with open(audio_cache, "r") as f:
            transcription = f.read()
    else:
        transcription = transcriber.transcribe()
        with open(audio_cache, "w") as f:
            f.write(transcription)
    logger.info("[Transcribe] Complete.")
    
    # Step: Visual Text - Get visual text from video (cached with language in filename)
    logger.info("[Visual Text] Extracting on-screen text...")
    visual_text = ""
    visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
    if os.path.exists(visual_cache):
        logger.info(f"[Visual Text] Using cached visual text ({lang}).")
        with open(visual_cache, "r") as f:
            visual_text = f.read()
    else:
        logger.info(f"[Visual Text] Extracting on-screen text from video using {config.LLM_PROVIDER} ({lang})...")
        try:
            visual_text = transcriber.extract_visual_text()
            with open(visual_cache, "w") as f:
                f.write(visual_text)
            logger.info(f"[Visual Text] Extracted {len(visual_text)} characters of visual text.")
        except Exception as e:
            logger.warning(f"[Visual Text] Could not extract visual text: {e}")
    logger.info("[Visual Text] Complete.")
    
    # Combine audio transcription and visual text
    combined_transcription = transcription
    if visual_text:
        combined_transcription = f"""=== AUDIO TRANSCRIPTION ===
{transcription}

=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===
{visual_text}"""
    
    # Step: Extract Image - Extract best dish image from video (cached)
    logger.info("[Extract Image] Extracting best dish image from video...")
    image_path = None
    image_cache = os.path.join(dish_dir, "dish.jpg")
    if os.path.exists(image_cache):
        logger.info("[Extract Image] Using cached dish image.")
        image_path = image_cache
    else:
        try:
            image_path = extract_dish_image(video_path)
            if image_path:
                logger.info(f"[Extract Image] Dish image extracted: {image_path}")
        except Exception as e:
            logger.warning(f"[Extract Image] Could not extract dish image: {e}")
    logger.info("[Extract Image] Complete.")
    
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
    
    # Step: AI Recipe
    logger.info("[AI Recipe] Creating recipe from transcription...")
    chef = Chef(source_url=url, description=video_results["description"],
                transcription=video_results["transcription"])

    recipe_data = chef.create_recipe()
    if not recipe_data:
        logger.warning("[AI Recipe] No recipe created.")
    else:
        logger.info("[AI Recipe] Complete.")
        image_path = video_results.get("image_path")
        
        # Step: Upload
        if not args.no_upload:
            logger.info("[Upload] Uploading recipe to recipe manager...")
            if config.OUTPUT_TARGET == "tandoor":
                from tandoor import Tandoor
                tandoor = Tandoor()
                tandoor_recipe = tandoor.create_recipe(recipe_data)
                logger.info("[Upload] Recipe uploaded to Tandoor.")
                
                # Upload dish image if available
                if image_path and tandoor_recipe.get("id"):
                    logger.info("[Upload] Uploading dish image to Tandoor...")
                    tandoor.upload_image(tandoor_recipe["id"], image_path)
                    logger.info("[Upload] Dish image uploaded to Tandoor.")
                    
            elif config.OUTPUT_TARGET == "mealie":
                mealie = Mealie()
                mealie_recipe = mealie.create_recipe(recipe_data)
                logger.info("[Upload] Recipe uploaded to Mealie.")
                
                # Upload dish image if available
                recipe_slug = mealie_recipe.get("slug") or mealie_recipe.get("id")
                if image_path and recipe_slug:
                    logger.info("[Upload] Uploading dish image to Mealie...")
                    mealie.upload_image(recipe_slug, image_path)
                    logger.info("[Upload] Dish image uploaded to Mealie.")
            
            logger.info("[Upload] Complete.")
                
    logger.info(json.dumps(recipe_data, ensure_ascii=False, indent=2))
