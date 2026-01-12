"""
LLM Providers package for image selection functionality.
Provides a unified interface for different LLM vision providers.
"""

from .base import LLMImageSelector
from .gemini import GeminiImageSelector
from .openai import OpenAIImageSelector
from config import config


def get_image_selector() -> LLMImageSelector:
    """
    Factory function to get the appropriate image selector based on config.
    
    Returns:
        An instance of the configured LLM image selector.
    
    Raises:
        ValueError: If the configured provider is not supported.
    """
    if config.LLM_PROVIDER == "gemini":
        return GeminiImageSelector()
    elif config.LLM_PROVIDER == "openai":
        return OpenAIImageSelector()
    else:
        raise ValueError(f"Unsupported LLM provider: {config.LLM_PROVIDER}")


__all__ = [
    "LLMImageSelector",
    "GeminiImageSelector", 
    "OpenAIImageSelector",
    "get_image_selector",
]
