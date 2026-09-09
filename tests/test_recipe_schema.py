"""Tests for recipe LLM schemas and post-parse guardrails."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from recipe_schema import (  # noqa: E402
    RECIPE_JSON_SCHEMA,
    NotARecipeError,
    ValidationError,
    WrongLanguageError,
    apply_yield_nutrition_guardrails,
    ensure_is_recipe,
    ensure_target_language,
    parse_recipe_extraction,
    parse_yield_nutrition,
    recipe_dict_from_extraction,
    sanitize_duration,
    sanitize_nutrition_fields,
)


def _valid_recipe(**overrides):
    data = {
        "is_recipe": True,
        "rejection_reason": "",
        "name": "Tomato pasta",
        "description": "A simple pasta.",
        "datePublished": "2026-09-09",
        "recipeYield": "2 servings",
        "recipeInstructions": [{"text": "Boil pasta."}],
        "recipeIngredients": [{
            "food": "pasta",
            "quantity": "200",
            "unit": "g",
            "notes": "",
            "raw": "200 g pasta",
        }],
    }
    data.update(overrides)
    return data


def _valid_nutrition(**overrides):
    data = {
        "servings": 4,
        "recipeYield": "4 servings",
        "prepTime": "PT15M",
        "cookTime": "PT30M",
        "totalTime": "PT45M",
        "nutrition": {
            "calories": "450 kcal",
            "proteinContent": "20 g",
            "fatContent": "18 g",
            "carbohydrateContent": "55 g",
            "fiberContent": "4 g",
            "sugarContent": "3 g",
            "sodiumContent": "680 mg",
            "cholesterolContent": "70 mg",
        },
    }
    data.update(overrides)
    return data


class ParseRecipeExtractionTests(unittest.TestCase):
    def test_parses_valid_payload(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe()))
        self.assertEqual(extraction.name, "Tomato pasta")
        ensure_is_recipe(extraction)

    def test_strips_markdown_fences(self):
        body = "```json\n" + json.dumps(_valid_recipe()) + "\n```"
        extraction = parse_recipe_extraction(body)
        self.assertEqual(extraction.name, "Tomato pasta")

    def test_rejects_extra_fields(self):
        payload = _valid_recipe()
        payload["surprise"] = "nope"
        with self.assertRaises(ValidationError):
            parse_recipe_extraction(json.dumps(payload))

    def test_not_a_recipe_flag(self):
        payload = _valid_recipe(
            is_recipe=False,
            rejection_reason="This is a travel vlog",
            name="",
            recipeInstructions=[],
            recipeIngredients=[],
        )
        extraction = parse_recipe_extraction(json.dumps(payload))
        with self.assertRaises(NotARecipeError) as ctx:
            ensure_is_recipe(extraction)
        self.assertIn("travel vlog", str(ctx.exception))

    def test_empty_name_is_not_a_recipe(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe(name="  ")))
        with self.assertRaises(NotARecipeError):
            ensure_is_recipe(extraction)

    def test_recipe_dict_drops_guardrail_fields(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe()))
        data = recipe_dict_from_extraction(extraction)
        self.assertNotIn("is_recipe", data)
        self.assertNotIn("rejection_reason", data)
        self.assertEqual(data["name"], "Tomato pasta")


class TargetLanguageTests(unittest.TestCase):
    def test_accepts_hebrew_when_target_is_he(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe(
            name="פסטה עגבניות עם בזיליקום",
            description="מנה פשוטה ליום חול עם רוטב עגבניות.",
            recipeInstructions=[{"text": "מרתיחים מים במסיר גדול ומוסיפים את הפסטה."}],
        )))
        ensure_target_language(extraction, "he")

    def test_rejects_english_when_target_is_hebrew(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe(
            name="Tomato basil pasta bake",
            description="A simple weeknight pasta with a tomato sauce.",
            recipeInstructions=[{"text": "Boil a large pot of water and cook the pasta."}],
        )))
        with self.assertRaises(WrongLanguageError):
            ensure_target_language(extraction, "he")

    def test_skips_when_sample_is_too_short(self):
        extraction = parse_recipe_extraction(json.dumps(_valid_recipe(
            name="Pie",
            description="Ok.",
            recipeInstructions=[{"text": "Mix."}],
        )))
        ensure_target_language(extraction, "he")


class OpenAISchemaTests(unittest.TestCase):
    def test_strict_object_contract(self):
        self.assertEqual(RECIPE_JSON_SCHEMA.get("additionalProperties"), False)
        required = RECIPE_JSON_SCHEMA.get("required") or []
        self.assertIn("is_recipe", required)
        self.assertIn("recipeIngredients", required)
        self.assertNotIn("default", RECIPE_JSON_SCHEMA)


class NutritionGuardrailTests(unittest.TestCase):
    def test_drops_impossible_calories(self):
        cleaned = sanitize_nutrition_fields({"calories": "90000 kcal", "proteinContent": "20 g"})
        self.assertNotIn("calories", cleaned)
        self.assertEqual(cleaned["proteinContent"], "20 g")

    def test_accepts_iso_durations(self):
        self.assertEqual(sanitize_duration("PT15M"), "PT15M")
        self.assertEqual(sanitize_duration("PT1H30M"), "PT1H30M")
        self.assertIsNone(sanitize_duration("15 minutes"))
        self.assertIsNone(sanitize_duration("PT"))

    def test_clamps_servings_and_times(self):
        estimate = parse_yield_nutrition(json.dumps(_valid_nutrition(
            servings=999,
            recipeYield="",
            prepTime="soon",
            cookTime="PT30M",
            nutrition={"calories": "450 kcal", **{
                k: "" for k in (
                    "proteinContent", "fatContent", "carbohydrateContent",
                    "fiberContent", "sugarContent", "sodiumContent",
                    "cholesterolContent",
                )
            }},
        )))
        recipe = apply_yield_nutrition_guardrails(
            {},
            estimate,
            need_yield=True,
            need_nutrition=True,
            need_prep_time=True,
            need_cook_time=True,
            need_total_time=True,
        )
        self.assertNotIn("recipeYield", recipe)
        self.assertNotIn("prepTime", recipe)
        self.assertEqual(recipe["cookTime"], "PT30M")
        self.assertEqual(recipe["nutrition"]["calories"], "450 kcal")


class ChefGuardrailTests(unittest.TestCase):
    def setUp(self):
        from chef import Chef
        self.chef = Chef(
            source_url="https://example.com/video",
            description="",
            transcription="מרתיחים פסטה עם רוטב עגבניות.",
        )
        self.chef._enrich_yield_and_nutrition = lambda recipe: recipe

    def test_create_recipe_from_structured_payload(self):
        payload = _valid_recipe(
            name="פסטה עגבניות ביתית עם בזיליקום",
            description="מנה פשוטה ליום חול עם רוטב עגבניות טרי.",
            recipeInstructions=[{"text": "מרתיחים מים במסיר גדול ומוסיפים את הפסטה."}],
        )
        payload["recipeIngredients"][0]["unit"] = "tablespoon"
        self.chef._call_llm = lambda *args, **kwargs: json.dumps(payload)
        recipe = self.chef.create_recipe()
        self.assertEqual(recipe["name"], "פסטה עגבניות ביתית עם בזיליקום")
        self.assertEqual(recipe["@type"], "Recipe")
        self.assertEqual(recipe["recipeInstructions"][0]["@type"], "HowToStep")
        self.assertEqual(recipe["recipeIngredients"][0]["unit"], "tbsp")

    def test_create_recipe_rejects_non_recipe(self):
        payload = _valid_recipe(
            is_recipe=False,
            rejection_reason="just a song",
            name="",
            recipeInstructions=[],
            recipeIngredients=[],
        )
        self.chef._call_llm = lambda *args, **kwargs: json.dumps(payload)
        with self.assertRaises(NotARecipeError):
            self.chef.create_recipe(max_retries=1)


if __name__ == "__main__":
    unittest.main()
