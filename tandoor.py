"""
Tandoor Recipes exporter.

This module provides functionality to export recipes to Tandoor Recipes.
API documentation: https://docs.tandoor.dev/api/
"""

from config import config
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
        Build Tandoor ingredient list from structured recipe data.
        
        Expects recipeIngredients from LLM with: food, quantity, unit, notes, raw
        Tandoor API requires: food ({"name": str}), unit ({"name": str} or null), amount (float)
        """
        ing_struct = recipe_data.get("recipeIngredients") or []
        
        if not isinstance(ing_struct, list) or not ing_struct:
            logger.warning("[Tandoor] No structured ingredients found")
            return []
        
        ingredients = []
        for order, item in enumerate(ing_struct):
            if not isinstance(item, dict):
                continue

            raw = (item.get("raw") or "").strip()
            qty_s = (item.get("quantity") or "").strip()
            unit_name = (item.get("unit") or "").strip()
            food_name = (item.get("food") or "").strip()
            notes = (item.get("notes") or "").strip()
            
            # Food name is required - use fallbacks if needed
            if not food_name:
                food_name = raw[:128] if raw else None
            if not food_name:
                logger.warning(f"[Tandoor] Skipping ingredient with no food name: {item}")
                continue

            amount = coerce_num(qty_s)
            ingredients.append({
                "amount": amount if amount > 0 else 0,
                "unit": {"name": unit_name} if unit_name else None,
                "food": {"name": food_name[:128]},
                "note": notes,
                "order": order,
                "is_header": False,
                "no_amount": amount == 0,
                "original_text": raw[:512] if raw else "",
            })

        logger.info(f"[Tandoor] Built {len(ingredients)} ingredients")
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
        elif not ingredients:
            logger.warning("[Tandoor] No ingredients found in recipe data")

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
        
        if keywords:
            payload["keywords"] = keywords
        
        nutrition = self._build_nutrition(recipe_data)
        if nutrition:
            payload["nutrition"] = nutrition
        
        return payload

    def create_recipe(self, recipe_data: dict) -> dict:
        """
        Create a recipe in Tandoor with a single API call.
        POST /api/recipe/
        
        Tandoor automatically creates units and foods when given just names,
        so no pre-creation API calls are needed.
        """
        headers = self._build_headers()
        payload = self._to_tandoor_payload(recipe_data)
        create_url = f"{self.base_url}/api/recipe/"

        recipe_name = payload.get('name', 'Unknown')
        logger.info(f"[Tandoor] Creating recipe: {recipe_name}")
        self._log(f"Creating recipe: {recipe_name}")

        resp = self._session.post(create_url, json=payload, headers=headers, timeout=120)

        if resp.status_code >= 400:
            logger.error(f"[Tandoor] Failed to create recipe: HTTP {resp.status_code}")
            self._log(f"Error: {resp.text[:500]}")
            resp.raise_for_status()

        try:
            result = resp.json()
            recipe_id = result.get('id')
            logger.info(f"[Tandoor] Recipe created with ID: {recipe_id}")
            self._log(f"Recipe ID: {recipe_id}")
            return result
        except Exception as e:
            logger.warning(f"[Tandoor] Could not parse response: {e}")
            return {"raw": resp.text}
