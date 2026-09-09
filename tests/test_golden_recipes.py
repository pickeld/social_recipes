"""Golden Chef packets: Hebrew/English fixtures, no live downloads."""

import glob
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from chef import (  # noqa: E402
    RecipeEditError,
    apply_confirmed_recipe,
    build_recipe_user_payload,
    postprocess_recipe,
)
from nutrition import lookup_recipe_nutrition  # noqa: E402
from recipe_schema import (  # noqa: E402
    NotARecipeError,
    ensure_is_recipe,
    ensure_target_language,
    parse_recipe_extraction,
    recipe_dict_from_extraction,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "golden")


def _load_fixtures() -> list[dict]:
    paths = sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.json")))
    fixtures = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            fixtures.append(json.load(fh))
    return fixtures


class GoldenFixtureTests(unittest.TestCase):
    def test_fixture_set_is_complete(self):
        fixtures = _load_fixtures()
        self.assertGreaterEqual(len(fixtures), 10)
        kinds = {f["kind"] for f in fixtures}
        self.assertIn("video", kinds)
        self.assertIn("web", kinds)
        langs = {f["language"] for f in fixtures}
        self.assertIn("he", langs)
        self.assertIn("en", langs)

    def test_expected_json_passes_schema_and_language(self):
        for fixture in _load_fixtures():
            with self.subTest(fixture["id"]):
                extraction = parse_recipe_extraction(json.dumps(fixture["expected"]))
                if not fixture["expected"]["is_recipe"]:
                    with self.assertRaises(NotARecipeError):
                        ensure_is_recipe(extraction)
                    continue
                ensure_is_recipe(extraction)
                ensure_target_language(extraction, fixture["language"])
                recipe = postprocess_recipe(
                    recipe_dict_from_extraction(extraction),
                    fixture["packet"].get("source_url"),
                )
                self.assertTrue(
                    recipe.get("recipeIngredient") or recipe.get("recipeInstructions")
                )
                if fixture.get("expect_nutrition"):
                    self.assertIsNotNone(lookup_recipe_nutrition(recipe))
                if fixture.get("expect_empty_quantities"):
                    for item in extraction.recipeIngredients:
                        self.assertEqual(item.quantity, "")
                        self.assertEqual(item.unit, "")
                if fixture.get("assert_quantity"):
                    food, qty = fixture["assert_quantity"]
                    found = next(
                        i for i in extraction.recipeIngredients if i.food == food
                    )
                    self.assertEqual(found.quantity, qty)


class ChefPacketTests(unittest.TestCase):
    def test_payload_puts_ocr_in_on_screen_text(self):
        payload = build_recipe_user_payload(
            source_url="https://example.com/v",
            description="desc",
            audio_transcript="audio says 500 g flour",
            visual_text="200 g flour",
            previous_attempt_error="not valid JSON",
        )
        self.assertEqual(payload["on_screen_text"], "200 g flour")
        self.assertEqual(payload["audio_transcript"], "audio says 500 g flour")
        self.assertIn("Never invent", payload["quantity_rule"])
        self.assertEqual(payload["previous_attempt_error"], "not valid JSON")
        self.assertIn("Do not invent quantities", payload["correction_hint"])

    def test_apply_confirmed_recipe_merges_and_looks_up(self):
        existing = postprocess_recipe(
            {
                "name": "Tomato pasta",
                "description": "A simple pasta.",
                "recipeIngredients": [
                    {
                        "food": "pasta",
                        "quantity": "200",
                        "unit": "g",
                        "notes": "",
                        "raw": "200 g pasta",
                    }
                ],
                "recipeInstructions": [{"text": "Boil pasta."}],
                "nutrition": {"calories": "9999 kcal"},
            },
            "https://example.com/v",
        )
        result = apply_confirmed_recipe(
            existing,
            {
                "name": "Lighter pasta",
                "recipeIngredients": [
                    {
                        "food": "pasta",
                        "quantity": "100",
                        "unit": "g",
                        "notes": "",
                        "raw": "100 g pasta",
                    }
                ],
            },
        )
        self.assertEqual(result["name"], "Lighter pasta")
        self.assertEqual(result["recipeIngredients"][0]["quantity"], "100")
        self.assertIn("pasta", result["recipeIngredient"][0])
        self.assertIsNotNone(result.get("nutrition"))
        kcal = float(str(result["nutrition"]["calories"]).split()[0])
        self.assertLess(kcal, 9000)

    def test_apply_confirmed_recipe_drops_guessed_macros_on_miss(self):
        existing = {
            "name": "Mystery bowl",
            "description": "Unknown spices.",
            "recipeIngredients": [
                {
                    "food": "mystery spice blend",
                    "quantity": "1",
                    "unit": "tsp",
                    "notes": "",
                    "raw": "",
                }
            ],
            "recipeInstructions": [{"text": "Stir."}],
            "nutrition": {"calories": "9999 kcal"},
        }
        result = apply_confirmed_recipe(existing, {})
        self.assertNotIn("nutrition", result)

    def test_apply_confirmed_recipe_requires_name(self):
        with self.assertRaises(RecipeEditError):
            apply_confirmed_recipe(
                {"name": "Keep", "recipeIngredients": [], "recipeInstructions": []},
                {"name": "  "},
            )


if __name__ == "__main__":
    unittest.main()
