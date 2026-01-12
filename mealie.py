from config import config
import requests
import re
import os


class Mealie:
    def __init__(self):
        self.api_key = config.MEALIE_API_KEY
        self.base_url = config.MEALIE_HOST.rstrip("/")

    def _normalize_qty_num(self, qty_str: str) -> float:
        if not qty_str:
            return 0
        v = qty_str.strip()
        if "-" in v:
            v = v.split("-")[0].strip()
        v = v.replace(",", ".")
        try:
            return float(v)
        except ValueError:
            return 0

    def _build_update_payload(self, original_recipe_schema: dict) -> dict:
        """
        Build payload for PATCH/PUT to /api/recipes/{id|slug} using Mealie internal field names.
        """
        ry = original_recipe_schema.get("recipeYield") or ""
        ry_qty = 0
        m = re.search(r"(\d+(?:[.,]\d+)?)", str(ry))
        if m:
            try:
                ry_qty = int(float(m.group(1).replace(",", ".")))
            except ValueError:
                ry_qty = 0

        # Ingredients
        ing_struct = original_recipe_schema.get(
            "recipeIngredientStructured") or []
        ingredients = []
        for item in ing_struct:
            if not isinstance(item, dict):
                continue
            raw = (item.get("raw") or item.get("normalized") or "").strip()
            qty_s = (item.get("quantity") or "").strip()
            qty_num = self._normalize_qty_num(qty_s)
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
        return update_payload

    def create_recipe(self, recipe_data: dict) -> dict:
        """
        Two-phase creation:
        1. Create base recipe via POST /api/recipes (minimal payload).
        2. If server returns primitive (slug/id) or placeholder ingredient list, PATCH with full structured data.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        base_payload = {
            "name": recipe_data.get("name") or recipe_data.get("title") or "Untitled",
            "description": recipe_data.get("description") or "",
        }
        create_url = f"{self.base_url}/api/recipes"
        print(f"[Mealie] Base create POST {create_url} payload={base_payload}")
        resp = requests.post(create_url, json=base_payload, headers=headers)
        raw_text = resp.text
        print(f"[Mealie] Base create status {resp.status_code}")
        if resp.status_code >= 400:
            print(f"[Mealie] Create error body: {raw_text[:1000]}")
            resp.raise_for_status()

        # Parse primitive or object
        created = None
        try:
            created = resp.json()
        except Exception as e:
            print(
                f"[Mealie] Create JSON parse failed: {e}; raw={raw_text[:300]}")
            created = raw_text

        ident = None
        if isinstance(created, dict):
            ident = created.get("id") or created.get("slug")
        elif isinstance(created, (str, int)):
            ident = str(created).strip().strip('"')
        if not ident:
            print(
                "[Mealie] Could not determine created recipe identifier; returning raw.")
            return {"raw": created}

        # Fetch current state
        detail_url_candidates = [
            f"{self.base_url}/api/recipes/{ident}",
            f"{self.base_url}/api/recipes/slug/{ident}",
        ]
        current = None
        for du in detail_url_candidates:
            try:
                d_resp = requests.get(du, headers=headers)
                print(f"[Mealie] GET {du} -> {d_resp.status_code}")
                if d_resp.status_code == 200:
                    try:
                        current = d_resp.json()
                        # prefer id if available
                        ident = current.get("id") or ident
                        break
                    except Exception as e2:
                        print(f"[Mealie] Detail parse failed: {e2}")
            except Exception as e3:
                print(f"[Mealie] Detail fetch exception: {e3}")

        if not isinstance(current, dict):
            print(
                "[Mealie] Unable to fetch current dict; returning primitive response.")
            return {"id_or_slug": ident, "raw": created}

        ingr_list = current.get("recipeIngredient") or []
        placeholder = (
            isinstance(ingr_list, list)
            and len(ingr_list) == 1
            and isinstance(ingr_list[0], dict)
            and (ingr_list[0].get("note") == "1 Cup Flour" or ingr_list[0].get("display") == "1 Cup Flour")
        )
        if not placeholder and ingr_list:
            print(
                f"[Mealie] Server already populated ingredients ({len(ingr_list)}). Skipping update.")
            return current

        # Build update payload
        update_payload = self._build_update_payload(recipe_data)
        print(
            f"[Mealie] PATCH update ingredients={len(update_payload['recipeIngredient'])} instructions={len(update_payload['recipeInstructions'])}")

        # Try PATCH first
        patch_url = f"{self.base_url}/api/recipes/{ident}"
        patch_resp = requests.patch(
            patch_url, json=update_payload, headers=headers)
        print(f"[Mealie] PATCH {patch_url} -> {patch_resp.status_code}")
        if patch_resp.status_code == 405:  # Method not allowed; try PUT
            put_resp = requests.put(
                patch_url, json=update_payload, headers=headers)
            print(f"[Mealie] PUT {patch_url} -> {put_resp.status_code}")
            if put_resp.status_code >= 400:
                print(f"[Mealie] Update error body: {put_resp.text[:1000]}")
                put_resp.raise_for_status()
            try:
                return put_resp.json()
            except Exception:
                return {"id": ident, "update_raw": put_resp.text}
        elif patch_resp.status_code >= 400:
            print(f"[Mealie] Update error body: {patch_resp.text[:1000]}")
            patch_resp.raise_for_status()

        try:
            return patch_resp.json()
        except Exception:
            return {"id": ident, "update_raw": patch_resp.text}

    def upload_image(self, recipe_slug: str, image_path: str) -> bool:
        """
        Upload an image for a recipe.
        PUT /api/recipes/{slug}/image
        
        Args:
            recipe_slug: The slug or ID of the recipe.
            image_path: Path to the image file (JPEG/PNG).
        
        Returns:
            True if upload succeeded, False otherwise.
        """
        if not os.path.exists(image_path):
            print(f"[Mealie] Image file not found: {image_path}")
            return False

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        url = f"{self.base_url}/api/recipes/{recipe_slug}/image"
        
        # Determine content type from file extension
        ext = os.path.splitext(image_path)[1].lower()
        content_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

        print(f"[Mealie] Uploading image to recipe {recipe_slug}")
        
        try:
            with open(image_path, "rb") as f:
                files = {"image": (os.path.basename(image_path), f, content_type)}
                resp = requests.put(url, files=files, headers=headers)
            
            print(f"[Mealie] Image upload status: {resp.status_code}")
            
            if resp.status_code in (200, 201, 204):
                print(f"[Mealie] Image uploaded successfully")
                return True
            else:
                print(f"[Mealie] Image upload failed: {resp.text[:500]}")
                return False
        except Exception as e:
            print(f"[Mealie] Image upload error: {e}")
            return False
