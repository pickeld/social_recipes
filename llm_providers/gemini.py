"""
Gemini LLM provider for image selection.
Uses Google's Gemini API with vision capabilities.
"""

from .base import LLMImageSelector
from config import config


class GeminiImageSelector(LLMImageSelector):
    """Image selector using Google Gemini's vision capabilities."""

    def select_best_frame(self, frame_paths: list[str]) -> int | None:
        """
        Select best frame using Gemini vision.
        
        Args:
            frame_paths: List of paths to candidate frame images.
        
        Returns:
            Index of the best frame, or None if selection fails.
        """
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Prepare image parts
        parts = []
        for i, path in enumerate(frame_paths):
            with open(path, "rb") as f:
                image_data = f.read()
            parts.append(types.Part.from_bytes(
                data=image_data,
                mime_type="image/jpeg"
            ))
            parts.append(types.Part.from_text(text=f"[Image {i}]"))

        prompt = self._get_selection_prompt(len(frame_paths))
        parts.append(types.Part.from_text(text=prompt))

        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[types.Content(role="user", parts=parts)],
            )
            return self._parse_selection_response(response.text or "", len(frame_paths))
        except Exception as e:
            print(f"[GeminiImageSelector] Selection error: {e}")
            return None
