"""Deterministic per-serving nutrition from ingredients.

Values are rounded USDA FoodData Central / SR Legacy figures per 100 g (public
data). Optional USDA FDC search is used only when USDA_FDC_API_KEY is set in
the environment — the key is never hardcoded.
"""

from __future__ import annotations

import os
from typing import Any

from helpers import extract_servings, setup_logger
from recipe_schema import sanitize_nutrition_fields
from units import quantity_to_grams

logger = setup_logger(__name__)

# (kcal, protein_g, fat_g, carb_g, fiber_g, sugar_g, sodium_mg, cholesterol_mg)
_N = tuple[float, float, float, float, float, float, float, float]

_FOODS: dict[str, dict[str, Any]] = {
    "flour": {"n": (364, 10.3, 1.0, 76.3, 2.7, 0.3, 2, 0), "density": 0.53, "aliases": ["all-purpose flour", "white flour", "קמח", "קמח לבן"]},
    "sugar": {"n": (387, 0, 0, 100, 0, 99.8, 1, 0), "density": 0.85, "aliases": ["white sugar", "granulated sugar", "סוכר"]},
    "brown sugar": {"n": (380, 0.1, 0, 98, 0, 97, 28, 0), "aliases": ["סוכר חום"]},
    "butter": {"n": (717, 0.9, 81, 0.1, 0, 0.1, 11, 215), "density": 0.91, "aliases": ["חמאה"]},
    "olive oil": {"n": (884, 0, 100, 0, 0, 0, 2, 0), "density": 0.91, "aliases": ["extra virgin olive oil", "שמן זית"]},
    "vegetable oil": {"n": (884, 0, 100, 0, 0, 0, 0, 0), "density": 0.92, "aliases": ["canola oil", "שמן"]},
    "egg": {"n": (143, 12.6, 9.5, 0.7, 0, 0.4, 142, 372), "piece_g": 50, "aliases": ["eggs", "ביצה", "ביצים"]},
    "milk": {"n": (61, 3.2, 3.3, 4.8, 0, 5.1, 43, 10), "density": 1.03, "aliases": ["whole milk", "חלב"]},
    "yogurt": {"n": (61, 3.5, 3.3, 4.7, 0, 4.7, 46, 13), "aliases": ["יוגורט"]},
    "cream": {"n": (340, 2.1, 36, 2.8, 0, 2.8, 38, 110), "density": 0.99, "aliases": ["heavy cream", "שמנת"]},
    "cheese": {"n": (402, 25, 33, 1.3, 0, 0.5, 621, 105), "aliases": ["cheddar", "גבינה"]},
    "parmesan": {"n": (431, 38, 29, 4.1, 0, 0.9, 1529, 88), "aliases": ["פרמזן"]},
    "chicken breast": {"n": (165, 31, 3.6, 0, 0, 0, 74, 85), "aliases": ["chicken", "חזה עוף", "עוף"]},
    "chicken thigh": {"n": (177, 24, 8.2, 0, 0, 0, 84, 93), "aliases": ["ירך עוף"]},
    "beef": {"n": (250, 26, 15, 0, 0, 0, 72, 90), "aliases": ["ground beef", "בקר", "בשר"]},
    "pork": {"n": (242, 27, 14, 0, 0, 0, 62, 80), "aliases": ["חזיר"]},
    "salmon": {"n": (208, 20, 13, 0, 0, 0, 59, 55), "aliases": ["סלמון"]},
    "tuna": {"n": (132, 28, 1.3, 0, 0, 0, 47, 47), "aliases": ["טונה"]},
    "shrimp": {"n": (99, 24, 0.3, 0.2, 0, 0, 111, 189), "aliases": ["prawn", "שרימפס"]},
    "tofu": {"n": (76, 8, 4.8, 1.9, 0.3, 0.6, 7, 0), "aliases": ["טופו"]},
    "rice": {"n": (130, 2.7, 0.3, 28, 0.4, 0.1, 1, 0), "aliases": ["white rice", "אורז"]},
    "pasta": {"n": (131, 5, 1.1, 25, 1.8, 0.6, 1, 0), "aliases": ["spaghetti", "noodle", "noodles", "פסטה", "ספגטי"]},
    "bread": {"n": (265, 9, 3.2, 49, 2.7, 5, 491, 0), "piece_g": 30, "aliases": ["לחם"]},
    "potato": {"n": (77, 2, 0.1, 17, 2.2, 0.8, 6, 0), "piece_g": 170, "aliases": ["potatoes", "תפוח אדמה", "תפוחי אדמה"]},
    "onion": {"n": (40, 1.1, 0.1, 9.3, 1.7, 4.2, 4, 0), "piece_g": 110, "aliases": ["onions", "בצל"]},
    "garlic": {"n": (149, 6.4, 0.5, 33, 2.1, 1, 17, 0), "piece_g": 5, "aliases": ["garlic clove", "שום"]},
    "tomato": {"n": (18, 0.9, 0.2, 3.9, 1.2, 2.6, 5, 0), "piece_g": 120, "aliases": ["tomatoes", "עגבניה", "עגבניות"]},
    "tomato paste": {"n": (82, 4.3, 0.5, 19, 4.1, 12, 59, 0), "aliases": ["רסק עגבניות"]},
    "carrot": {"n": (41, 0.9, 0.2, 10, 2.8, 4.7, 69, 0), "piece_g": 60, "aliases": ["carrots", "גזר"]},
    "celery": {"n": (16, 0.7, 0.2, 3, 1.6, 1.3, 80, 0), "aliases": ["סלרי"]},
    "bell pepper": {"n": (31, 1, 0.3, 6, 2.1, 4.2, 4, 0), "aliases": ["pepper", "red pepper", "פלפל"]},
    "chili": {"n": (40, 1.9, 0.4, 8.8, 1.5, 5.3, 9, 0), "aliases": ["chili pepper", "צ'ילי", "צ׳ילי"]},
    "cucumber": {"n": (15, 0.7, 0.1, 3.6, 0.5, 1.7, 2, 0), "aliases": ["מלפפון"]},
    "lettuce": {"n": (15, 1.4, 0.2, 2.9, 1.3, 0.8, 28, 0), "aliases": ["חסה"]},
    "spinach": {"n": (23, 2.9, 0.4, 3.6, 2.2, 0.4, 79, 0), "aliases": ["תרד"]},
    "broccoli": {"n": (34, 2.8, 0.4, 7, 2.6, 1.7, 33, 0), "aliases": ["ברוקולי"]},
    "mushroom": {"n": (22, 3.1, 0.3, 3.3, 1, 2, 5, 0), "aliases": ["mushrooms", "פטריות"]},
    "avocado": {"n": (160, 2, 15, 8.5, 6.7, 0.7, 7, 0), "piece_g": 150, "aliases": ["אבוקדו"]},
    "lemon": {"n": (29, 1.1, 0.3, 9, 2.8, 2.5, 2, 0), "piece_g": 60, "aliases": ["lemon juice", "לימון"]},
    "lime": {"n": (30, 0.7, 0.2, 11, 2.8, 1.7, 2, 0), "aliases": ["ליים"]},
    "apple": {"n": (52, 0.3, 0.2, 14, 2.4, 10, 1, 0), "piece_g": 180, "aliases": ["תפוח"]},
    "banana": {"n": (89, 1.1, 0.3, 23, 2.6, 12, 1, 0), "piece_g": 120, "aliases": ["בננה"]},
    "strawberry": {"n": (32, 0.7, 0.3, 7.7, 2, 4.9, 1, 0), "aliases": ["strawberries", "תות"]},
    "salt": {"n": (0, 0, 0, 0, 0, 0, 38758, 0), "aliases": ["kosher salt", "sea salt", "מלח"]},
    "black pepper": {"n": (251, 10, 3.3, 64, 25, 0.6, 20, 0), "aliases": ["pepper", "פלפל שחור"]},
    "soy sauce": {"n": (53, 8.1, 0.1, 4.9, 0.8, 0.4, 5493, 0), "density": 1.2, "aliases": ["סויו", "רוטב סויה"]},
    "vinegar": {"n": (18, 0, 0, 0.04, 0, 0.04, 2, 0), "density": 1.01, "aliases": ["חומץ"]},
    "honey": {"n": (304, 0.3, 0, 82, 0.2, 82, 4, 0), "density": 1.42, "aliases": ["דבש"]},
    "maple syrup": {"n": (260, 0, 0.1, 67, 0, 60, 12, 0), "density": 1.32, "aliases": ["סירופ מייפל"]},
    "cocoa": {"n": (228, 20, 14, 58, 37, 1.8, 21, 0), "aliases": ["cocoa powder", "קקאו"]},
    "chocolate": {"n": (546, 4.9, 31, 61, 7, 48, 24, 8), "aliases": ["שוקולד"]},
    "almond": {"n": (579, 21, 50, 22, 12.5, 4.4, 1, 0), "aliases": ["almonds", "שקד", "שקדים"]},
    "walnut": {"n": (654, 15, 65, 14, 6.7, 2.6, 2, 0), "aliases": ["walnuts", "אגוז מלך"]},
    "peanut": {"n": (567, 26, 49, 16, 8.5, 4.7, 18, 0), "aliases": ["peanuts", "בוטן", "בוטנים"]},
    "sesame": {"n": (573, 18, 50, 23, 12, 0.3, 11, 0), "aliases": ["sesame seeds", "שומשום"]},
    "tahini": {"n": (595, 17, 54, 21, 9.3, 0.5, 35, 0), "density": 1.1, "aliases": ["טחינה"]},
    "chickpea": {"n": (164, 8.9, 2.6, 27, 7.6, 4.8, 7, 0), "aliases": ["chickpeas", "garbanzo", "גרגרים", "חומוס"]},
    "lentil": {"n": (116, 9, 0.4, 20, 7.9, 1.8, 2, 0), "aliases": ["lentils", "עדשים"]},
    "bean": {"n": (127, 8.7, 0.5, 23, 6.4, 0.3, 2, 0), "aliases": ["beans", "black beans", "שעועית"]},
    "corn": {"n": (86, 3.3, 1.4, 19, 2, 3.2, 15, 0), "aliases": ["תירס"]},
    "oat": {"n": (389, 17, 6.9, 66, 10.6, 0.9, 2, 0), "aliases": ["oats", "oatmeal", "שיבולת שועל"]},
    "coconut milk": {"n": (197, 2, 21, 3, 2.2, 3.3, 13, 0), "density": 1.0, "aliases": ["חלב קוקוס"]},
    "ginger": {"n": (80, 1.8, 0.8, 18, 2, 1.7, 13, 0), "aliases": ["ג'ינג'ר", "ג׳ינג׳ר", "זנגביל"]},
    "basil": {"n": (23, 3.2, 0.6, 2.7, 1.6, 0.3, 4, 0), "aliases": ["בזיליקום"]},
    "parsley": {"n": (36, 3, 0.8, 6.3, 3.3, 0.9, 56, 0), "aliases": ["פטרוזיליה"]},
    "cilantro": {"n": (23, 2.1, 0.5, 3.7, 2.8, 0.9, 46, 0), "aliases": ["coriander", "כוסברה"]},
    "water": {"n": (0, 0, 0, 0, 0, 0, 0, 0), "density": 1.0, "aliases": ["מים"]},
}

