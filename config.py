"""
Configuration module for Social Recipes.
Reads configuration from SQLite database (with fallback to environment variables for backwards compatibility).
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
    "whisper_model": "small"
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

    Falls back to environment variables if database is not available,
    allowing backwards compatibility with .env files.
    """

    def __init__(self):
        self._db_config = _get_config_from_db()

    def _get(self, key: str, env_var: str, default: str) -> str:
        """Get config value with priority: env var > database > default."""
        # Environment variable takes priority (allows runtime override)
        env_value = os.getenv(env_var)
        if env_value is not None:
            return env_value
        # Then check database
        if key in self._db_config and self._db_config[key]:
            return self._db_config[key]
        # Finally use default
        return default

    @property
    def LLM_PROVIDER(self) -> str:
        return self._get('llm_provider', 'LLM_PROVIDER', DEFAULTS['llm_provider'])

    @property
    def OPENAI_API_KEY(self) -> str:
        return self._get('openai_api_key', 'OPENAI_API_KEY', DEFAULTS['openai_api_key'])

    @property
    def OPENAI_MODEL(self) -> str:
        return self._get('openai_model', 'OPENAI_MODEL', DEFAULTS['openai_model'])

    @property
    def GEMINI_API_KEY(self) -> str:
        return self._get('gemini_api_key', 'GEMINI_API_KEY', DEFAULTS['gemini_api_key'])

    @property
    def GEMINI_MODEL(self) -> str:
        return self._get('gemini_model', 'GEMINI_MODEL', DEFAULTS['gemini_model'])

    @property
    def RECIPE_LANG(self) -> str:
        return self._get('recipe_lang', 'RECIPE_LANG', DEFAULTS['recipe_lang'])

    @property
    def MEALIE_API_KEY(self) -> str:
        return self._get('mealie_api_key', 'MEALIE_API_KEY', DEFAULTS['mealie_api_key'])

    @property
    def MEALIE_HOST(self) -> str:
        return self._get('mealie_host', 'MEALIE_HOST', DEFAULTS['mealie_host'])

    @property
    def TANDOOR_API_KEY(self) -> str:
        return self._get('tandoor_api_key', 'TANDOOR_API_KEY', DEFAULTS['tandoor_api_key'])

    @property
    def TANDOOR_HOST(self) -> str:
        return self._get('tandoor_host', 'TANDOOR_HOST', DEFAULTS['tandoor_host'])

    @property
    def TARGET_LANGUAGE(self) -> str:
        return self._get('target_language', 'TARGET_LANGUAGE', DEFAULTS['target_language'])

    @property
    def OUTPUT_TARGET(self) -> str:
        return self._get('output_target', 'OUTPUT_TARGET', DEFAULTS['output_target'])

    @property
    def WHISPER_MODEL(self) -> str:
        return self._get('whisper_model', 'WHISPER_MODEL', DEFAULTS['whisper_model'])

    def reload(self):
        """Reload configuration from database."""
        self._db_config = _get_config_from_db()


# Global config instance
config = Config()
