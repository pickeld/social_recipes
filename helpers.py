from config import config

# Map language codes to full names
_LANG_NAMES = {
    "he": "Hebrew",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ar": "Arabic",
    "ru": "Russian",
}


def _get_target_lang() -> str:
    return _LANG_NAMES.get(config.TARGET_LANGUAGE, config.TARGET_LANGUAGE)


def get_recipe_system_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary data normalizer.
Return a single valid JSON object in Schema.org JSON-LD for a Recipe.
MUST be strictly valid JSON (no comments, no trailing commas).
ALL text content MUST be in {target_lang}. Translate any content that is not already in {target_lang}.

Required fields:
- "@context": "https://schema.org"
- "@type": "Recipe"
- "name"
- "description" (1–2 short sentences)
- "datePublished" (ISO 8601)
- "recipeYield" (string)
- "recipeInstructions" (array of HowToStep objects: {{ "@type": "HowToStep", "text": "<step>" }})

Ingredients (MUST provide BOTH):
1) "recipeIngredients" (array of objects for Mealie). Each item MUST be:
   {{
     "food": "<base ingredient noun in {target_lang}>",
     "quantity": "<number or range as string, or empty string if unknown>",
     "unit": "<unit in {target_lang}, or empty string if none>",
     "note": "<prep/brand/extra notes in {target_lang}, or empty string>"
   }}
   Rules:
   - Do NOT invent quantities. If missing/unclear → "quantity": "" and "unit": "".
   - Preserve numeric ranges literally, e.g., "3-4".
   - Put prep words (e.g., קצוץ / chopped) and clarifiers into "note".
   - Merge true duplicates (identical food+quantity+unit+note).

2) "recipeIngredient" (array of strings for Schema.org), derived from recipeIngredients:
   - Compose each line as: "<quantity> <unit> <food> <note>" (skip empties; normalize spaces).
   - Preserve order.

General rules:
- Keep instructions chronological; one step per HowToStep.
- Only output the JSON object (no explanations).
- ALL TEXT MUST BE IN {target_lang}.
"""


def get_yield_nutrition_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a registered-dietitian-style assistant.
Given a recipe's ingredients and instructions, estimate:
- servings (number of portions; if unclear, infer a reasonable integer)
- per-serving nutrition (Schema.org NutritionInformation fields):
  calories (kcal), proteinContent (g), fatContent (g), carbohydrateContent (g),
  fiberContent (g), sugarContent (g), sodiumContent (mg), cholesterolContent (mg).
Assumptions must be realistic; if an item is truly unclear, leave it out.
Return a single valid JSON object with:
{{
  "servings": <int>,
  "recipeYield": "<string in {target_lang}, e.g., '4 מנות' for Hebrew or '4 servings' for English>",
  "nutrition": {{
    "@type": "NutritionInformation",
    "calories": "450 kcal",
    "proteinContent": "20 g",
    "fatContent": "18 g",
    "carbohydrateContent": "55 g",
    "fiberContent": "4 g",
    "sugarContent": "3 g",
    "sodiumContent": "680 mg",
    "cholesterolContent": "70 mg"
  }}
}}
All nutrition values are per serving.
Do not invent impossible numbers; keep them plausible.
Output recipeYield in {target_lang}.
"""
