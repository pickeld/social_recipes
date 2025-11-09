from config import config
import requests


class Mealie:
    def __init__(self):
        self.api_key = config.MEALIE_API_KEY
        self.base_url = config.MEALIE_HOST

    def create_recipe(self, recipe_data: dict) -> dict:

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        response = requests.post(
            f"{self.base_url}/recipes", json=recipe_data, headers=headers)
        response.raise_for_status()
        return response.json()
