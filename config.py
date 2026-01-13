"""
Configuration module for Social Recipes.
Reads configuration from SQLite database with defaults for first run.
"""

import os
import sqlite3
from contextlib import contextmanager

# Database file path - must match ui/database.py location
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'social_recipes.db')

# Default configuration values
DEFAULT_CONFIG = {
    "llm_provider": "openai",
    "openai_api_key": "",
    "openai_model": "gpt-5-mini-2025-08-07",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.0-flash",
    "recipe_lang": "hebrew",
    "mealie_api_key": "",
    "mealie_host": "",
    "tandoor_api_key": "",
    "tandoor_host": "",
    "target_language": "he",
    "output_target": "tandoor",
    "whisper_model": "small",
    "confirm_before_upload": "true",
    "hf_token": ""
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
    config = DEFAULT_CONFIG.copy()

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
        return self._get('llm_provider', DEFAULT_CONFIG['llm_provider'])

    @property
    def OPENAI_API_KEY(self) -> str:
        return self._get('openai_api_key', DEFAULT_CONFIG['openai_api_key'])

    @property
    def OPENAI_MODEL(self) -> str:
        return self._get('openai_model', DEFAULT_CONFIG['openai_model'])

    @property
    def GEMINI_API_KEY(self) -> str:
        return self._get('gemini_api_key', DEFAULT_CONFIG['gemini_api_key'])

    @property
    def GEMINI_MODEL(self) -> str:
        return self._get('gemini_model', DEFAULT_CONFIG['gemini_model'])

    @property
    def RECIPE_LANG(self) -> str:
        return self._get('recipe_lang', DEFAULT_CONFIG['recipe_lang'])

    @property
    def MEALIE_API_KEY(self) -> str:
        return self._get('mealie_api_key', DEFAULT_CONFIG['mealie_api_key'])

    @property
    def MEALIE_HOST(self) -> str:
        return self._get('mealie_host', DEFAULT_CONFIG['mealie_host'])

    @property
    def TANDOOR_API_KEY(self) -> str:
        return self._get('tandoor_api_key', DEFAULT_CONFIG['tandoor_api_key'])

    @property
    def TANDOOR_HOST(self) -> str:
        return self._get('tandoor_host', DEFAULT_CONFIG['tandoor_host'])

    @property
    def TARGET_LANGUAGE(self) -> str:
        return self._get('target_language', DEFAULT_CONFIG['target_language'])

    @property
    def OUTPUT_TARGET(self) -> str:
        return self._get('output_target', DEFAULT_CONFIG['output_target'])

    @property
    def WHISPER_MODEL(self) -> str:
        return self._get('whisper_model', DEFAULT_CONFIG['whisper_model'])

    @property
    def CONFIRM_BEFORE_UPLOAD(self) -> bool:
        value = self._get('confirm_before_upload', DEFAULT_CONFIG['confirm_before_upload'])
        return value.lower() in ('true', '1', 'yes', 'on')

    @property
    def HF_TOKEN(self) -> str:
        return self._get('hf_token', DEFAULT_CONFIG['hf_token'])

    def reload(self):
        """Reload configuration from database."""
        self._db_config = _get_config_from_db()


# Global config instance
config = Config()
