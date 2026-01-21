"""
Mealie recipe exporter.

This module provides functionality to export recipes to Mealie.
"""

import os
from config import config

from recipe_exporter import RecipeExporter
from helpers import coerce_num, extract_servings, parse_nutrition_value, setup_logger

logger = setup_logger(__name__)


class Mealie(RecipeExporter):
    """Export recipes to Mealie."""

    def __init__(self):
        super().__init__(
            api_key=config.MEALIE_API_KEY,
            base_url=config.MEALIE_HOST,
            name="Mealie"
        )

    def _get_image_upload_url(self, recipe_id: str | int) -> str:
        """Get the URL for image upload endpoint."""
        return f"{self.base_url}/api/recipes/{recipe_id}/image"

    def _get_all_units(self, headers: dict) -> dict[str, dict]:
        """Fetch all units from Mealie and return a dict keyed by lowercase name."""
        units_url = f"{self.base_url}/api/units?page=1&perPage=-1"
        try:
            resp = self._session.get(units_url, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) if isinstance(data, dict) else data
                return {u.get("name", "").lower(): u for u in items if u.get("name")}
        except Exception as e:
            logger.warning(f"[Mealie] Failed to fetch units: {e}")
        return {}

    def _get_all_foods(self, headers: dict) -> dict[str, dict]:
        """Fetch all foods from Mealie and return a dict keyed by lowercase name."""
        foods_url = f"{self.base_url}/api/foods?page=1&perPage=-1"
        try:
            resp = self._session.get(foods_url, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) if isinstance(data, dict) else data
                return {f.get("name", "").lower(): f for f in items if f.get("name")}
        except Exception as e:
            logger.warning(f"[Mealie] Failed to fetch foods: {e}")
        return {}

    def _create_unit(self, name: str, headers: dict) -> dict | None:
        """Create a new unit in Mealie and return the created object with ID."""
        units_url = f"{self.base_url}/api/units"
        try:
            resp = self._session.post(units_url, json={"name": name}, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.warning(f"[Mealie] Failed to create unit '{name}': {resp.status_code}")
        except Exception as e:
            logger.warning(f"[Mealie] Failed to create unit '{name}': {e}")
        return None

    def _create_food(self, name: str, headers: dict) -> dict | None:
        """Create a new food in Mealie and return the created object with ID."""
        foods_url = f"{self.base_url}/api/foods"
        try:
            resp = self._session.post(foods_url, json={"name": name}, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.warning(f"[Mealie] Failed to create food '{name}': {resp.status_code}")
        except Exception as e:
            logger.warning(f"[Mealie] Failed to create food '{name}': {e}")
        return None

    def _get_or_create_unit(self, name: str, units: dict[str, dict], headers: dict) -> dict | None:
        """Get existing unit or create new one. Returns unit object with ID or None."""
        if not name:
            return None
        name_lower = name.lower()
        if name_lower in units:
            return units[name_lower]
        # Create new unit
        new_unit = self._create_unit(name, headers)
        if new_unit:
            units[name_lower] = new_unit  # Update the dict for subsequent lookups
            return new_unit
        return None

    def _get_or_create_food(self, name: str, foods: dict[str, dict], headers: dict) -> dict | None:
        """Get existing food or create new one. Returns food object with ID or None."""
        if not name:
            return None
        name_lower = name.lower()
        if name_lower in foods:
            return foods[name_lower]
        # Create new food
        new_food = self._create_food(name, headers)
        if new_food:
            foods[name_lower] = new_food  # Update the dict for subsequent lookups
            return new_food
        return None

    def _build_nutrition(self, recipe_data: dict) -> dict | None:
        """
        Build Mealie nutrition object from Schema.org NutritionInformation.
        
        Mealie RecipeNutrition schema supports:
        - calories: string (e.g., "450 kcal")
        - fatContent: string (e.g., "20 g")
        - proteinContent: string (e.g., "15 g")
        - carbohydrateContent: string (e.g., "50 g")
        - fiberContent: string (e.g., "5 g")
        - sodiumContent: string (e.g., "500 mg")
        - sugarContent: string (e.g., "10 g")
        """
        nutrition = recipe_data.get("nutrition")
        if not isinstance(nutrition, dict):
            return None
        
        # Map Schema.org fields to Mealie fields (Mealie uses string format)
        mealie_nutrition = {}
        
        # Standard nutrition fields from Schema.org
        field_mappings = {
            "calories": "calories",
            "fatContent": "fatContent",
            "proteinContent": "proteinContent",
            "carbohydrateContent": "carbohydrateContent",
            "fiberContent": "fiberContent",
            "sodiumContent": "sodiumContent",
            "sugarContent": "sugarContent",
        }
        
        has_any = False
        for schema_field, mealie_field in field_mappings.items():
            value = nutrition.get(schema_field)
            if value:
                mealie_nutrition[mealie_field] = str(value)
                has_any = True
        
        return mealie_nutrition if has_any else None

    def _build_update_payload(self, original_recipe_schema: dict, headers: dict,
                              units: dict[str, dict], foods: dict[str, dict]) -> dict:
        """
        Build payload for PATCH/PUT to /api/recipes/{id|slug} using Mealie internal field names.
        
        Mealie RecipeIngredient schema (from GitHub source):
        - title: string | None (optional header)
        - note: string | None (optional notes)
        - unit: IngredientUnit | None (object with 'id' and 'name' - MUST have id for PUT)
        - food: IngredientFood | None (object with 'id' and 'name' - MUST have id for PUT)
        - disableAmount: bool (default True)
        - quantity: float | None
        - originalText: string | None (original parsed text)
        - referenceId: UUID | None
        
        Args:
            original_recipe_schema: The recipe data in Schema.org format
            headers: HTTP headers for API calls
            units: Dict of existing units keyed by lowercase name
            foods: Dict of existing foods keyed by lowercase name
        """
        ry = original_recipe_schema.get("recipeYield") or ""
        ry_qty = extract_servings(original_recipe_schema)

        # Ingredients - build from structured data
        ing_struct = original_recipe_schema.get(
            "recipeIngredientStructured") or []
        ingredients = []
        
        logger.info(f"[Mealie] Building ingredients: structured={len(ing_struct) if ing_struct else 0}")
        
        # If no structured ingredients, fall back to simple ingredient strings
        if not ing_struct:
            simple_ingredients = original_recipe_schema.get("recipeIngredient") or []
            for line in simple_ingredients:
                if isinstance(line, str) and line.strip():
                    # For simple text ingredients, put the text in note field
                    ingredients.append({
                        "title": None,
                        "note": line.strip(),
                        "unit": None,
                        "food": None,
                        "disableAmount": True,
                        "quantity": None,
                        "originalText": line.strip(),
                    })
        else:
            for item in ing_struct:
                if not isinstance(item, dict):
                    continue
                raw = (item.get("raw") or item.get("normalized") or "").strip()
                qty_s = (item.get("quantity") or "").strip()
                qty_num = coerce_num(qty_s) if qty_s else None
                unit_name = (item.get("unit") or "").strip()
                food_name = (item.get("food") or "").strip()
                modifiers = item.get("modifiers") or []
                if isinstance(modifiers, str):
                    modifiers = [m.strip()
                                 for m in modifiers.split(",") if m.strip()]
                notes_from_item = (item.get("notes") or "").strip()
                
                # Build note from modifiers and notes
                note_parts = []
                if notes_from_item:
                    note_parts.append(notes_from_item)
                if modifiers:
                    note_parts.append(" ".join(modifiers))
                note = " | ".join(note_parts) if note_parts else None
                
                # Get or create unit and food with proper IDs
                unit_obj = self._get_or_create_unit(unit_name, units, headers)
                food_obj = self._get_or_create_food(food_name, foods, headers)
                
                # Build ingredient with proper unit/food objects (with IDs)
                ingredient = {
                    "title": None,
                    "note": note,
                    "unit": unit_obj,  # Now has proper ID from Mealie
                    "food": food_obj,  # Now has proper ID from Mealie
                    "disableAmount": qty_num is None or qty_num == 0,
                    "quantity": qty_num,
                    "originalText": raw if raw else None,
                }
                
                ingredients.append(ingredient)

        # Instructions
        instructions_src = original_recipe_schema.get(
            "recipeInstructions") or []
        instructions = []
        for step in instructions_src:
            text = ""
            if isinstance(step, dict):
                text = (step.get("text") or "").strip()
            elif isinstance(step, str):
                text = step.strip()
            if text:
                instructions.append({
                    "title": "",
                    "summary": "",
                    "text": text,
                    "ingredientReferences": []
                })

        # Get source URL from recipe data
        source_url = (
            original_recipe_schema.get("url") or
            original_recipe_schema.get("source_url") or
            ""
        )

        update_payload = {
            "name": original_recipe_schema.get("name") or original_recipe_schema.get("title") or "Untitled",
            "description": original_recipe_schema.get("description") or "",
            "recipeYield": ry or None,
            "recipeYieldQuantity": ry_qty,
            "recipeServings": ry_qty,
            "recipeIngredient": ingredients,
            "recipeInstructions": instructions,
            "orgURL": source_url,  # Mealie uses orgURL for the source URL
        }
        
        # Add nutrition if available
        nutrition_data = original_recipe_schema.get("nutrition")
        logger.info(f"[Mealie] Recipe nutrition data: {nutrition_data}")
        nutrition = self._build_nutrition(original_recipe_schema)
        logger.info(f"[Mealie] Built nutrition: {nutrition}")
        if nutrition:
            update_payload["nutrition"] = nutrition
            # Enable nutrition display in Mealie settings
            update_payload["settings"] = update_payload.get("settings", {})
            if isinstance(update_payload["settings"], dict):
                update_payload["settings"]["showNutrition"] = True
            self._log(f"Including nutrition: {nutrition}")
        else:
            logger.warning("[Mealie] No nutrition to include")
        
        return update_payload

    def create_recipe(self, recipe_data: dict) -> dict:
        """
        Two-phase creation:
        1. Create base recipe via POST /api/recipes (minimal payload).
        2. If server returns primitive (slug/id) or placeholder ingredient list, PATCH with full structured data.
        """
        logger.info("[Upload] Starting Mealie recipe upload...")
        headers = self._build_headers()
        
        recipe_name = recipe_data.get("name") or recipe_data.get("title") or "Untitled"
        base_payload = {
            "name": recipe_name,
            "description": recipe_data.get("description") or "",
        }
        create_url = f"{self.base_url}/api/recipes"
        logger.info(f"[Upload] Creating recipe in Mealie: {recipe_name}")
        self._log(f"Base create POST {create_url} payload={base_payload}")
        
        resp = self._session.post(create_url, json=base_payload, headers=headers)
        raw_text = resp.text
        self._log(f"Base create status {resp.status_code}")
        
        if resp.status_code >= 400:
            logger.error(f"[Upload] Failed to create recipe in Mealie: HTTP {resp.status_code}")
            self._log(f"Create error body: {raw_text[:1000]}")
            resp.raise_for_status()

        logger.info(f"[Upload] Recipe created successfully (HTTP {resp.status_code})")

        # Parse primitive or object
        created = None
        try:
            created = resp.json()
        except Exception as e:
            self._log(f"Create JSON parse failed: {e}; raw={raw_text[:300]}")
            created = raw_text

        ident = None
        if isinstance(created, dict):
            ident = created.get("id") or created.get("slug")
        elif isinstance(created, (str, int)):
            ident = str(created).strip().strip('"')
        if not ident:
            logger.warning("[Upload] Could not determine recipe identifier")
            self._log("Could not determine created recipe identifier; returning raw.")
            return {"raw": created}
        
        logger.info(f"[Upload] Recipe identifier: {ident}")

        # Fetch current state
        detail_url_candidates = [
            f"{self.base_url}/api/recipes/{ident}",
            f"{self.base_url}/api/recipes/slug/{ident}",
        ]
        current = None
        for du in detail_url_candidates:
            try:
                d_resp = self._session.get(du, headers=headers)
                self._log(f"GET {du} -> {d_resp.status_code}")
                if d_resp.status_code == 200:
                    try:
                        current = d_resp.json()
                        # prefer id if available
                        ident = current.get("id") or ident
                        break
                    except Exception as e2:
                        self._log(f"Detail parse failed: {e2}")
            except Exception as e3:
                self._log(f"Detail fetch exception: {e3}")

        if not isinstance(current, dict):
            self._log("Unable to fetch current dict; returning primitive response.")
            return {"id_or_slug": ident, "raw": created}

        # Always update with our structured data - Mealie's initial creation doesn't include our ingredients
        # The old logic would skip update if server had any ingredients, which caused data loss

        # Fetch existing units and foods from Mealie for proper ID resolution
        logger.info("[Mealie] Fetching existing units and foods...")
        units = self._get_all_units(headers)
        foods = self._get_all_foods(headers)
        logger.info(f"[Mealie] Found {len(units)} units and {len(foods)} foods in Mealie")

        # Build update payload - merge with existing recipe to ensure all required fields present
        update_fields = self._build_update_payload(recipe_data, headers, units, foods)
        self._log(f"Update ingredients={len(update_fields['recipeIngredient'])} instructions={len(update_fields['recipeInstructions'])}")
        
        # Merge update fields into the existing recipe (Mealie PUT requires complete object)
        update_payload = current.copy()
        
        # Log what's in current nutrition before merge
        logger.info(f"[Mealie] Current recipe nutrition before merge: {current.get('nutrition')}")
        
        update_payload.update(update_fields)
        
        # Log what's in update_payload nutrition after merge
        logger.info(f"[Mealie] Update payload nutrition after merge: {update_payload.get('nutrition')}")

        # Use PUT (Mealie's PATCH can be temperamental)
        put_url = f"{self.base_url}/api/recipes/{ident}"
        self._log(f"PUT {put_url}")
        logger.info(f"[Mealie] Sending PUT to {put_url} with {len(update_payload.get('recipeIngredient', []))} ingredients")
        logger.info(f"[Mealie] Payload has nutrition: {'nutrition' in update_payload}, value: {update_payload.get('nutrition')}")
        if update_payload.get('recipeIngredient'):
            logger.info(f"[Mealie] First ingredient in payload: {update_payload['recipeIngredient'][0]}")
        
        try:
            put_resp = self._session.put(put_url, json=update_payload, headers=headers, timeout=60)
            self._log(f"PUT -> {put_resp.status_code}")
            logger.info(f"[Mealie] PUT response status: {put_resp.status_code}")
            
            if put_resp.status_code >= 400:
                self._log(f"Update error body: {put_resp.text[:1000]}")
                logger.error(f"[Mealie] PUT failed: {put_resp.text[:500]}")
                put_resp.raise_for_status()
            
            try:
                result = put_resp.json()
                logger.info(f"[Mealie] PUT succeeded, recipe updated")
                
                # Log what Mealie returned for nutrition and ingredients
                returned_nutrition = result.get('nutrition')
                returned_ingredients = result.get('recipeIngredient', [])
                logger.info(f"[Mealie] Response nutrition: {returned_nutrition}")
                logger.info(f"[Mealie] Response has {len(returned_ingredients)} ingredients")
                if returned_ingredients:
                    logger.info(f"[Mealie] First ingredient: {returned_ingredients[0] if returned_ingredients else 'None'}")
                
                return result
            except Exception:
                return {"id": ident, "update_raw": put_resp.text}
        except Exception as e:
            logger.error(f"[Mealie] PUT request failed with exception: {e}")
            raise

    def upload_image(self, recipe_id: str | int, image_path: str) -> bool:
        """
        Upload an image for a recipe to Mealie.
        
        Mealie's image API endpoint: PUT /api/recipes/{slug}/image
        Accepts multipart form data with the image file.
        
        Args:
            recipe_id: The slug or ID of the recipe.
            image_path: Path to the image file (JPEG/PNG).
            
        Returns:
            True if upload succeeded, False otherwise.
        """
        if not os.path.exists(image_path):
            self._log(f"Image file not found: {image_path}")
            logger.warning(f"[Upload] Image file not found: {image_path}")
            return False

        # Mealie uses PUT for image upload with multipart form data
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        url = self._get_image_upload_url(recipe_id)

        # Determine content type from file extension
        ext = os.path.splitext(image_path)[1].lower()
        content_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

        self._log(f"Uploading image to recipe {recipe_id}")
        logger.info(f"[Upload] Uploading image to Mealie recipe {recipe_id}")

        try:
            with open(image_path, "rb") as f:
                # Mealie expects the file field to be named 'image'
                files = {"image": (os.path.basename(image_path), f, content_type)}
                # Also add extension parameter
                data = {"extension": ext.lstrip(".")}
                resp = self._session.put(url, files=files, data=data, headers=headers, timeout=60)

            self._log(f"Image upload status: {resp.status_code}")

            if resp.status_code in (200, 201, 204):
                self._log("Image uploaded successfully")
                logger.info("[Upload] Image uploaded successfully to Mealie")
                return True
            else:
                self._log(f"Image upload failed: {resp.text[:500]}")
                logger.warning(f"[Upload] Image upload failed: HTTP {resp.status_code}")
                return False
        except Exception as e:
            self._log(f"Image upload error: {e}")
            logger.error(f"[Upload] Image upload error: {e}")
            return False
