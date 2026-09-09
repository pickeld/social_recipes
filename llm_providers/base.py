"""
Base class for LLM image selectors.
Provides a common interface for different LLM providers.
"""

from abc import ABC, abstractmethod
import re


class LLMImageSelector(ABC):
    """Abstract base class for LLM-based image selection."""

    @abstractmethod
    def select_best_frame(self, frame_paths: list[str]) -> int | None:
        """
        Use LLM vision to select the best frame showing the finished dish.
        
        Args:
            frame_paths: List of paths to candidate frame images.
        
        Returns:
            Index of the best frame, or None if selection fails.
        """
        pass

    def _get_selection_prompt(self, num_frames: int) -> str:
        """Get the prompt for frame selection."""
        return f"""You are analyzing {num_frames} frames from a cooking video to find the BEST image that describes and represents the dish being made.

Select the frame that best:
1. DESCRIBES THE DISH - Shows what the dish actually is (ingredients, style, cuisine type are visible/recognizable)
2. REPRESENTS THE FINAL RESULT - Shows the completed/finished dish, not preparation steps
3. IDENTIFIES THE FOOD - A viewer can clearly understand what dish this is just by looking at the image
4. Shows appetizing presentation with good lighting and the food as the main subject
5. Has clear focus and attractive plating where the dish's key characteristics are visible

PRIORITY: Choose the image that someone could look at and immediately understand "this is [dish name]". The image should capture the essence and identity of the dish.

Return JSON {{"index": N}} where N is the 0-based frame number (0-{num_frames - 1}).
If none show a finished dish, pick the frame that best describes what food is being made."""

    def _parse_selection_response(self, response: str, max_idx: int) -> int | None:
        """Parse LLM response to get frame index."""
        from recipe_schema import parse_frame_selection

        structured = parse_frame_selection(response, max_idx)
        if structured is not None:
            return structured
        match = re.search(r"\d+", response.strip())
        if match:
            idx = int(match.group())
            if 0 <= idx < max_idx:
                return idx
        return None
