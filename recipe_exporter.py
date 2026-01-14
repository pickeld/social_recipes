"""
Abstract base class for recipe exporters.

This module provides a common interface for exporting recipes to different
recipe management systems (e.g., Tandoor, Mealie).
"""

from abc import ABC, abstractmethod
import os

from helpers import create_http_session, setup_logger


class RecipeExporter(ABC):
    """Abstract base class for recipe exporters.
    
    Provides common functionality for API communication, logging,
    and image uploads. Subclasses must implement the create_recipe method.
    """

    def __init__(self, api_key: str, base_url: str, name: str):
        """Initialize the recipe exporter.
        
        Args:
            api_key: API key for authentication.
            base_url: Base URL of the recipe management API.
            name: Display name for logging (e.g., "Tandoor", "Mealie").
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._name = name
        self._session = create_http_session()

    @abstractmethod
    def create_recipe(self, recipe_data: dict) -> dict:
        """Create a recipe in the target system.
        
        Args:
            recipe_data: Recipe data in Schema.org JSON-LD format.
            
        Returns:
            Response from the API, typically containing the created recipe ID.
        """
        pass

    def upload_image(self, recipe_id: str | int, image_path: str) -> bool:
        """Upload an image for a recipe.
        
        This is a base implementation that can be overridden by subclasses
        for API-specific endpoints.
        
        Args:
            recipe_id: The ID or slug of the recipe.
            image_path: Path to the image file (JPEG/PNG).
            
        Returns:
            True if upload succeeded, False otherwise.
        """
        if not os.path.exists(image_path):
            self._log(f"Image file not found: {image_path}")
            return False

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        url = self._get_image_upload_url(recipe_id)

        # Determine content type from file extension
        ext = os.path.splitext(image_path)[1].lower()
        content_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

        self._log(f"Uploading image to recipe {recipe_id}")

        try:
            with open(image_path, "rb") as f:
                files = {"image": (os.path.basename(image_path), f, content_type)}
                resp = self._session.put(url, files=files, headers=headers, timeout=60)

            self._log(f"Image upload status: {resp.status_code}")

            if resp.status_code in (200, 201, 204):
                self._log("Image uploaded successfully")
                return True
            else:
                self._log(f"Image upload failed: {resp.text[:500]}")
                return False
        except Exception as e:
            self._log(f"Image upload error: {e}")
            return False

    @abstractmethod
    def _get_image_upload_url(self, recipe_id: str | int) -> str:
        """Get the URL for image upload endpoint.
        
        Args:
            recipe_id: The ID or slug of the recipe.
            
        Returns:
            Full URL for the image upload endpoint.
        """
        pass

    def _build_headers(self) -> dict:
        """Build common API headers for JSON requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _log(self, message: str) -> None:
        """Log a message with the exporter name prefix.
        
        Args:
            message: Message to log.
        """
        logger = setup_logger(self._name)
        logger.info(message)
