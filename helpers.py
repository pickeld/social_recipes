from config import config
import logging
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ==============================================================================
# Logging Configuration
# ==============================================================================

def setup_logger(name: str) -> logging.Logger:
    """Create and configure a logger with time, function name, and severity.
    
    Args:
        name: Name of the logger (typically __name__).
        
    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    
    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Create console handler
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        
        # Create formatter with time, name, function, severity
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(funcName)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


# ==============================================================================
# HTTP Utilities
# ==============================================================================

def create_http_session() -> requests.Session:
    """Create a requests session with retry logic and proper timeouts."""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ==============================================================================
# Parsing Utilities
# ==============================================================================

def coerce_num(val: str) -> float:
    """Convert string quantity to float, handling ranges and locales.
    
    Examples:
        >>> coerce_num("2.5")
        2.5
        >>> coerce_num("3-4")
        3.0
        >>> coerce_num("1,5")
        1.5
    """
    if not val:
        return 0
    v = str(val).strip()
    # Handle range -> take first number
    if "-" in v:
        v = v.split("-")[0].strip()
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0


def parse_nutrition_value(value: str | None) -> float:
    """Extract numeric value from nutrition string like '450 kcal' or '20 g'.
    
    Examples:
        >>> parse_nutrition_value("450 kcal")
        450.0
        >>> parse_nutrition_value("20 g")
        20.0
        >>> parse_nutrition_value(None)
        0
    """
    if not value:
        return 0
    # Extract the first number from the string
    match = re.search(r"(\d+(?:[.,]\d+)?)", str(value))
    if match:
        return float(match.group(1).replace(",", "."))
    return 0


def extract_servings(recipe_data: dict) -> int:
    """Extract numeric servings from recipeYield field.
    
    Args:
        recipe_data: Recipe dictionary containing 'recipeYield' field.
        
    Returns:
        Integer number of servings, defaults to 1 if not found.
    """
    ry = recipe_data.get("recipeYield") or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(ry))
    if m:
        try:
            return int(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    return 1


def parse_iso_duration(duration: str) -> int:
    """Parse ISO 8601 duration (e.g., PT30M, PT1H30M) to minutes.
    
    Examples:
        >>> parse_iso_duration("PT30M")
        30
        >>> parse_iso_duration("PT1H30M")
        90
        >>> parse_iso_duration("PT2H")
        120
        
    Returns:
        Minutes as integer, 0 if parsing fails.
    """
    if not duration:
        return 0
    # Match patterns like PT1H30M, PT45M, PT2H
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration).upper()
    )
    if not match:
        # Try simple numeric (assume minutes)
        try:
            return int(duration)
        except (ValueError, TypeError):
            return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


# ==============================================================================
# Language Utilities
# ==============================================================================

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
Return a single valid JSON object.
MUST be strictly valid JSON (no comments, no trailing commas).
ALL text content MUST be in {target_lang}. Translate any content that is not already in {target_lang}.

If the input is not a recipe (no dish, no ingredients, no cooking steps — e.g. a vlog, ad, music video, or unrelated clip), set "is_recipe" to false, put a short reason in "rejection_reason", and leave the recipe fields empty. Do NOT invent a recipe.

If it is a recipe, set "is_recipe" to true and "rejection_reason" to "".

Required fields:
- "is_recipe" (boolean)
- "rejection_reason" (string; empty when is_recipe is true)
- "name"
- "description" (1–2 short sentences)
- "datePublished" (ISO 8601)
- "recipeYield" (string; empty if unknown)
- "recipeInstructions" (array of objects: {{ "text": "<step>" }})

Ingredients:
- "recipeIngredients" (array of objects). Each item MUST be:
  {{
    "food": "<base ingredient noun in {target_lang}>",
    "quantity": "<number or range as string, or empty string if unknown>",
    "unit": "<unit abbreviation or name in {target_lang}, or empty string if none>",
    "notes": "<prep/brand/extra notes in {target_lang}, or empty string>",
    "raw": "<full ingredient line as shown in recipe, for display fallback>"
  }}
  Rules:
  - "food" MUST be the core ingredient name only (e.g., "flour", "chicken breast", "olive oil").
  - "unit" MUST be a measurement unit only (e.g., "g", "kg", "ml", "cup", "tbsp", "tsp", "piece").
  - Do NOT include modifiers or prep instructions in "food" or "unit" - put them in "notes".
  - Do NOT invent quantities. If missing/unclear → "quantity": "" and "unit": "".
  - If on_screen_text and audio_transcript disagree, trust on_screen_text for ingredients and amounts.
  - A quantity that appears in neither source MUST stay empty.
  - Preserve numeric ranges literally, e.g., "3-4".
  - Put prep words (e.g., קצוץ / chopped, melted, room temperature) into "notes".
  - "raw" should be the complete ingredient line for display purposes.
  - Merge true duplicates (identical food+quantity+unit+notes).

General rules:
- Keep instructions chronological; one step per object.
- Only output the JSON object (no explanations).
- ALL TEXT MUST BE IN {target_lang}.
"""


