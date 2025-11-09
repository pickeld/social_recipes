import json
import os
from datetime import datetime
from openai import OpenAI
from config import config


class Schemas:
    Mealie = "mealie"
    Tandoor = "tandoor"


RECIPE_SYSTEM_PROMPT = """You are a culinary data normalizer.
Return a single valid JSON object in Schema.org JSON-LD for a Recipe.
MUST be strictly valid JSON (no comments, no trailing commas). Keep Hebrew intact.

Required fields:
- "@context": "https://schema.org"
- "@type": "Recipe"
- "name"
- "description"
- "datePublished" (ISO 8601)
- "recipeIngredient" (array of strings; one per item)
- "recipeInstructions" (array of HowToStep objects: { "@type": "HowToStep", "text": "<step>" })
- "recipeYield" (string)
Optional (only if confidently inferable): "prepTime","cookTime","totalTime" (ISO8601 durations), "keywords" (array), "recipeCuisine","recipeCategory","image","video","author","url","nutrition".

Rules:
- Do NOT invent quantities that aren't supported by the text.
- If quantity is unclear, put the ingredient name cleanly without fake numbers.
- Merge duplicate ingredients; keep instructions clear, stepwise, chronological.
"""


class Chef:
    def __init__(self, description: str, transcription: str, *, model: str = "gpt-5.1-mini"):
        # assumes you have config.OPENAI_API_KEY
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = model
        self.description = description or ""
        self.transcription = transcription or ""
        # Optional: debug prints
        # print("Transcription:", self.transcription[:300])
        # print("Description:", self.description[:300])

    def _postprocess_recipe(self, data: dict, source_url: str | None) -> dict:
        # Guard rails & defaults
        data.setdefault("@context", "https://schema.org")
        data.setdefault("@type", "Recipe")
        if "datePublished" not in data:
            data["datePublished"] = datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        if source_url and "url" not in data:
            data["url"] = source_url

        # Ensure ingredients is a list of unique strings
        ingredients = data.get("recipeIngredient", [])
        if isinstance(ingredients, str):
            ingredients = [i.strip()
                           for i in ingredients.split("\n") if i.strip()]
        # de-dup while preserving order
        seen = set()
        deduped = []
        for ing in ingredients:
            if ing not in seen:
                seen.add(ing)
                deduped.append(ing)
        data["recipeIngredient"] = deduped

        # Ensure instructions is an array of HowToStep
        instr = data.get("recipeInstructions", [])
        if isinstance(instr, str):
            steps = [s.strip() for s in instr.split("\n") if s.strip()]
            instr = [{"@type": "HowToStep", "text": s} for s in steps]
        else:
            # If list of strings → wrap; if list of dicts, leave as-is
            wrapped = []
            for s in instr:
                if isinstance(s, str):
                    wrapped.append({"@type": "HowToStep", "text": s.strip()})
                elif isinstance(s, dict):
                    # normalize key presence
                    s.setdefault("@type", "HowToStep")
                    wrapped.append(s)
            instr = wrapped
        data["recipeInstructions"] = instr

        return data

    def create_recipe(self, *, source_url: str | None = None) -> dict:
        """Send description + transcription to OpenAI and get a JSON-LD Recipe back."""
        payload = {
            "source_url": source_url,
            "description": self.description,
            "transcript": self.transcription
        }

        resp = self.client.responses.create(
            model=self.model,
            # response_format={"type": "json_object"},  # strong JSON guarantee
            input=[
                {"role": "system", "content": RECIPE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    payload, ensure_ascii=False)}
            ],
        )

        raw_text = resp.output_text  # already JSON due to response_format
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            # Fallback: try to salvage JSON if the model added wrappers unexpectedly
            raise RuntimeError(
                f"Model did not return valid JSON. Error: {e}\nRaw:\n{raw_text}")

        # return self._postprocess_recipe(data, source_url)
        return data

    @staticmethod
    def save_json(recipe: dict, path: str = "recipe.json") -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        return path