_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _meta in _FOODS.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _alias in _meta.get("aliases") or []:
        _ALIAS_TO_CANON[_alias.casefold()] = _canon

_USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"


def _fold_food(name: str) -> str:
    return " ".join((name or "").casefold().split())


def match_food(name: str) -> str | None:
    """Return the canonical food key for an ingredient name, if any."""
    folded = _fold_food(name)
    if not folded:
        return None
    if folded in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[folded]
    for alias, canon in _ALIAS_TO_CANON.items():
        if alias and alias in folded:
            return canon
    return None


def _scale(n: _N, grams: float) -> _N:
    factor = grams / 100.0
    return tuple(v * factor for v in n)  # type: ignore[return-value]


def _add(a: _N, b: _N) -> _N:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def _zero() -> _N:
    return (0, 0, 0, 0, 0, 0, 0, 0)


def _usda_api_key() -> str:
    return (os.environ.get("USDA_FDC_API_KEY") or "").strip()


def _search_usda(food: str) -> _N | None:
    """Best-effort FDC lookup. Swallows errors; never logs the API key."""
    key = _usda_api_key()
    if not key:
        return None
    try:
        from helpers import create_http_session
        from url_safety import safe_get

        session = create_http_session()
        resp = safe_get(
            session,
            _USDA_SEARCH,
            params={"query": food, "pageSize": 1, "api_key": key},
            timeout=8,
        )
        resp.raise_for_status()
        foods = (resp.json() or {}).get("foods") or []
        if not foods:
            return None
        by_id = {
            int(n.get("nutrientId") or 0): float(n.get("value") or 0)
            for n in foods[0].get("foodNutrients") or []
            if n.get("nutrientId")
        }
        return (
            by_id.get(1008, 0),
            by_id.get(1003, 0),
            by_id.get(1004, 0),
            by_id.get(1005, 0),
            by_id.get(1079, 0),
            by_id.get(2000, 0),
            by_id.get(1093, 0),
            by_id.get(1253, 0),
        )
    except Exception as exc:
        logger.info("[Nutrition] USDA lookup skipped for %r: %s", food, type(exc).__name__)
        return None


