"""Recipe LLM output schemas, validation, and post-parse guardrails.

Chef used to ask the model for JSON in prose and then json.loads whatever came
back. These models are the contract: structured-output requests on the provider
side, and fail-closed validation on ours.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

# ISO-8601 durations that actually show up on recipes (PT15M, PT1H30M, P1DT2H).
_ISO8601_DURATION = re.compile(
    r"^P(?!$)(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$",
    re.IGNORECASE,
)

_AMOUNT = re.compile(r"[-+]?\d+(?:\.\d+)?")

NUTRITION_BOUNDS: dict[str, tuple[float, float]] = {
    "calories": (0, 5000),
    "proteinContent": (0, 500),
    "fatContent": (0, 500),
    "carbohydrateContent": (0, 1000),
    "fiberContent": (0, 200),
    "sugarContent": (0, 500),
    "sodiumContent": (0, 10_000),
    "cholesterolContent": (0, 2000),
}

SERVINGS_MIN = 1
SERVINGS_MAX = 50


class NotARecipeError(ValueError):
    """The source was not a recipe; Chef must not invent one."""


class HowToStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    food: str
    quantity: str
    unit: str
    notes: str
    raw: str


class RecipeExtraction(BaseModel):
    """Structured payload from the recipe-normalization LLM call."""

    model_config = ConfigDict(extra="forbid")
    is_recipe: bool
    rejection_reason: str
    name: str
    description: str
    datePublished: str
    recipeYield: str
    recipeInstructions: list[HowToStep]
    recipeIngredients: list[Ingredient]


class NutritionEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calories: str
    proteinContent: str
    fatContent: str
    carbohydrateContent: str
    fiberContent: str
    sugarContent: str
    sodiumContent: str
    cholesterolContent: str


class YieldNutritionEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servings: int
    recipeYield: str
    prepTime: str
    cookTime: str
    totalTime: str
    nutrition: NutritionEstimate


def extract_json(text: str) -> str:
    """Strip markdown fences so a JSON object remains."""
    if not text:
        return text
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def _strictify(node: dict) -> dict:
    """Make a Pydantic JSON schema acceptable to OpenAI strict json_schema."""
    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties") or {}
        node["additionalProperties"] = False
        node["required"] = list(props.keys())
        for value in props.values():
            if isinstance(value, dict):
                _strip_openai_noise(value)
                _strictify(value)
    items = node.get("items")
    if isinstance(items, dict):
        _strip_openai_noise(items)
        _strictify(items)
    for key in ("anyOf", "oneOf", "allOf"):
        for item in node.get(key) or []:
            if isinstance(item, dict):
                _strictify(item)
    for key in ("$defs", "definitions"):
        defs = node.get(key)
        if isinstance(defs, dict):
            for value in defs.values():
                if isinstance(value, dict):
                    _strictify(value)
    _strip_openai_noise(node)
    return node


def _strip_openai_noise(node: dict) -> None:
    node.pop("default", None)
    node.pop("title", None)
    node.pop("description", None)


def to_openai_json_schema(model: type[BaseModel]) -> dict:
    """JSON Schema for OpenAI Responses API `text.format` (strict)."""
    schema = model.model_json_schema()
    schema.pop("$schema", None)
    return _strictify(schema)


RECIPE_JSON_SCHEMA = to_openai_json_schema(RecipeExtraction)
NUTRITION_JSON_SCHEMA = to_openai_json_schema(YieldNutritionEstimate)


def parse_model(text: str, model: type[BaseModel]) -> BaseModel:
    """Parse LLM text as JSON and validate against ``model``."""
    raw = extract_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return model.model_validate(data)


def parse_recipe_extraction(text: str) -> RecipeExtraction:
    extraction = parse_model(text, RecipeExtraction)
    assert isinstance(extraction, RecipeExtraction)
    return extraction


def parse_yield_nutrition(text: str) -> YieldNutritionEstimate:
    estimate = parse_model(text, YieldNutritionEstimate)
    assert isinstance(estimate, YieldNutritionEstimate)
    return estimate


def recipe_dict_from_extraction(extraction: RecipeExtraction) -> dict[str, Any]:
    """Drop guardrail fields; Chef postprocess adds Schema.org chrome."""
    data = extraction.model_dump()
    data.pop("is_recipe", None)
    data.pop("rejection_reason", None)
    return data


def ensure_is_recipe(extraction: RecipeExtraction) -> None:
    """Raise NotARecipeError when the model (or our checks) say it is not a recipe."""
    if not extraction.is_recipe:
        reason = (extraction.rejection_reason or "").strip() or "Source is not a recipe"
        raise NotARecipeError(f"This doesn't look like a recipe: {reason}")
    name = extraction.name.strip()
    has_ingredients = any(i.food.strip() for i in extraction.recipeIngredients)
    has_steps = any(s.text.strip() for s in extraction.recipeInstructions)
    if not name or not (has_ingredients or has_steps):
        raise NotARecipeError(
            "This doesn't look like a recipe: "
            + (extraction.rejection_reason.strip() or "Source did not contain a usable recipe")
        )


def is_iso8601_duration(value: str) -> bool:
    return bool(value) and bool(_ISO8601_DURATION.match(value.strip()))


def parse_amount(value: str) -> float | None:
    if not value:
        return None
    match = _AMOUNT.search(value.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def clamp_servings(servings: int) -> int | None:
    if SERVINGS_MIN <= servings <= SERVINGS_MAX:
        return servings
    return None


def sanitize_duration(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if is_iso8601_duration(text) else None


def sanitize_nutrition_fields(nutrition: dict) -> dict:
    """Keep Schema.org nutrition keys whose numeric values sit in plausible bounds."""
    cleaned = {"@type": "NutritionInformation"}
    for key, (lo, hi) in NUTRITION_BOUNDS.items():
        raw = nutrition.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        amount = parse_amount(text)
        if amount is None or amount < lo or amount > hi:
            continue
        cleaned[key] = text
    return cleaned


def apply_yield_nutrition_guardrails(
    recipe: dict,
    estimate: YieldNutritionEstimate,
    *,
    need_yield: bool,
    need_nutrition: bool,
    need_prep_time: bool,
    need_cook_time: bool,
    need_total_time: bool,
) -> dict:
    """Copy estimate fields onto the recipe only when they pass bounds checks."""
    servings = clamp_servings(estimate.servings)

    if need_yield:
        ry = (estimate.recipeYield or "").strip()
        if not ry and servings is not None:
            ry = f"{servings} servings"
        if ry:
            recipe["recipeYield"] = str(ry)

    if need_prep_time:
        prep = sanitize_duration(estimate.prepTime)
        if prep:
            recipe["prepTime"] = prep

    if need_cook_time:
        cook = sanitize_duration(estimate.cookTime)
        if cook:
            recipe["cookTime"] = cook

    if need_total_time:
        total = sanitize_duration(estimate.totalTime)
        if total:
            recipe["totalTime"] = total

    if need_nutrition:
        nutrition = sanitize_nutrition_fields(estimate.nutrition.model_dump())
        if any(k in nutrition for k in NUTRITION_BOUNDS):
            recipe["nutrition"] = nutrition

    return recipe
