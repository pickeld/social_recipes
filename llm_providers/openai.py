"""
OpenAI LLM provider for image selection.
Uses OpenAI's API with vision capabilities.
"""

import base64
from .base import LLMImageSelector
from config import config
from helpers import setup_logger

logger = setup_logger(__name__)


class OpenAIImageSelector(LLMImageSelector):
    """Image selector using OpenAI's vision capabilities."""

    def select_best_frame(self, frame_paths: list[str]) -> int | None:
        """
        Select best frame using OpenAI vision.
        
        Args:
            frame_paths: List of paths to candidate frame images.
        
        Returns:
            Index of the best frame, or None if selection fails.
        """
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)

        # Encode frames as base64
        image_contents = []
        for i, frame_path in enumerate(frame_paths):
            with open(frame_path, "rb") as f:
                b64_image = base64.standard_b64encode(f.read()).decode("utf-8")
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}",
                    "detail": "low"  # Use low detail for faster selection
                }
            })
            image_contents.append({
                "type": "text",
                "text": f"[Image {i}]"
            })

        prompt = self._get_selection_prompt(len(frame_paths))
        image_contents.append({"type": "text", "text": prompt})

        try:
            response = client.responses.create(
                model=config.OPENAI_MODEL,
                input=[{"role": "user", "content": image_contents}]
            )
            return self._parse_selection_response(response.output_text or "", len(frame_paths))
        except Exception as e:
            logger.error(f"Selection error: {e}")
            return None
