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
        return f"""You are analyzing {num_frames} frames from a cooking video to find the BEST image of the finished dish.

Look for a frame that shows:
1. The COMPLETED/FINISHED dish (not preparation steps)
2. Appetizing presentation with good lighting
3. Clear, well-focused image
4. The food as the main subject (not the cook's face or hands)
5. Attractive plating or serving presentation

Respond with ONLY the number (0-{num_frames - 1}) of the best frame.
If none show a finished dish, pick the most appetizing food image.
Just respond with the single number, nothing else."""

    def _parse_selection_response(self, response: str, max_idx: int) -> int | None:
        """Parse LLM response to get frame index."""
        # Find first number in response
        match = re.search(r"\d+", response.strip())
        if match:
            idx = int(match.group())
            if 0 <= idx < max_idx:
                return idx
        return None
