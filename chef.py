import json
from datetime import datetime, timezone
from typing import Any

from config import config
from helpers import get_recipe_system_prompt, get_web_recipe_system_prompt, get_yield_nutrition_prompt, setup_logger
from llm_resilience import call_with_model_fallback
from recipe_schema import (
    NUTRITION_JSON_SCHEMA,
    RECIPE_JSON_SCHEMA,
    NotARecipeError,
    RecipeExtraction,
    ValidationError,
    YieldNutritionEstimate,
    apply_yield_nutrition_guardrails,
    ensure_is_recipe,
    ensure_target_language,
    extract_json,
    parse_recipe_extraction,
    parse_yield_nutrition,
    recipe_dict_from_extraction,
)
from nutrition import lookup_recipe_nutrition
from units import normalize_ingredient_units

logger = setup_logger(__name__)


class RecipeEditError(ValueError):
    """User-edited preview JSON is not a usable recipe."""


def build_recipe_user_payload(
    *,
    source_url: str | None,
    description: str,
    audio_transcript: str,
    visual_text: str = "",
    previous_attempt_error: str | None = None,
) -> dict[str, Any]:
    """Packet Chef sends the model. On-screen text outranks audio on conflict."""
    payload: dict[str, Any] = {
        "source_url": source_url,
        "description": description or "",
        "on_screen_text": visual_text or "",
        "audio_transcript": audio_transcript or "",
        "quantity_rule": (
            "Prefer on_screen_text for ingredients and quantities. "
            "Use audio_transcript only to fill gaps. "
            "Never invent a quantity or unit; use empty strings if not clearly present."
        ),
    }
    if previous_attempt_error:
        payload["previous_attempt_error"] = previous_attempt_error
        payload["correction_hint"] = (
            "Fix the JSON, schema, or language issue in previous_attempt_error. "
            "Do not invent quantities to make the recipe look complete."
        )
    return payload


