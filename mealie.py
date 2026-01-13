"""
Mealie recipe exporter.

This module provides functionality to export recipes to Mealie.
"""

from config import config
import re

from recipe_exporter import RecipeExporter
from helpers import coerce_num, extract_servings, parse_nutrition_value


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
        """
        ry = original_recipe_schema.get("recipeYield") or ""
        ry_qty = extract_servings(original_recipe_schema)

        # Ingredients
        ing_struct = original_recipe_schema.get(
            "recipeIngredientStructured") or []
        ingredients = []
        for item in ing_struct:
            if not isinstance(item, dict):
                continue
            raw = (item.get("raw") or item.get("normalized") or "").strip()
            qty_s = (item.get("quantity") or "").strip()
            qty_num = coerce_num(qty_s)
            unit = (item.get("unit") or "").strip() or None
            food = (item.get("food") or "").strip() or None
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
                filter(None, [qty_s, unit or "", food or ""])).strip()
            ingredients.append(
                {
                    "quantity": qty_num,
                    "unit": unit,
                    "food": food,
                    "note": note,
                    "display": display,
                    "title": None,
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
                instructions.append({"title": "", "summary": "", "text": text})

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
        headers = self._build_headers()
        
        base_payload = {
            "name": recipe_data.get("name") or recipe_data.get("title") or "Untitled",
            "description": recipe_data.get("description") or "",
        }
        create_url = f"{self.base_url}/api/recipes"
        self._log(f"Base create POST {create_url} payload={base_payload}")
        
        resp = self._session.post(create_url, json=base_payload, headers=headers)
        raw_text = resp.text
        self._log(f"Base create status {resp.status_code}")
        
        if resp.status_code >= 400:
            self._log(f"Create error body: {raw_text[:1000]}")
            resp.raise_for_status()

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
            self._log("Could not determine created recipe identifier; returning raw.")
            return {"raw": created}

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

        # Build update payload
        update_payload = self._build_update_payload(recipe_data)
        self._log(f"PATCH update ingredients={len(update_payload['recipeIngredient'])} instructions={len(update_payload['recipeInstructions'])}")

        # Try PATCH first
        patch_url = f"{self.base_url}/api/recipes/{ident}"
        patch_resp = self._session.patch(patch_url, json=update_payload, headers=headers)
        self._log(f"PATCH {patch_url} -> {patch_resp.status_code}")
        
        if patch_resp.status_code == 405:  # Method not allowed; try PUT
            put_resp = self._session.put(patch_url, json=update_payload, headers=headers)
            self._log(f"PUT {patch_url} -> {put_resp.status_code}")
            if put_resp.status_code >= 400:
                self._log(f"Update error body: {put_resp.text[:1000]}")
                put_resp.raise_for_status()
            try:
                return put_resp.json()
            except Exception:
                return {"id": ident, "update_raw": put_resp.text}
        elif patch_resp.status_code >= 400:
            self._log(f"Update error body: {patch_resp.text[:1000]}")
            patch_resp.raise_for_status()

        try:
            return patch_resp.json()
        except Exception:
            return {"id": ident, "update_raw": patch_resp.text}
