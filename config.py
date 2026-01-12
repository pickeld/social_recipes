"""
Configuration module for Social Recipes.
Reads configuration from SQLite database with defaults for first run.
"""

import os
import sqlite3
from contextlib import contextmanager

# Database file path - same as UI database
DB_FILE = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), 'social_recipes.db')

# Default configuration values
DEFAULTS = {
    "llm_provider": "openai",
    "openai_api_key": "",
    "openai_model": "gpt-5-mini-2025-08-07",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "recipe_lang": "hebrew",
    "mealie_api_key": "",
    "mealie_host": "http://192.168.127.251:9925",
    "tandoor_api_key": "",
    "tandoor_host": "https://tandoor.pickel.me",
    "target_language": "he",
    "output_target": "tandoor",
    "whisper_model": "small",
    "confirm_before_upload": "true"
}


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _get_config_from_db() -> dict:
    """Load all configuration values from SQLite database."""
    config = DEFAULTS.copy()

    # Only try to read from DB if it exists
    if not os.path.exists(DB_FILE):
        return config

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM config')
            for row in cursor.fetchall():
                config[row['key']] = row['value']
    except sqlite3.OperationalError:
        # Table doesn't exist yet, use defaults
        pass

    return config


class Config:
    """Configuration class that reads from SQLite database.

    Uses default values if database is not available or value is not set.
    """

    def __init__(self):
        self._db_config = _get_config_from_db()

    def _get(self, key: str, default: str) -> str:
        """Get config value from database or default."""
        if key in self._db_config and self._db_config[key]:
            return self._db_config[key]
        return default

    @property
    def LLM_PROVIDER(self) -> str:
        return self._get('llm_provider', DEFAULTS['llm_provider'])

    @property
    def OPENAI_API_KEY(self) -> str:
        return self._get('openai_api_key', DEFAULTS['openai_api_key'])

    @property
    def OPENAI_MODEL(self) -> str:
        return self._get('openai_model', DEFAULTS['openai_model'])

    @property
    def GEMINI_API_KEY(self) -> str:
        return self._get('gemini_api_key', DEFAULTS['gemini_api_key'])

    @property
    def GEMINI_MODEL(self) -> str:
        return self._get('gemini_model', DEFAULTS['gemini_model'])

    @property
    def RECIPE_LANG(self) -> str:
        return self._get('recipe_lang', DEFAULTS['recipe_lang'])

    @property
    def MEALIE_API_KEY(self) -> str:
        return self._get('mealie_api_key', DEFAULTS['mealie_api_key'])

    @property
    def MEALIE_HOST(self) -> str:
        return self._get('mealie_host', DEFAULTS['mealie_host'])

    @property
    def TANDOOR_API_KEY(self) -> str:
        return self._get('tandoor_api_key', DEFAULTS['tandoor_api_key'])

    @property
    def TANDOOR_HOST(self) -> str:
        return self._get('tandoor_host', DEFAULTS['tandoor_host'])

    @property
    def TARGET_LANGUAGE(self) -> str:
        return self._get('target_language', DEFAULTS['target_language'])

    @property
    def OUTPUT_TARGET(self) -> str:
        return self._get('output_target', DEFAULTS['output_target'])

    @property
    def WHISPER_MODEL(self) -> str:
        return self._get('whisper_model', DEFAULTS['whisper_model'])

    @property
    def CONFIRM_BEFORE_UPLOAD(self) -> bool:
        value = self._get('confirm_before_upload', DEFAULTS['confirm_before_upload'])
        return value.lower() in ('true', '1', 'yes', 'on')

    def reload(self):
        """Reload configuration from database."""
        self._db_config = _get_config_from_db()


# Global config instance
config = Config()
