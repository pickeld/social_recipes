"""
Tandoor Recipes exporter.

This module provides functionality to export recipes to Tandoor Recipes.
API documentation: https://docs.tandoor.dev/api/
"""

from config import config
import re

from recipe_exporter import RecipeExporter
from helpers import coerce_num, parse_iso_duration, extract_servings, parse_nutrition_value, setup_logger

logger = setup_logger(__name__)


class Tandoor(RecipeExporter):
    """Export recipes to Tandoor Recipes."""

    def __init__(self):
        super().__init__(
            api_key=config.TANDOOR_API_KEY,
            base_url=config.TANDOOR_HOST,
            name="Tandoor"
        )

    def _get_image_upload_url(self, recipe_id: str | int) -> str:
        """Get the URL for image upload endpoint."""
        return f"{self.base_url}/api/recipe/{recipe_id}/image/"

    def _build_ingredients(self, recipe_data: dict) -> list[dict]:
        """
        Build Tandoor ingredient list from recipe data.
        
        According to Tandoor API (v2.3.6):
        - Ingredient requires: amount (float), food (Food|null), unit (Unit|null)
        - Food requires: name (string, minLength: 1)
        - Unit requires: name (string, minLength: 1)
        
        Tandoor will auto-create units and foods when given objects with just 'name'.
        When no food/unit is available, pass null (not an empty object).
        """
        ingredients = []
        order = 0

        # Prefer structured ingredients
        ing_struct = recipe_data.get("recipeIngredientStructured") or []
        logger.info(f"[Tandoor] Building ingredients: structured={len(ing_struct) if ing_struct else 0}")
        
        if isinstance(ing_struct, list) and ing_struct:
            for item in ing_struct:
                if not isinstance(item, dict):
                    continue

                raw = (
                    item.get("raw") or item.get("normalized") or ""
                ).strip()
                qty_s = (item.get("quantity") or "").strip()
                unit_name = (item.get("unit") or "").strip()
                food_name = (item.get("food") or "").strip()
                modifiers = item.get("modifiers") or []
                if isinstance(modifiers, str):
                    modifiers = [m.strip()
                                 for m in modifiers.split(",") if m.strip()]
                notes = (item.get("notes") or "").strip()

                # Combine modifiers + notes
                note_parts = []
                if notes:
                    note_parts.append(notes)
                if modifiers:
                    note_parts.append(" ".join(modifiers))
                note = " | ".join([p for p in note_parts if p]) or None

                amount = coerce_num(qty_s)
                
                # Tandoor API: food and unit must be objects with 'name' field or null
                # Do NOT pass empty objects {} - only {"name": "value"} or null
                unit_obj = {"name": unit_name} if unit_name else None
                food_obj = {"name": food_name} if food_name else None

                # If we have no food name but have raw text, use raw text as food name
                # This ensures Tandoor has something to display
                if not food_obj and raw:
                    food_obj = {"name": raw[:128]}  # Tandoor food name max is 128 chars

                ingredient = {
                    "amount": amount if amount > 0 else 0,
                    "unit": unit_obj,
                    "food": food_obj,
                    "note": note or "",
                    "order": order,
                    "is_header": False,
                    "no_amount": amount == 0,
                }
                
                # Store original text for reference
                if raw:
                    ingredient["original_text"] = raw[:512]  # max 512 chars per API
                    
                ingredients.append(ingredient)
                order += 1
        else:
            # Fallback to simple ingredient strings
            for line in recipe_data.get("recipeIngredient", []):
                if not isinstance(line, str):
                    continue
                raw = line.strip()
                if not raw:
                    continue

                # Enhanced parsing: number + optional unit + rest as food
                # Pattern: optional amount, optional unit, required food
                m2 = re.match(
                    r"^\s*(\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?)?[\s]*"  # amount (optional, with range support)
                    r"([a-zA-Z]+(?:\s+[a-zA-Z]+)?)?[\s]*"  # unit (optional, 1-2 words)
                    r"(.+)?$",  # food (rest)
                    raw
                )
                amount = 0
                unit_name = ""
                food_name = ""

                if m2:
                    amount = coerce_num(m2.group(1) or "")
                    potential_unit = (m2.group(2) or "").strip()
                    food_name = (m2.group(3) or "").strip()
                    
                    # Common unit abbreviations and names
                    common_units = {
                        'g', 'kg', 'mg', 'lb', 'lbs', 'oz', 'ml', 'l', 'dl', 'cl',
                        'cup', 'cups', 'tbsp', 'tsp', 'tablespoon', 'tablespoons',
                        'teaspoon', 'teaspoons', 'piece', 'pieces', 'slice', 'slices',
                        'clove', 'cloves', 'pinch', 'bunch', 'can', 'cans',
                        'package', 'packages', 'pkg', 'jar', 'bottle', 'head',
                        'stalk', 'stalks', 'sprig', 'sprigs', 'handful', 'dash'
                    }
                    
                    # Only treat as unit if it's a known unit
                    if potential_unit.lower() in common_units:
                        unit_name = potential_unit
                    else:
                        # Not a known unit, prepend to food name
                        if potential_unit and food_name:
                            food_name = f"{potential_unit} {food_name}"
                        elif potential_unit:
                            food_name = potential_unit

                # Ensure food_name exists; use raw if needed
                if not food_name:
                    food_name = raw

                # Build objects according to Tandoor API spec
                unit_obj = {"name": unit_name} if unit_name else None
                food_obj = {"name": food_name[:128]} if food_name else None  # max 128 chars

                ingredient = {
                    "amount": amount if amount > 0 else 0,
                    "unit": unit_obj,
                    "food": food_obj,
                    "note": "",
                    "order": order,
                    "is_header": False,
                    "no_amount": amount == 0,
                    "original_text": raw[:512],  # max 512 chars per API
                }
                ingredients.append(ingredient)
                order += 1

        return ingredients

    def _build_steps(self, recipe_data: dict) -> list[dict]:
        """
        Build Tandoor step list from recipe instructions.
        
        According to Tandoor API (v2.3.6) Step schema:
        - instruction: string (the step text)
        - ingredients: array of Ingredient objects (required)
        - order: integer
        - time: integer (time in minutes for this step)
        - name: string (optional step name/header, max 128 chars)
        - show_as_header: boolean
        - show_ingredients_table: boolean
        """
        steps = []
        instructions_src = recipe_data.get("recipeInstructions", [])
        order = 0

        for step in instructions_src:
            text = ""
            step_name = ""
            
            if isinstance(step, dict):
                # Handle HowToStep schema.org format
                text = (step.get("text") or step.get("description") or "").strip()
                step_name = (step.get("name") or "").strip()
                
                # Handle HowToSection which groups steps
                if step.get("@type") == "HowToSection":
                    section_name = step.get("name", "")
                    item_list = step.get("itemListElement", [])
                    
                    # Add section header as a step
                    if section_name:
                        section_step = {
                            "instruction": "",
                            "ingredients": [],
                            "order": order,
                            "time": 0,
                            "name": section_name[:128],
                            "show_as_header": True,
                            "show_ingredients_table": False,
                        }
                        steps.append(section_step)
                        order += 1
                    
                    # Process nested steps
                    for nested_step in item_list:
                        if isinstance(nested_step, dict):
                            nested_text = (nested_step.get("text") or "").strip()
                            if nested_text:
                                step_obj = {
                                    "instruction": nested_text,
                                    "ingredients": [],
                                    "order": order,
                                    "time": 0,
                                    "name": "",
                                    "show_as_header": False,
                                    "show_ingredients_table": False,
                                }
                                steps.append(step_obj)
                                order += 1
                        elif isinstance(nested_step, str) and nested_step.strip():
                            step_obj = {
                                "instruction": nested_step.strip(),
                                "ingredients": [],
                                "order": order,
                                "time": 0,
                                "name": "",
                                "show_as_header": False,
                                "show_ingredients_table": False,
                            }
                            steps.append(step_obj)
                            order += 1
                    continue  # Already processed section items
                    
            elif isinstance(step, str):
                text = step.strip()

            if text:
                step_obj = {
                    "instruction": text,
                    "ingredients": [],
                    "order": order,
                    "time": 0,
                    "name": step_name[:128] if step_name else "",
                    "show_as_header": False,
                    "show_ingredients_table": False,
                }
                steps.append(step_obj)
                order += 1

        return steps

    def _build_keywords(self, recipe_data: dict) -> list[dict]:
        """
        Build Tandoor keyword list from recipe data.
        
        According to Tandoor API (v2.3.6) Keyword schema:
        - name: string (max 64 chars, required)
        - description: string (optional)
        
        Tandoor auto-creates keywords when given objects with just 'name'.
        """
        keywords = []
        seen_names = set()
        
        # Extract from recipeCategory
        categories = recipe_data.get("recipeCategory", [])
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(",") if c.strip()]
        elif not isinstance(categories, list):
            categories = []
            
        for cat in categories:
            if isinstance(cat, str) and cat.strip():
                name = cat.strip()[:64]  # max 64 chars
                if name.lower() not in seen_names:
                    keywords.append({"name": name})
                    seen_names.add(name.lower())
        
        # Extract from recipeCuisine
        cuisines = recipe_data.get("recipeCuisine", [])
        if isinstance(cuisines, str):
            cuisines = [c.strip() for c in cuisines.split(",") if c.strip()]
        elif not isinstance(cuisines, list):
            cuisines = []
            
        for cuisine in cuisines:
            if isinstance(cuisine, str) and cuisine.strip():
                name = cuisine.strip()[:64]
                if name.lower() not in seen_names:
                    keywords.append({"name": name})
                    seen_names.add(name.lower())
        
        # Extract from keywords field (common in recipe data)
        kw_list = recipe_data.get("keywords", [])
        if isinstance(kw_list, str):
            kw_list = [k.strip() for k in kw_list.split(",") if k.strip()]
        elif not isinstance(kw_list, list):
            kw_list = []
            
        for kw in kw_list:
            if isinstance(kw, str) and kw.strip():
                name = kw.strip()[:64]
                if name.lower() not in seen_names:
                    keywords.append({"name": name})
                    seen_names.add(name.lower())
        
        # Extract from tags if present
        tags = recipe_data.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        elif not isinstance(tags, list):
            tags = []
            
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                name = tag.strip()[:64]
                if name.lower() not in seen_names:
                    keywords.append({"name": name})
                    seen_names.add(name.lower())
        
        return keywords

    def _build_nutrition(self, recipe_data: dict) -> dict | None:
        """
        Build Tandoor nutrition object from Schema.org NutritionInformation.
        
        According to Tandoor API (v2.3.6) NutritionInformation schema:
        - calories: number (required)
        - carbohydrates: number (required)
        - fats: number (required)
        - proteins: number (required)
        
        All values are per serving.
        """
        nutrition = recipe_data.get("nutrition")
        if not isinstance(nutrition, dict):
            return None
        
        # Map Schema.org fields to Tandoor fields
        calories = parse_nutrition_value(nutrition.get("calories"))
        carbs = parse_nutrition_value(nutrition.get("carbohydrateContent"))
        fats = parse_nutrition_value(nutrition.get("fatContent"))
        proteins = parse_nutrition_value(nutrition.get("proteinContent"))
        
        # Only return nutrition if we have at least calories
        if calories > 0:
            return {
                "calories": calories,
                "carbohydrates": carbs,
                "fats": fats,
                "proteins": proteins,
            }
        
        return None

    def _to_tandoor_payload(self, recipe_data: dict) -> dict:
        """
        Map Schema.org style recipe into Tandoor API expected fields.
        
        According to Tandoor API (v2.3.6) Recipe schema, required fields are:
          - name: string (max 128 chars)
          - steps: array of Step objects
          
        Optional fields we use:
          - description: string (max 512 chars)
          - keywords: array of Keyword objects
          - servings: integer
          - servings_text: string (max 32 chars)
          - working_time: integer (minutes)
          - waiting_time: integer (minutes)
          - source_url: string (max 1024 chars)
          - internal: boolean
          - show_ingredient_overview: boolean
        """
        # Name is required, max 128 chars
        name = (
            recipe_data.get("name")
            or recipe_data.get("headline")
            or recipe_data.get("title")
            or "Untitled"
        )[:128]
        
        # Description is optional, max 512 chars
        description = (recipe_data.get("description", "") or "")[:512]
        
        # Servings
        servings = extract_servings(recipe_data)
        servings_text = str(recipe_data.get("recipeYield") or servings)[:32]

        # Parse times
        prep_time = parse_iso_duration(recipe_data.get("prepTime", ""))
        cook_time = parse_iso_duration(recipe_data.get("cookTime", ""))
        total_time = parse_iso_duration(recipe_data.get("totalTime", ""))

        # working_time = prep, waiting_time = cook (or derive from total)
        working_time = prep_time
        waiting_time = cook_time
        if total_time and not (working_time or waiting_time):
            working_time = total_time

        # Source URL, max 1024 chars
        source_url = (recipe_data.get("url") or recipe_data.get("source_url") or "")[:1024]

        # Build ingredients, steps, and keywords
        ingredients = self._build_ingredients(recipe_data)
        steps = self._build_steps(recipe_data)
        keywords = self._build_keywords(recipe_data)

        # Tandoor requires ingredients to be attached to steps.
        # If there are no steps, create a default one.
        if not steps:
            combined_instructions = description or "Follow the recipe instructions."
            steps = [{
                "instruction": combined_instructions,
                "ingredients": [],
                "order": 0,
                "time": working_time + waiting_time,
                "name": "",
                "show_as_header": False,
                "show_ingredients_table": False,
            }]

        # Attach all ingredients to the first step (Tandoor's model)
        if steps and ingredients:
            steps[0]["ingredients"] = ingredients

        payload = {
            "name": name,
            "description": description,
            "servings": servings,
            "servings_text": servings_text,
            "working_time": working_time,
            "waiting_time": waiting_time,
            "source_url": source_url,
            "internal": True,
            "show_ingredient_overview": True,
            "steps": steps,
        }
        
        # Add keywords if any were extracted
        if keywords:
            payload["keywords"] = keywords
        
        # Add nutrition if available
        nutrition_data = recipe_data.get("nutrition")
        logger.info(f"[Tandoor] Recipe nutrition data: {nutrition_data}")
        nutrition = self._build_nutrition(recipe_data)
        logger.info(f"[Tandoor] Built nutrition: {nutrition}")
        if nutrition:
            payload["nutrition"] = nutrition
            self._log(f"Including nutrition: {nutrition}")
        else:
            logger.warning("[Tandoor] No nutrition to include")
        
        return payload

    def create_recipe(self, recipe_data: dict) -> dict:
        """
        Create a recipe in Tandoor with a single API call.
        POST /api/recipe/
        
        Tandoor automatically creates units and foods when given just names,
        so no pre-creation API calls are needed.
        """
        logger.info("[Upload] Starting Tandoor recipe upload...")
        headers = self._build_headers()
        payload = self._to_tandoor_payload(recipe_data)
        create_url = f"{self.base_url}/api/recipe/"

        recipe_name = payload.get('name', 'Unknown')
        logger.info(f"[Upload] Creating recipe in Tandoor: {recipe_name}")
        logger.info(f"[Tandoor] Payload has nutrition: {'nutrition' in payload}, value: {payload.get('nutrition')}")
        self._log(f"Creating recipe: {recipe_name}")
        self._log(f"POST {create_url}")

        resp = self._session.post(create_url, json=payload, headers=headers, timeout=120)
        self._log(f"Response status: {resp.status_code}")

        if resp.status_code >= 400:
            logger.error(f"[Upload] Failed to create recipe in Tandoor: HTTP {resp.status_code}")
            self._log(f"Error response: {resp.text[:1000]}")
            resp.raise_for_status()

        logger.info(f"[Upload] Recipe created successfully (HTTP {resp.status_code})")

        try:
            result = resp.json()
            recipe_id = result.get('id')
            logger.info(f"[Upload] Recipe ID: {recipe_id}")
            self._log(f"Recipe created with ID: {recipe_id}")
            return result
        except Exception as e:
            logger.warning(f"[Upload] Could not parse response JSON: {e}")
            self._log(f"JSON parse error: {e}")
            return {"raw": resp.text}
