from config import config
import requests
import re


class Tandoor:
    """
    Export recipes to Tandoor Recipes.
    API documentation: https://docs.tandoor.dev/api/
    """

    def __init__(self):
        self.api_key = config.TANDOOR_API_KEY
        self.base_url = config.TANDOOR_HOST.rstrip("/")

    def _parse_iso_duration(self, duration: str) -> int:
        """
        Parse ISO 8601 duration (e.g., PT30M, PT1H30M) to minutes.
        Returns 0 if parsing fails.
        """
        if not duration:
            return 0
        # Match patterns like PT1H30M, PT45M, PT2H
        match = re.match(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration).upper()
        )
        if not match:
            # Try simple numeric (assume minutes)
            try:
                return int(duration)
            except (ValueError, TypeError):
                return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 60 + minutes + (1 if seconds >= 30 else 0)

    def _coerce_num(self, val: str) -> float:
        """Convert string quantity to float, handling ranges and locales."""
        if not val:
            return 0
        v = str(val).strip()
        # Handle range -> take first number
        if "-" in v:
            v = v.split("-")[0].strip()
        v = v.replace(",", ".")
        try:
            return float(v)
        except ValueError:
            return 0

    def _extract_servings(self, recipe_data: dict) -> int:
        """Extract numeric servings from recipeYield."""
        ry = recipe_data.get("recipeYield") or ""
        m = re.search(r"(\d+(?:[.,]\d+)?)", str(ry))
        if m:
            try:
                return int(float(m.group(1).replace(",", ".")))
            except ValueError:
                pass
        return 1

    def _get_or_create_unit(self, unit_name: str, headers: dict) -> int | None:
        """Get existing unit ID or create new one. Returns unit ID or None."""
        if not unit_name:
            return None

        # Search for existing unit
        search_url = f"{self.base_url}/api/unit/"
        try:
            resp = requests.get(
                search_url, headers=headers, params={"query": unit_name}
            )
            if resp.status_code == 200:
                units = resp.json()
                if isinstance(units, dict) and "results" in units:
                    units = units["results"]
                for u in units:
                    if isinstance(u, dict) and u.get("name", "").lower() == unit_name.lower():
                        return u.get("id")
        except Exception as e:
            print(f"[Tandoor] Unit search error: {e}")

        # Create new unit
        create_url = f"{self.base_url}/api/unit/"
        try:
            resp = requests.post(
                create_url, json={"name": unit_name}, headers=headers
            )
            if resp.status_code in (200, 201):
                created = resp.json()
                return created.get("id")
        except Exception as e:
            print(f"[Tandoor] Unit create error: {e}")

        return None

    def _get_or_create_food(self, food_name: str, headers: dict) -> int | None:
        """Get existing food ID or create new one. Returns food ID or None."""
        if not food_name:
            return None

        # Search for existing food
        search_url = f"{self.base_url}/api/food/"
        try:
            resp = requests.get(
                search_url, headers=headers, params={"query": food_name}
            )
            if resp.status_code == 200:
                foods = resp.json()
                if isinstance(foods, dict) and "results" in foods:
                    foods = foods["results"]
                for f in foods:
                    if isinstance(f, dict) and f.get("name", "").lower() == food_name.lower():
                        return f.get("id")
        except Exception as e:
            print(f"[Tandoor] Food search error: {e}")

        # Create new food
        create_url = f"{self.base_url}/api/food/"
        try:
            resp = requests.post(
                create_url, json={"name": food_name}, headers=headers
            )
            if resp.status_code in (200, 201):
                created = resp.json()
                return created.get("id")
        except Exception as e:
            print(f"[Tandoor] Food create error: {e}")

        return None

    def _build_ingredients(self, recipe_data: dict, headers: dict) -> list[dict]:
        """
        Build Tandoor ingredient list from recipe data.
        Tandoor expects: { amount, unit (id + name), food (id + name), note, order }
        """
        ingredients = []
        order = 0

        # Prefer structured ingredients
        ing_struct = recipe_data.get("recipeIngredientStructured") or []
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
                    modifiers = [m.strip() for m in modifiers.split(",") if m.strip()]
                notes = (item.get("notes") or "").strip()

                # Combine modifiers + notes
                note_parts = []
                if notes:
                    note_parts.append(notes)
                if modifiers:
                    note_parts.append(" ".join(modifiers))
                note = " | ".join([p for p in note_parts if p]) or None

                amount = self._coerce_num(qty_s)
                unit_id = self._get_or_create_unit(unit_name, headers) if unit_name else None
                food_id = self._get_or_create_food(food_name, headers) if food_name else None

                # Tandoor requires both id and name for unit/food objects
                unit_obj = {"id": unit_id, "name": unit_name} if unit_id and unit_name else None
                food_obj = {"id": food_id, "name": food_name} if food_id and food_name else None

                ingredient = {
                    "amount": amount,
                    "unit": unit_obj,
                    "food": food_obj,
                    "note": note or raw or "",
                    "order": order,
                }
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

                # Simple parse: number + unit + rest
                m2 = re.match(
                    r"^\s*(\d+(?:[.,]\d+)?)\s*([^\s]+)?\s+(.*)$", raw
                )
                amount = 0
                unit_id = None
                unit_name = ""
                food_id = None
                food_name = ""

                if m2:
                    amount = self._coerce_num(m2.group(1))
                    unit_name = (m2.group(2) or "").strip()
                    food_name = (m2.group(3) or "").strip()
                    if unit_name:
                        unit_id = self._get_or_create_unit(unit_name, headers)
                    if food_name:
                        food_id = self._get_or_create_food(food_name, headers)

                # Tandoor requires both id and name for unit/food objects
                unit_obj = {"id": unit_id, "name": unit_name} if unit_id and unit_name else None
                food_obj = {"id": food_id, "name": food_name} if food_id and food_name else None

                ingredient = {
                    "amount": amount,
                    "unit": unit_obj,
                    "food": food_obj,
                    "note": raw,
                    "order": order,
                }
                ingredients.append(ingredient)
                order += 1

        return ingredients

    def _build_steps(self, recipe_data: dict, headers: dict) -> list[dict]:
        """
        Build Tandoor step list from recipe instructions.
        Tandoor expects: { instruction, ingredients (list), order, time, name }
        """
        steps = []
        instructions_src = recipe_data.get("recipeInstructions", [])
        order = 0

        for step in instructions_src:
            text = ""
            if isinstance(step, dict):
                text = (step.get("text") or "").strip()
            elif isinstance(step, str):
                text = step.strip()

            if text:
                step_obj = {
                    "instruction": text,
                    "ingredients": [],
                    "order": order,
                    "time": 0,
                    "name": "",
                }
                steps.append(step_obj)
                order += 1

        return steps

    def _to_tandoor_payload(self, recipe_data: dict, headers: dict) -> dict:
        """
        Map Schema.org style recipe into Tandoor API expected fields.
        Tandoor recipe structure:
          - name
          - description
          - servings
          - servings_text
          - working_time (minutes)
          - waiting_time (minutes)
          - source_url
          - steps: list of step objects with ingredients
        """
        name = (
            recipe_data.get("name")
            or recipe_data.get("headline")
            or recipe_data.get("title")
            or "Untitled"
        )
        description = recipe_data.get("description", "") or ""
        servings = self._extract_servings(recipe_data)
        servings_text = str(recipe_data.get("recipeYield") or servings)

        # Parse times
        prep_time = self._parse_iso_duration(recipe_data.get("prepTime", ""))
        cook_time = self._parse_iso_duration(recipe_data.get("cookTime", ""))
        total_time = self._parse_iso_duration(recipe_data.get("totalTime", ""))

        # working_time = prep, waiting_time = cook (or derive from total)
        working_time = prep_time
        waiting_time = cook_time
        if total_time and not (working_time or waiting_time):
            working_time = total_time

        source_url = recipe_data.get("url") or recipe_data.get("source_url") or ""

        # Build ingredients and steps
        ingredients = self._build_ingredients(recipe_data, headers)
        steps = self._build_steps(recipe_data, headers)

        # Tandoor expects ingredients inside steps. 
        # If we have a single step or no steps, put all ingredients in first step
        if ingredients:
            if not steps:
                # Create a default step with all instructions combined
                combined_instructions = "\n".join(
                    s.get("instruction", "") for s in self._build_steps(recipe_data, headers)
                ) or description or "See ingredients."
                steps = [{
                    "instruction": combined_instructions,
                    "ingredients": ingredients,
                    "order": 0,
                    "time": working_time + waiting_time,
                    "name": "",
                }]
            else:
                # Attach all ingredients to the first step
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
            "steps": steps,
        }
        return payload

    def create_recipe(self, recipe_data: dict) -> dict:
        """
        Create a recipe in Tandoor.
        POST /api/recipe/
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = self._to_tandoor_payload(recipe_data, headers)
        create_url = f"{self.base_url}/api/recipe/"

        print(f"[Tandoor] Creating recipe: {payload.get('name')}")
        print(f"[Tandoor] POST {create_url}")

        resp = requests.post(create_url, json=payload, headers=headers)
        print(f"[Tandoor] Response status: {resp.status_code}")

        if resp.status_code >= 400:
            print(f"[Tandoor] Error response: {resp.text[:1000]}")
            resp.raise_for_status()

        try:
            result = resp.json()
            print(f"[Tandoor] Recipe created with ID: {result.get('id')}")
            return result
        except Exception as e:
            print(f"[Tandoor] JSON parse error: {e}")
            return {"raw": resp.text}

    def get_recipe(self, recipe_id: int) -> dict:
        """
        Get a recipe by ID.
        GET /api/recipe/{id}/
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/api/recipe/{recipe_id}/"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def update_recipe(self, recipe_id: int, recipe_data: dict) -> dict:
        """
        Update an existing recipe.
        PATCH /api/recipe/{id}/
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = self._to_tandoor_payload(recipe_data, headers)
        url = f"{self.base_url}/api/recipe/{recipe_id}/"

        print(f"[Tandoor] Updating recipe {recipe_id}")
        resp = requests.patch(url, json=payload, headers=headers)

        if resp.status_code >= 400:
            print(f"[Tandoor] Update error: {resp.text[:1000]}")
            resp.raise_for_status()

        return resp.json()

    def delete_recipe(self, recipe_id: int) -> bool:
        """
        Delete a recipe by ID.
        DELETE /api/recipe/{id}/
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/api/recipe/{recipe_id}/"
        resp = requests.delete(url, headers=headers)
        return resp.status_code in (200, 204)

    def list_recipes(self, query: str | None = None, limit: int = 50) -> list[dict]:
        """
        List recipes, optionally filtered by search query.
        GET /api/recipe/
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/api/recipe/"
        params: dict[str, str | int] = {"limit": limit}
        if query:
            params["query"] = query

        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()

        result = resp.json()
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result if isinstance(result, list) else []