def postprocess_recipe(data: dict, source_url: str | None) -> dict:
    """Normalize a Recipe dict for upload (also used when the user edits preview)."""
    data.setdefault("@context", "https://schema.org")
    data.setdefault("@type", "Recipe")
    data.setdefault("url", source_url or "")
    data.setdefault("video", {"@type": "VideoObject", "url": source_url or data.get("url") or ""})

    dp = data.get("datePublished")
    if not isinstance(dp, str) or len(dp) <= 10:
        data["datePublished"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ingredients = data.get("recipeIngredients") or []
    clean = []
    seen_foods: dict[str, int] = {}

    for i in ingredients:
        if not isinstance(i, dict):
            continue
        food = " ".join(str(i.get("food", "")).split()).strip()
        qty = " ".join(str(i.get("quantity", "")).split()).strip()
        unit = " ".join(str(i.get("unit", "")).split()).strip()
        notes = " ".join(str(i.get("notes", "")).split()).strip()
        raw_from_llm = (i.get("raw") or "").strip()
        if not food:
            continue

        food_key = food.casefold()
        raw_line = raw_from_llm or " ".join(p for p in [qty, unit, food, notes] if p).strip()

        if food_key in seen_foods:
            existing_idx = seen_foods[food_key]
            existing = clean[existing_idx]

            if existing["quantity"] == qty and existing["unit"] == unit:
                if notes and notes not in existing["notes"]:
                    if existing["notes"]:
                        existing["notes"] = f"{existing['notes']}, {notes}"
                    else:
                        existing["notes"] = notes
            elif qty and existing["quantity"]:
                try:
                    existing_num = float(existing["quantity"].replace(",", "."))
                    new_num = float(qty.replace(",", "."))
                    if existing["unit"] == unit:
                        total = existing_num + new_num
                        existing["quantity"] = str(int(total) if total == int(total) else total)
                        if notes and notes not in existing["notes"]:
                            if existing["notes"]:
                                existing["notes"] = f"{existing['notes']}, {notes}"
                            else:
                                existing["notes"] = notes
                    else:
                        clean.append({"food": food, "quantity": qty, "unit": unit, "notes": notes, "raw": raw_line})
                except ValueError:
                    clean.append({"food": food, "quantity": qty, "unit": unit, "notes": notes, "raw": raw_line})
            else:
                clean.append({"food": food, "quantity": qty, "unit": unit, "notes": notes, "raw": raw_line})
        else:
            seen_foods[food_key] = len(clean)
            clean.append({"food": food, "quantity": qty, "unit": unit, "notes": notes, "raw": raw_line})

    data["recipeIngredients"] = clean
    logger.info(f"[Chef] Processed {len(clean)} ingredients")
    normalize_ingredient_units(clean)

    flattened = []
    for i in clean:
        parts = [i["quantity"], i["unit"], i["food"], i.get("notes", "")]
        line = " ".join(p for p in parts if p).strip().replace("–", "-")
        if line:
            flattened.append(line)
    data["recipeIngredient"] = flattened

    steps = data.get("recipeInstructions") or []
    clean_steps = []
    for step in steps:
        if isinstance(step, dict):
            text = str(step.get("text", "")).strip()
        else:
            text = str(step).strip()
        if text:
            clean_steps.append({"@type": "HowToStep", "text": text})
    data["recipeInstructions"] = clean_steps

    if not str(data.get("recipeYield") or "").strip():
        data.pop("recipeYield", None)

    return data


def apply_confirmed_recipe(existing: dict, patch: dict) -> dict:
    """Merge a preview edit onto the stored recipe and re-normalize."""
    if not isinstance(patch, dict):
        raise RecipeEditError("Recipe must be a JSON object")
    merged = dict(existing or {})
    if "name" in patch:
        merged["name"] = str(patch.get("name") or "").strip()
    if "description" in patch:
        merged["description"] = str(patch.get("description") or "").strip()
    if "recipeYield" in patch:
        merged["recipeYield"] = str(patch.get("recipeYield") or "").strip()
    if "recipeIngredients" in patch:
        if not isinstance(patch["recipeIngredients"], list):
            raise RecipeEditError("Ingredients must be a list")
        merged["recipeIngredients"] = patch["recipeIngredients"]
    if "recipeInstructions" in patch:
        if not isinstance(patch["recipeInstructions"], list):
            raise RecipeEditError("Instructions must be a list")
        merged["recipeInstructions"] = patch["recipeInstructions"]

    result = postprocess_recipe(merged, merged.get("url") or existing.get("url"))
    name = str(result.get("name") or "").strip()
    ings = result.get("recipeIngredients") or []
    steps = result.get("recipeInstructions") or []
    if not name:
        raise RecipeEditError("Recipe needs a name")
    if not ings and not steps:
        raise RecipeEditError("Recipe needs ingredients or steps")
    looked = lookup_recipe_nutrition(result)
    if looked:
        result["nutrition"] = looked
    else:
        result.pop("nutrition", None)
    return result


class Chef:
    def __init__(
        self,
        source_url: str,
        description: str,
        transcription: str,
        *,
        visual_text: str = "",
        model: str | None = None,
    ):
        logger.info("[AI Recipe] Initializing Chef...")
        self.provider = config.LLM_PROVIDER
        
        if self.provider == "openai":
            from openai import OpenAI
            logger.info("[AI Recipe] Using OpenAI LLM provider")
            self.client = OpenAI(api_key=config.OPENAI_API_KEY or "not-configured")
            self.model = model or config.OPENAI_MODEL
            logger.info(f"[AI Recipe] OpenAI model: {self.model}")
        elif self.provider == "gemini":
            from google import genai
            logger.info("[AI Recipe] Using Gemini LLM provider")
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            self.model = model or config.GEMINI_MODEL
            logger.info(f"[AI Recipe] Gemini model: {self.model}")
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")
        self.source_url = source_url
        self.description = description
        self.transcription = transcription
        self.visual_text = visual_text or ""
        logger.info(
            f"[AI Recipe] Chef initialized. Audio {len(transcription)} chars, "
            f"on-screen {len(self.visual_text)} chars"
        )

    def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        *,
        schema_name: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_model: type | None = None,
    ) -> str:
        """Call the LLM and return the response text, abstracting provider differences.

        Wrapped with a model-fallback chain so a retired/deprecated model (404)
        transparently fails over to a known-good model instead of taking the
        whole extraction down (see PIC-34).

        When ``json_schema`` / ``schema_model`` are provided the provider is
        asked for structured JSON so we do not have to scrape markdown fences.
        """
        def _openai(model: str) -> str:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            if json_schema is not None and schema_name:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": json_schema,
                    }
                }
            resp = self.client.responses.create(**kwargs)
            return resp.output_text

        def _gemini(model: str) -> str:
            from google.genai import types

            kwargs: dict[str, Any] = {
                "model": model,
                "contents": f"{system_prompt}\n\n{user_content}",
            }
            if schema_model is not None:
                kwargs["config"] = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema_model,
                )
            resp = self.client.models.generate_content(**kwargs)
            raw_text = resp.text or ""
            logger.debug(f"Gemini raw response: {raw_text[:500]}...")
            return extract_json(raw_text)

        if self.provider == "openai":
            call = _openai
        elif self.provider == "gemini":
            call = _gemini
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

        result, used_model = call_with_model_fallback(self.provider, self.model, call)
        # Stick with the working model for the rest of this Chef's lifetime.
        self.model = used_model
        return result

    def _postprocess_recipe(self, data: dict, source_url: str | None) -> dict:
        return postprocess_recipe(data, source_url or self.source_url)

    def create_recipe(self, *, source_url: str | None = None, max_retries: int = 3) -> dict:
        logger.info("[AI Recipe] Starting recipe creation from transcription...")
        payload = build_recipe_user_payload(
            source_url=source_url or self.source_url,
            description=self.description,
            audio_transcript=self.transcription,
            visual_text=self.visual_text,
        )

        extraction = self._extract_recipe(
            get_recipe_system_prompt(),
            json.dumps(payload, ensure_ascii=False),
            max_retries=max_retries,
        )
        recipe = self._postprocess_recipe(
            recipe_dict_from_extraction(extraction), source_url
        )
        logger.info(
            f"[AI Recipe] Recipe postprocessed. Ingredients: "
            f"{len(recipe.get('recipeIngredient', []))}, "
            f"Steps: {len(recipe.get('recipeInstructions', []))}"
        )
        recipe = self._enrich_yield_and_nutrition(recipe)
        logger.info("[AI Recipe] Recipe creation complete.")
        return recipe

    def create_recipe_from_web_content(
        self,
        *,
        page_text: str,
        structured_data: dict | None = None,
        source_url: str | None = None,
        max_retries: int = 3,
    ) -> dict:
        """Create a normalised recipe from a scraped web page.

        If ``structured_data`` is a Schema.org Recipe dict we include it
        verbatim so the LLM can use accurate quantities/steps instead of
        guessing from raw text.  The raw ``page_text`` is always appended as a
        fallback context.
        """
        logger.info("[AI Recipe] Starting recipe creation from web content...")

        payload: dict = {"source_url": source_url or self.source_url}

        if structured_data:
            payload["structured_recipe"] = structured_data
            logger.info("[AI Recipe] Structured Schema.org Recipe included in payload")

        if page_text:
            payload["page_text"] = page_text

        extraction = self._extract_recipe(
            get_web_recipe_system_prompt(),
            json.dumps(payload, ensure_ascii=False),
            max_retries=max_retries,
        )
        recipe = self._postprocess_recipe(
            recipe_dict_from_extraction(extraction), source_url
        )
        logger.info(
            f"[AI Recipe] Recipe postprocessed. "
            f"Ingredients: {len(recipe.get('recipeIngredient', []))}, "
            f"Steps: {len(recipe.get('recipeInstructions', []))}"
        )
        recipe = self._enrich_yield_and_nutrition(recipe)
        logger.info("[AI Recipe] Web recipe creation complete.")
        return recipe

    def _extract_recipe(
        self, system_prompt: str, user_content: str, *, max_retries: int
    ) -> RecipeExtraction:
        last_error: BaseException | None = None
        response_text = ""
        original_content = user_content
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"[AI Recipe] Calling LLM to generate recipe "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )
                attempt_content = original_content
                if last_error is not None:
                    try:
                        payload = json.loads(original_content)
                    except json.JSONDecodeError:
                        payload = {"original": original_content}
                    if not isinstance(payload, dict):
                        payload = {"original": original_content}
                    payload["previous_attempt_error"] = str(last_error)
                    payload["correction_hint"] = (
                        "Fix the JSON, schema, or language issue in previous_attempt_error. "
                        "Do not invent quantities to make the recipe look complete."
                    )
                    attempt_content = json.dumps(payload, ensure_ascii=False)
                response_text = self._call_llm(
                    system_prompt,
                    attempt_content,
                    schema_name="recipe",
                    json_schema=RECIPE_JSON_SCHEMA,
                    schema_model=RecipeExtraction,
                )
                logger.info(
                    f"[AI Recipe] LLM response received ({len(response_text)} chars)"
                )
                extraction = parse_recipe_extraction(response_text)
                ensure_is_recipe(extraction)
                ensure_target_language(extraction, config.TARGET_LANGUAGE)
                logger.info(
                    f"[AI Recipe] Recipe parsed. Name: {extraction.name or 'Unknown'}"
                )
                return extraction
            except NotARecipeError:
                raise
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    f"[AI Recipe] Recipe validation failed "
                    f"(attempt {attempt + 1}/{max_retries}): {exc}"
                )
                logger.debug(f"[AI Recipe] Raw response: {response_text[:500]}...")
                if attempt < max_retries - 1:
                    continue
        raise RuntimeError(
            f"Failed to parse LLM response as a recipe after {max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _enrich_yield_and_nutrition(self, recipe: dict) -> dict:
        need_yield = not str(recipe.get("recipeYield") or "").strip()
        need_prep_time = not str(recipe.get("prepTime") or "").strip()
        need_cook_time = not str(recipe.get("cookTime") or "").strip()
        need_total_time = not str(recipe.get("totalTime") or "").strip()

        looked = lookup_recipe_nutrition(recipe)
        if looked:
            recipe["nutrition"] = looked
            logger.info("[Chef] Nutrition from local/USDA table: %s", looked)
        else:
            recipe.pop("nutrition", None)

        if not (need_yield or need_prep_time or need_cook_time or need_total_time):
            return recipe

        payload = {
            "language_hint": config.RECIPE_LANG,
            "ingredients": recipe.get("recipeIngredient", []),
            "instructions": [
                (step.get("text") if isinstance(step, dict) else str(step))
                for step in (recipe.get("recipeInstructions") or [])
            ],
            "nutrition_rule": "Leave nutrition fields empty. Macros come from the ingredient table, not guesses.",
        }

        response_text = self._call_llm(
            get_yield_nutrition_prompt(),
            json.dumps(payload, ensure_ascii=False),
            schema_name="yield_nutrition",
            json_schema=NUTRITION_JSON_SCHEMA,
            schema_model=YieldNutritionEstimate,
        )
        try:
            estimate = parse_yield_nutrition(response_text)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise RuntimeError(
                f"Nutrition/servings/time estimation failed: {exc}\nRaw:\n{response_text}"
            ) from exc

        recipe = apply_yield_nutrition_guardrails(
            recipe,
            estimate,
            need_yield=need_yield,
            need_nutrition=False,
            need_prep_time=need_prep_time,
            need_cook_time=need_cook_time,
            need_total_time=need_total_time,
        )
        if recipe.get("prepTime"):
            logger.info(f"Estimated prepTime: {recipe['prepTime']}")
        if recipe.get("cookTime"):
            logger.info(f"Estimated cookTime: {recipe['cookTime']}")
        if recipe.get("totalTime"):
            logger.info(f"Estimated totalTime: {recipe['totalTime']}")

        looked = lookup_recipe_nutrition(recipe)
        if looked:
            recipe["nutrition"] = looked
            logger.info("[Chef] Nutrition from local/USDA table after yield: %s", looked)
        else:
            recipe.pop("nutrition", None)
        return recipe