def _nutrients_for(name: str) -> tuple[str, dict[str, Any]] | None:
    canon = match_food(name)
    if canon:
        return canon, _FOODS[canon]
    remote = _search_usda(name)
    if remote is None:
        return None
    return name, {"n": remote, "density": 1.0}


def ingredient_grams(item: dict) -> float | None:
    food = str(item.get("food") or "")
    meta = _FOODS.get(match_food(food) or "")
    density = float((meta or {}).get("density") or 1.0)
    piece_g = (meta or {}).get("piece_g")
    return quantity_to_grams(
        str(item.get("quantity") or ""),
        str(item.get("unit") or ""),
        density_g_per_ml=density,
        piece_grams=float(piece_g) if piece_g else None,
    )


def lookup_recipe_nutrition(recipe: dict) -> dict | None:
    """Return Schema.org NutritionInformation per serving, or None if too little matched."""
    ingredients = recipe.get("recipeIngredients") or []
    if not ingredients:
        return None
    total = _zero()
    matched = 0
    weighed = 0
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        food = str(item.get("food") or "").strip()
        if not food:
            continue
        resolved = _nutrients_for(food)
        grams = ingredient_grams(item)
        if grams is None or grams <= 0 or resolved is None:
            continue
        _canon, meta = resolved
        total = _add(total, _scale(meta["n"], grams))
        matched += 1
        weighed += grams

    usable = sum(1 for i in ingredients if isinstance(i, dict) and str(i.get("food") or "").strip())
    if matched == 0 or weighed <= 0:
        return None
    if usable and matched / usable < 0.5:
        logger.info(
            "[Nutrition] local/USDA match rate %.0f%% — leaving nutrition to the LLM",
            100 * matched / usable,
        )
        return None

    servings = max(extract_servings(recipe), 1)
    kcal, protein, fat, carb, fiber, sugar, sodium, chol = (v / servings for v in total)
    raw = {
        "calories": f"{round(kcal)} kcal",
        "proteinContent": f"{round(protein, 1)} g",
        "fatContent": f"{round(fat, 1)} g",
        "carbohydrateContent": f"{round(carb, 1)} g",
        "fiberContent": f"{round(fiber, 1)} g",
        "sugarContent": f"{round(sugar, 1)} g",
        "sodiumContent": f"{round(sodium)} mg",
        "cholesterolContent": f"{round(chol)} mg",
    }
    cleaned = sanitize_nutrition_fields(raw)
    if not any(k != "@type" for k in cleaned):
        return None
    logger.info("[Nutrition] estimated from %d/%d ingredients, %d servings", matched, usable, servings)
    return cleaned
