"""Canonical cooking units and quantity → grams conversion.

Chef often emits tablespoon/cup/גרם spellings. Nutrition lookup and display
both need a single abbreviation and a gram estimate.
"""

from __future__ import annotations

import re

from helpers import coerce_num

# Volume units → millilitres. 1 ml of water ≈ 1 g; densities refine that.
_VOLUME_ML: dict[str, float] = {
    "ml": 1,
    "milliliter": 1,
    "millilitre": 1,
    "l": 1000,
    "liter": 1000,
    "litre": 1000,
    "tsp": 5,
    "teaspoon": 5,
    "teaspoons": 5,
    "tbsp": 15,
    "tablespoon": 15,
    "tablespoons": 15,
    "cup": 240,
    "cups": 240,
    "fl oz": 30,
    "floz": 30,
    "fluid ounce": 30,
    "fluid ounces": 30,
    "pint": 473,
    "quart": 946,
    "gallon": 3785,
    "כפית": 5,
    "כפיות": 5,
    "כף": 15,
    "כפות": 15,
    "כוס": 240,
    "כוסות": 240,
    "מ״ל": 1,
    'מ"ל': 1,
    "מל": 1,
    "ליטר": 1000,
}

_MASS_G: dict[str, float] = {
    "g": 1,
    "gram": 1,
    "grams": 1,
    "kg": 1000,
    "kilogram": 1000,
    "kilograms": 1000,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.6,
    "lbs": 453.6,
    "pound": 453.6,
    "pounds": 453.6,
    "גרם": 1,
    "גרמים": 1,
    "ק״ג": 1000,
    'ק"ג': 1000,
    "קג": 1000,
}

_COUNT_UNITS = frozenset({
    "piece", "pieces", "pc", "pcs", "unit", "units", "whole", "clove", "cloves",
    "slice", "slices", "leaf", "leaves", "sprig", "sprigs", "bunch", "bunches",
    "יחידה", "יחידות", "שן", "שיניים", "פרוסה", "פרוסות", "עלה", "עלים",
})

_PINCH_G = 0.3
_PINCH_UNITS = frozenset({"pinch", "pinches", "dash", "dashes", "קמצוץ", "קורט"})

_CANONICAL: dict[str, str] = {}
for _alias in _VOLUME_ML:
    if _alias in {"tsp", "teaspoon", "teaspoons", "כפית", "כפיות"}:
        _CANONICAL[_alias] = "tsp"
    elif _alias in {"tbsp", "tablespoon", "tablespoons", "כף", "כפות"}:
        _CANONICAL[_alias] = "tbsp"
    elif _alias in {"cup", "cups", "כוס", "כוסות"}:
        _CANONICAL[_alias] = "cup"
    elif _alias in {"ml", "milliliter", "millilitre", "מ״ל", 'מ"ל', "מל"}:
        _CANONICAL[_alias] = "ml"
    elif _alias in {"l", "liter", "litre", "ליטר"}:
        _CANONICAL[_alias] = "l"
    elif _alias in {"fl oz", "floz", "fluid ounce", "fluid ounces"}:
        _CANONICAL[_alias] = "fl oz"
for _alias in _MASS_G:
    if _alias in {"kg", "kilogram", "kilograms", "ק״ג", 'ק"ג', "קג"}:
        _CANONICAL[_alias] = "kg"
    elif _alias in {"oz", "ounce", "ounces"}:
        _CANONICAL[_alias] = "oz"
    elif _alias in {"lb", "lbs", "pound", "pounds"}:
        _CANONICAL[_alias] = "lb"
    else:
        _CANONICAL[_alias] = "g"

_WS = re.compile(r"\s+")


def _fold(unit: str) -> str:
    return _WS.sub(" ", (unit or "").strip().casefold())


def canonicalize_unit(unit: str) -> str:
    """Map a free-text unit onto a short canonical abbreviation, if known."""
    folded = _fold(unit)
    if not folded:
        return ""
    if folded in _CANONICAL:
        return _CANONICAL[folded]
    if folded in _COUNT_UNITS:
        return "piece"
    if folded in _PINCH_UNITS:
        return "pinch"
    return unit.strip()


def quantity_to_grams(
    quantity: str,
    unit: str,
    *,
    density_g_per_ml: float = 1.0,
    piece_grams: float | None = None,
) -> float | None:
    """Convert a quantity + unit to grams. None when the unit is unknown."""
    amount = coerce_num(quantity)
    if amount <= 0:
        return None
    folded = _fold(unit)
    if not folded:
        if piece_grams:
            return amount * piece_grams
        return None
    if folded in _MASS_G:
        return amount * _MASS_G[folded]
    if folded in _VOLUME_ML:
        return amount * _VOLUME_ML[folded] * density_g_per_ml
    if folded in _PINCH_UNITS:
        return amount * _PINCH_G
    if folded in _COUNT_UNITS and piece_grams:
        return amount * piece_grams
    return None


def normalize_ingredient_units(ingredients: list[dict]) -> None:
    """Rewrite ``unit`` on each structured ingredient in place."""
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        unit = item.get("unit") or ""
        canonical = canonicalize_unit(str(unit))
        if canonical:
            item["unit"] = canonical