def get_web_recipe_system_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary data normalizer.
You will receive either:
  (a) a Schema.org Recipe JSON object already extracted from the page, or
  (b) raw visible text scraped from a recipe web page.

Return a single valid JSON object.
MUST be strictly valid JSON (no comments, no trailing commas).
ALL text content MUST be in {target_lang}. Translate any content that is not already in {target_lang}.

If the input is not a recipe (no dish, no ingredients, no cooking steps — e.g. a homepage, blog post, or shop page), set "is_recipe" to false, put a short reason in "rejection_reason", and leave the recipe fields empty. Do NOT invent a recipe.

If it is a recipe, set "is_recipe" to true and "rejection_reason" to "".

Required fields:
- "is_recipe" (boolean)
- "rejection_reason" (string; empty when is_recipe is true)
- "name"
- "description" (1–2 short sentences)
- "datePublished" (ISO 8601)
- "recipeYield" (string; empty if unknown)
- "recipeInstructions" (array of objects: {{ "text": "<step>" }})

Ingredients:
- "recipeIngredients" (array of objects). Each item MUST be:
  {{
    "food": "<base ingredient noun in {target_lang}>",
    "quantity": "<number or range as string, or empty string if unknown>",
    "unit": "<unit abbreviation or name in {target_lang}, or empty string if none>",
    "notes": "<prep/brand/extra notes in {target_lang}, or empty string>",
    "raw": "<full ingredient line as shown in recipe, for display fallback>"
  }}
  Rules:
  - "food" MUST be the core ingredient name only (e.g., "flour", "chicken breast", "olive oil").
  - "unit" MUST be a measurement unit only (e.g., "g", "kg", "ml", "cup", "tbsp", "tsp", "piece").
  - Do NOT include modifiers or prep instructions in "food" or "unit" - put them in "notes".
  - Do NOT invent quantities. If missing/unclear → "quantity": "" and "unit": "".
  - Preserve numeric ranges literally, e.g., "3-4".
  - Put prep words (e.g., chopped, melted, room temperature) into "notes".
  - "raw" should be the complete ingredient line for display purposes.
  - Merge true duplicates (identical food+quantity+unit+notes).

General rules:
- Keep instructions chronological; one step per object.
- Only output the JSON object (no explanations).
- ALL TEXT MUST BE IN {target_lang}.
- If the input already contains Schema.org data, preserve accurate numeric quantities — do not invent or alter them.
- Never guess a missing amount to make the recipe look complete.
"""


def get_yield_nutrition_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary timing assistant.
Given a recipe's ingredients and instructions, estimate:
- servings (number of portions; if unclear, infer a reasonable integer based on ingredient amounts)
- prepTime, cookTime, totalTime as ISO-8601 durations
Leave every nutrition field as "". Do not guess macros; another step looks those up from ingredients.
Assumptions must be realistic; if an item is truly unclear, leave it out.
Return a single valid JSON object with:
{{
  "servings": <int>,
  "recipeYield": "<string in {target_lang}, e.g., '4 מנות' for Hebrew or '4 servings' for English>",
  "prepTime": "PT15M",
  "cookTime": "PT30M",
  "totalTime": "PT45M",
  "nutrition": {{
    "@type": "NutritionInformation",
    "calories": "",
    "proteinContent": "",
    "fatContent": "",
    "carbohydrateContent": "",
    "fiberContent": "",
    "sugarContent": "",
    "sodiumContent": "",
    "cholesterolContent": ""
  }}
}}
Time values must be in ISO 8601 duration format (e.g., "PT30M" for 30 minutes, "PT1H" for 1 hour, "PT1H30M" for 1 hour 30 minutes). Use "" if unknown.
Servings 1–50 only. Output recipeYield in {target_lang}.
"""
