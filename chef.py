import json
import logging
import re
from datetime import datetime, timezone

from config import config
from helpers import get_recipe_system_prompt, get_yield_nutrition_prompt

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Extract JSON from text, stripping markdown code blocks if present."""
    if not text:
        return text
    text = text.strip()
    # Remove markdown code blocks (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        # Find the end of the first line (language specifier)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove trailing ```
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


class Chef:
    def __init__(self, source_url: str, description: str, transcription: str, *, model: str | None = None):
        self.provider = config.LLM_PROVIDER
        
        if self.provider == "openai":
            from openai import OpenAI
            logger.info("Using OpenAI LLM provider")
            self.client = OpenAI(api_key=config.OPENAI_API_KEY)
            self.model = model or config.OPENAI_MODEL
        elif self.provider == "gemini":
            from google import genai
            logger.info("Using Gemini LLM provider")
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            self.model = model or config.GEMINI_MODEL
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")
        self.source_url = source_url
        self.description = description
        self.transcription = transcription

    def _call_llm(self, system_prompt: str, user_content: str) -> str:
        """Call the LLM and return the response text, abstracting provider differences."""
        if self.provider == "openai":
            resp = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            return resp.output_text
        elif self.provider == "gemini":
            resp = self.client.models.generate_content(
                model=self.model,
                contents=f"{system_prompt}\n\n{user_content}"
            )
            raw_text = resp.text or ""
            logger.debug(f"Gemini raw response: {raw_text[:500]}...")
            # Extract JSON from markdown code blocks if present
            return _extract_json(raw_text)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def _postprocess_recipe(self, data: dict, source_url: str | None) -> dict:
        data.setdefault("@context", "https://schema.org")
        data.setdefault("@type", "Recipe")
        data.setdefault("url", source_url or self.source_url)
        data.setdefault("video", {"@type": "VideoObject", "url": source_url or self.source_url})

        # Ensure valid date
        dp = data.get("datePublished")
        if not isinstance(dp, str) or len(dp) <= 10:
            data["datePublished"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Clean and deduplicate recipeIngredients ---
        ingredients = data.get("recipeIngredients") or []
        clean = []
        seen_foods: dict[str, int] = {}  # Map food name (casefolded) to index in clean list
        
        for i in ingredients:
            if not isinstance(i, dict):
                continue
            food = " ".join(str(i.get("food", "")).split()).strip()
            qty = " ".join(str(i.get("quantity", "")).split()).strip()
            unit = " ".join(str(i.get("unit", "")).split()).strip()
            note = " ".join(str(i.get("note", "")).split()).strip()
            if not food:
                continue
            
            food_key = food.casefold()
            if food_key in seen_foods:
                # Merge duplicate: combine quantities or notes
                existing_idx = seen_foods[food_key]
                existing = clean[existing_idx]
                
                # If same quantity and unit, just merge notes
                if existing["quantity"] == qty and existing["unit"] == unit:
                    if note and note not in existing["note"]:
                        if existing["note"]:
                            existing["note"] = f"{existing['note']}, {note}"
                        else:
                            existing["note"] = note
                # If different quantities, combine them (e.g., "1 + 1" or just add second amount)
                elif qty and existing["quantity"]:
                    # Try to add numeric quantities
                    try:
                        existing_num = float(existing["quantity"].replace(",", "."))
                        new_num = float(qty.replace(",", "."))
                        if existing["unit"] == unit:
                            # Same unit, sum them up
                            total = existing_num + new_num
                            existing["quantity"] = str(int(total) if total == int(total) else total)
                            if note and note not in existing["note"]:
                                if existing["note"]:
                                    existing["note"] = f"{existing['note']}, {note}"
                                else:
                                    existing["note"] = note
                        else:
                            # Different units, keep both as separate entries
                            clean.append({"food": food, "quantity": qty, "unit": unit, "note": note})
                    except ValueError:
                        # Non-numeric quantities, keep as separate entries
                        clean.append({"food": food, "quantity": qty, "unit": unit, "note": note})
                else:
                    # One or both have no quantity, keep both
                    clean.append({"food": food, "quantity": qty, "unit": unit, "note": note})
            else:
                seen_foods[food_key] = len(clean)
                clean.append({"food": food, "quantity": qty, "unit": unit, "note": note})

        # overwrite structured list
        data["recipeIngredients"] = clean

        # overwrite Schema.org recipeIngredient with a simple flattened list
        flattened = []
        for i in clean:
            parts = [i["quantity"], i["unit"], i["food"], i["note"]]
            line = " ".join(p for p in parts if p).strip().replace("–", "-")
            if line:
                flattened.append(line)
        data["recipeIngredient"] = flattened

        return data

    def create_recipe(self, *, source_url: str | None = None, max_retries: int = 3) -> dict:
        payload = {
            "source_url": source_url,
            "description": self.description,
            "transcript": self.transcription,
        }

        last_error = None
        for attempt in range(max_retries):
            try:
                response_text = self._call_llm(
                    get_recipe_system_prompt(),
                    json.dumps(payload, ensure_ascii=False)
                )
                data = json.loads(response_text)
                recipe = self._postprocess_recipe(data, source_url)
                recipe = self._enrich_yield_and_nutrition(recipe)
                return recipe
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(f"JSON parsing failed (attempt {attempt + 1}/{max_retries}): {e}")
                logger.debug(f"Raw response: {response_text[:500]}...")
                if attempt < max_retries - 1:
                    continue
        
        raise RuntimeError(
            f"Failed to parse LLM response as JSON after {max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _enrich_yield_and_nutrition(self, recipe: dict) -> dict:
        need_yield = "recipeYield" not in recipe
        need_nutrition = "nutrition" not in recipe
        need_prep_time = "prepTime" not in recipe
        need_cook_time = "cookTime" not in recipe
        need_total_time = "totalTime" not in recipe

        if not (need_yield or need_nutrition or need_prep_time or need_cook_time or need_total_time):
            return recipe  # Nothing to enrich

        # Prepare input for the LLM: ingredients list + instructions
        payload = {
            "language_hint": config.RECIPE_LANG,
            "ingredients": recipe.get("recipeIngredient", []),
            "instructions": [
                (step.get("text") if isinstance(step, dict) else str(step))
                for step in (recipe.get("recipeInstructions") or [])
            ]
        }

        response_text = self._call_llm(
            get_yield_nutrition_prompt(),
            json.dumps(payload, ensure_ascii=False)
        )
        try:
            est = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Nutrition/servings/time estimation failed: {e}\nRaw:\n{response_text}")

        # Apply yield if needed
        if need_yield:
            ry = est.get("recipeYield")
            servings = est.get("servings")
            if not ry and isinstance(servings, int) and servings > 0:
                ry = f"{servings} servings"
            if ry:
                recipe["recipeYield"] = str(ry)

        # Apply time estimates if needed
        if need_prep_time and est.get("prepTime"):
            recipe["prepTime"] = str(est["prepTime"])
            logger.info(f"Estimated prepTime: {est['prepTime']}")
            
        if need_cook_time and est.get("cookTime"):
            recipe["cookTime"] = str(est["cookTime"])
            logger.info(f"Estimated cookTime: {est['cookTime']}")
            
        if need_total_time and est.get("totalTime"):
            recipe["totalTime"] = str(est["totalTime"])
            logger.info(f"Estimated totalTime: {est['totalTime']}")

        # Apply nutrition if needed
        if need_nutrition and isinstance(est.get("nutrition"), dict):
            allowed = {
                "@type", "calories", "proteinContent", "fatContent", "carbohydrateContent",
                "fiberContent", "sugarContent", "sodiumContent", "cholesterolContent"
            }
            nutrition = {"@type": "NutritionInformation"}
            for k, v in est["nutrition"].items():
                if k in allowed and v:
                    nutrition[k] = str(v)
            # Add nutrition if we have at least one valid field
            if any(k in nutrition for k in ("calories", "proteinContent", "fatContent",
                                            "carbohydrateContent", "fiberContent",
                                            "sugarContent", "sodiumContent", "cholesterolContent")):
                recipe["nutrition"] = nutrition

        return recipe
