import os

from dotenv import load_dotenv
load_dotenv()


class Config:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07")
    RECIPE_LANG: str = os.getenv("RECIPE_LANG", "hebrew")
    MEALIE_API_KEY: str = os.getenv("MEALIE_API_KEY", "")
    MEALIE_HOST: str = os.getenv("MEALIE_HOST", "http://192.168.127.251:9925")


config = Config()
