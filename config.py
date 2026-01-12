import os

from dotenv import load_dotenv
load_dotenv()


class Config:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    RECIPE_LANG: str = os.getenv("RECIPE_LANG", "hebrew")
    MEALIE_API_KEY: str = os.getenv("MEALIE_API_KEY", "")
    MEALIE_HOST: str = os.getenv("MEALIE_HOST", "http://192.168.127.251:9925")
    TANDOOR_API_KEY: str = os.getenv("TANDOOR_API_KEY", "")
    TANDOOR_HOST: str = os.getenv("TANDOOR_HOST", "https://tandoor.pickel.me")
    TARGET_LANGUAGE: str = os.getenv("TARGET_LANGUAGE", "he")
    OUTPUT_TARGET: str = os.getenv("OUTPUT_TARGET", "tandoor")

config = Config()
