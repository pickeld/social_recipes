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

    def _build_update_payload(self, original_recipe_schema: dict) -> dict:
        """
        Build payload for PATCH/PUT to /api/recipes/{id|slug} using Mealie internal field names.
        
        Mealie RecipeIngredient schema:
        - quantity: float (optional)
        - unit: object with 'name' field or null (Mealie auto-creates units)
        - food: object with 'name' field or null (Mealie auto-creates foods)
        - note: string (optional)
        - display: string (the full ingredient text for display)
        - originalText: string (optional, original parsed text)
        """
        ry = original_recipe_schema.get("recipeYield") or ""
        ry_qty = extract_servings(original_recipe_schema)

        # Ingredients - build from structured data
        ing_struct = original_recipe_schema.get(
            "recipeIngredientStructured") or []
        ingredients = []
        
        # If no structured ingredients, fall back to simple ingredient strings
        if not ing_struct:
            simple_ingredients = original_recipe_schema.get("recipeIngredient") or []
            for line in simple_ingredients:
                if isinstance(line, str) and line.strip():
                    ingredients.append({
                        "quantity": None,
                        "unit": None,
                        "food": None,
                        "note": line.strip(),
                        "display": line.strip(),
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
                notes = (item.get("notes") or "").strip()
                note_parts = []
                if notes:
                    note_parts.append(notes)
                if modifiers:
                    note_parts.append(" ".join(modifiers))
                note = " | ".join(note_parts) or None
                display = raw or " ".join(
                    filter(None, [qty_s, unit_name, food_name])).strip()
                
                # Mealie expects unit and food as objects with 'name' field, not just strings
                # Mealie will auto-create units/foods when given objects with 'name'
                unit_obj = {"name": unit_name} if unit_name else None
                food_obj = {"name": food_name} if food_name else None
                
                ingredients.append(
                    {
                        "quantity": qty_num,
                        "unit": unit_obj,
                        "food": food_obj,
                        "note": note,
                        "display": display,
                        "originalText": raw or None,
                    }
                )

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

        update_payload = {
            "name": original_recipe_schema.get("name") or original_recipe_schema.get("title") or "Untitled",
            "description": original_recipe_schema.get("description") or "",
            "recipeYield": ry or None,
            "recipeYieldQuantity": ry_qty,
            "recipeServings": ry_qty,
            "recipeIngredient": ingredients,
            "recipeInstructions": instructions,
        }
        
        # Add nutrition if available
        nutrition = self._build_nutrition(original_recipe_schema)
        if nutrition:
            update_payload["nutrition"] = nutrition
            self._log(f"Including nutrition: {nutrition}")
        
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

        ingr_list = current.get("recipeIngredient") or []
        placeholder = (
            isinstance(ingr_list, list)
            and len(ingr_list) == 1
            and isinstance(ingr_list[0], dict)
            and (ingr_list[0].get("note") == "1 Cup Flour" or ingr_list[0].get("display") == "1 Cup Flour")
        )
        if not placeholder and ingr_list:
            self._log(f"Server already populated ingredients ({len(ingr_list)}). Skipping update.")
            return current

        # Build update payload - merge with existing recipe to ensure all required fields present
        update_fields = self._build_update_payload(recipe_data)
        self._log(f"Update ingredients={len(update_fields['recipeIngredient'])} instructions={len(update_fields['recipeInstructions'])}")
        
        # Merge update fields into the existing recipe (Mealie PUT requires complete object)
        update_payload = current.copy()
        update_payload.update(update_fields)

        # Use PUT (Mealie's PATCH can be temperamental)
        put_url = f"{self.base_url}/api/recipes/{ident}"
        self._log(f"PUT {put_url}")
        put_resp = self._session.put(put_url, json=update_payload, headers=headers)
        self._log(f"PUT -> {put_resp.status_code}")
        
        if put_resp.status_code >= 400:
            self._log(f"Update error body: {put_resp.text[:1000]}")
            put_resp.raise_for_status()
        
        try:
            return put_resp.json()
        except Exception:
            return {"id": ident, "update_raw": put_resp.text}

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
