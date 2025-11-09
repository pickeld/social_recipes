import json
import re
from datetime import datetime, timezone
from openai import OpenAI
from config import config
from helpers import RECIPE_SYSTEM_PROMPT, YIELD_NUTRITION_PROMPT


class Chef:
    def __init__(self, description: str, transcription: str, *, model: str | None = None):
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = model or config.OPENAI_MODEL
        self.description = description
        self.transcription = transcription

    # ----------------- Recipe generation -----------------
    def _postprocess_recipe(self, data: dict, source_url: str | None) -> dict:
        # Context & type
        data.setdefault("@context", "https://schema.org")
        data.setdefault("@type", "Recipe")

        # datePublished → ISO 8601 מלא
        dp = data.get("datePublished")
        if not isinstance(dp, str) or len(dp) <= 10:
            data["datePublished"] = datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # video/url אם יש מקור
        if source_url:
            data.setdefault("url", source_url)
            data.setdefault(
                "video", {"@type": "VideoObject", "url": source_url})

        # מצרכים: רשימה ייחודית
        ingredients = data.get("recipeIngredient", [])
        if isinstance(ingredients, str):
            ingredients = [i.strip()
                           for i in ingredients.split("\n") if i.strip()]
        seen = set()
        dedup = []
        for ing in ingredients or []:
            if ing and ing not in seen:
                seen.add(ing)
                dedup.append(ing)
        data["recipeIngredient"] = dedup

        # הוראות: עטיפה ל-HowToStep
        instr = data.get("recipeInstructions", [])
        if isinstance(instr, str):
            steps = [s.strip() for s in instr.split("\n") if s.strip()]
            instr = [{"@type": "HowToStep", "text": s} for s in steps]
        else:
            wrapped = []
            for s in instr or []:
                if isinstance(s, str):
                    txt = s.strip()
                    if txt:
                        wrapped.append({"@type": "HowToStep", "text": txt})
                elif isinstance(s, dict):
                    s.setdefault("@type", "HowToStep")
                    if s.get("text"):
                        s["text"] = s["text"].strip()
                        wrapped.append(s)
            instr = wrapped
        data["recipeInstructions"] = instr

        # recipeYield: הסר אם לא ידוע
        if str(data.get("recipeYield", "")).strip() in ("", "לא צויין", "לא צוין"):
            data.pop("recipeYield", None)

        return data

    def create_recipe(self, *, source_url: str | None = None) -> dict:
        """
        שולח description+transcription ל-OpenAI ומחזיר JSON-LD תקין (Schema.org/Recipe).
        """
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
        """
        אם חסרים recipeYield או nutrition – נאמד אותם בעזרת AI.
        nutrition יהיה פר-מנה, כמצופה ב-Schema.org.
        """
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
