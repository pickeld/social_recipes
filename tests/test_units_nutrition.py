"""Tests for unit conversion and local/USDA-style nutrition lookup."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nutrition import lookup_recipe_nutrition, match_food  # noqa: E402
from recipe_schema import parse_frame_selection, parse_visual_text  # noqa: E402
from units import canonicalize_unit, normalize_ingredient_units, quantity_to_grams  # noqa: E402


class UnitConversionTests(unittest.TestCase):
    def test_canonicalizes_volume_spellings(self):
        self.assertEqual(canonicalize_unit("tablespoon"), "tbsp")
        self.assertEqual(canonicalize_unit("כף"), "tbsp")
        self.assertEqual(canonicalize_unit("cup"), "cup")
        self.assertEqual(canonicalize_unit("grams"), "g")
        self.assertEqual(canonicalize_unit("גרם"), "g")

    def test_quantity_to_grams(self):
        self.assertAlmostEqual(quantity_to_grams("2", "tbsp"), 30.0)
        self.assertAlmostEqual(quantity_to_grams("1", "cup"), 240.0)
        self.assertAlmostEqual(quantity_to_grams("200", "g"), 200.0)
        self.assertIsNone(quantity_to_grams("1", "handful"))

    def test_normalize_ingredient_units_in_place(self):
        items = [{"food": "flour", "quantity": "1", "unit": "cup", "notes": "", "raw": ""}]
        normalize_ingredient_units(items)
        self.assertEqual(items[0]["unit"], "cup")
        items[0]["unit"] = "tablespoons"
        normalize_ingredient_units(items)
        self.assertEqual(items[0]["unit"], "tbsp")


class NutritionLookupTests(unittest.TestCase):
    def test_matches_hebrew_and_english_foods(self):
        self.assertEqual(match_food("pasta"), "pasta")
        self.assertEqual(match_food("פסטה"), "pasta")
        self.assertEqual(match_food("olive oil"), "olive oil")

    def test_estimates_per_serving_from_local_table(self):
        recipe = {
            "recipeYield": "2 servings",
            "recipeIngredients": [
                {"food": "pasta", "quantity": "200", "unit": "g", "notes": "", "raw": "200 g pasta"},
                {"food": "olive oil", "quantity": "1", "unit": "tbsp", "notes": "", "raw": "1 tbsp oil"},
            ],
        }
        nutrition = lookup_recipe_nutrition(recipe)
        self.assertIsNotNone(nutrition)
        kcal = float(str(nutrition["calories"]).split()[0])
        # 200 g pasta ≈ 262 kcal + 15 ml oil ≈ 119 kcal → ~381 / 2 servings ≈ 190
        self.assertGreater(kcal, 100)
        self.assertLess(kcal, 300)

    def test_skips_when_most_ingredients_unknown(self):
        recipe = {
            "recipeYield": "2 servings",
            "recipeIngredients": [
                {"food": "mystery spice blend", "quantity": "1", "unit": "tsp", "notes": "", "raw": ""},
                {"food": "another unknown", "quantity": "2", "unit": "g", "notes": "", "raw": ""},
            ],
        }
        self.assertIsNone(lookup_recipe_nutrition(recipe))


class VisionSchemaTests(unittest.TestCase):
    def test_visual_text_joins_sections(self):
        payload = {
            "title": "Pasta",
            "ingredients_text": "200 g pasta",
            "instructions_text": "Boil.",
            "other_text": "",
        }
        text = parse_visual_text(json.dumps(payload)).as_plain_text()
        self.assertIn("Pasta", text)
        self.assertIn("200 g pasta", text)
        self.assertIn("Boil.", text)

    def test_frame_selection_bounds(self):
        self.assertEqual(parse_frame_selection('{"index": 2}', 5), 2)
        self.assertIsNone(parse_frame_selection('{"index": 9}', 5))
        self.assertIsNone(parse_frame_selection("not json", 5))


if __name__ == "__main__":
    unittest.main()
