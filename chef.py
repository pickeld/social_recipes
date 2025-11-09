import json
import re
from datetime import datetime, timezone
from openai import OpenAI
from config import config
from helpers import RECIPE_SYSTEM_PROMPT, YIELD_NUTRITION_PROMPT


def _normalize_space(s: str) -> str:
    return " ".join(str(s or "").split()).strip()


class Chef:
    def __init__(self, source_url: str, description: str, transcription: str, *, model: str | None = None):
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = model or config.OPENAI_MODEL
        self.source_url = source_url
        self.description = description
        self.transcription = transcription

    def _clean_mealie_and_schema_lists(self, data: dict) -> None:
        # Clean recipeIngredients (objects)
        cleaned, seen = [], set()
        for it in data.get("recipeIngredients") or []:
            food = _normalize_space(it.get("food", ""))
            quantity = _normalize_space(it.get("quantity", ""))
            unit = _normalize_space(it.get("unit", ""))
            note = _normalize_space(it.get("note", ""))

            key = (food.casefold(), quantity, unit, note.casefold())
            if not food:
                continue
            if key in seen:
                continue
            seen.add(key)

            cleaned.append({
                "food": food,
                "quantity": quantity,
                "unit": unit,
                "note": note,
            })

        data["recipeIngredients"] = cleaned

        # Derive Schema.org recipeIngredient (strings) from the objects
        lines = []
        for it in cleaned:
            parts = [it["quantity"], it["unit"], it["food"], it["note"]]
            line = _normalize_space(" ".join(p for p in parts if p))
            if line:
                lines.append(line)
        data["recipeIngredient"] = lines

    # ----------------- Recipe generation -----------------

    def _postprocess_recipe(self, data: dict, source_url: str | None) -> dict:
        """
        Normalizes and enriches raw model output into a valid Schema.org Recipe.
        Additionally parses quantities/units for structured ingredients when missing.
        """
        # Context & type
        data.setdefault("@context", "https://schema.org")
        data.setdefault("@type", "Recipe")
        data.setdefault("url", self.source_url)
        data.setdefault(
            "video", {"@type": "VideoObject", "url": self.source_url})

        dp = data.get("datePublished")
        if not isinstance(dp, str) or len(dp) <= 10:
            data["datePublished"] = datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._clean_mealie_and_schema_lists(data)
        return data

    def create_recipe(self, *, source_url: str | None = None) -> dict:
        payload = {
            "source_url": source_url,
            "description": self.description,
            "transcript": self.transcription,
        }

        resp = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": RECIPE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    payload, ensure_ascii=False)},
            ],
        )

        data = json.loads(resp.output_text)
        recipe = self._postprocess_recipe(data, source_url)
        recipe = self._enrich_yield_and_nutrition(recipe)
        return recipe

    def _enrich_yield_and_nutrition(self, recipe: dict) -> dict:
        need_yield = "recipeYield" not in recipe
        need_nutrition = "nutrition" not in recipe

        if not (need_yield or need_nutrition):
            return recipe  # אין מה להשלים

        # נכין קלט למודל: רשימת מצרכים + הוראות
        payload = {
            "language_hint": config.RECIPE_LANG,
            "ingredients": recipe.get("recipeIngredient", []),
            "instructions": [
                (step.get("text") if isinstance(step, dict) else str(step))
                for step in (recipe.get("recipeInstructions") or [])
            ]
        }

        resp = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": YIELD_NUTRITION_PROMPT},
                {"role": "user", "content": json.dumps(
                    payload, ensure_ascii=False)},
            ],
        )
        try:
            est = json.loads(resp.output_text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Nutrition/servings estimation failed: {e}\nRaw:\n{resp.output_text}")

        # החלה מבוקרת:
        if need_yield:
            # נעדיף מחרוזת בשפת המתכון (אם Hebrew – "X מנות")
            ry = est.get("recipeYield")
            servings = est.get("servings")
            if not ry and isinstance(servings, int) and servings > 0:
                ry = f"{servings} servings"
            if ry:
                recipe["recipeYield"] = str(ry)

        if need_nutrition and isinstance(est.get("nutrition"), dict):
            # להשלים רק שדות חוקיים של Schema.org
            allowed = {
                "@type", "calories", "proteinContent", "fatContent", "carbohydrateContent",
                "fiberContent", "sugarContent", "sodiumContent", "cholesterolContent"
            }
            nutrition = {"@type": "NutritionInformation"}
            for k, v in est["nutrition"].items():
                if k in allowed and v:
                    nutrition[k] = str(v)
            # אם יש לפחות calories או אחד נוסף – נוסיף לשדה
            if any(k in nutrition for k in ("calories", "proteinContent", "fatContent",
                                            "carbohydrateContent", "fiberContent",
                                            "sugarContent", "sodiumContent", "cholesterolContent")):
                recipe["nutrition"] = nutrition

        return recipe
