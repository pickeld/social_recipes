"""
Configuration module for Pick-a-Recipe.
Reads configuration from SQLite database with defaults for first run.
"""

import os
import shutil
import sqlite3
from contextlib import contextmanager

# Database file path - must match ui/database.py location
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'pick-a-recipe.db')

# Legacy filenames from social_recipes / pick_a_recipe deployments on srv2
LEGACY_DB_FILES = ('pick_a_recipe.db', 'social_recipes.db')


def _db_activity_score(db_path: str) -> int:
    """Heuristic: prefer the DB with the most user data."""
    if not os.path.exists(db_path):
        return 0
    score = 0
    try:
        conn = sqlite3.connect(db_path)
        for table, weight in (
            ('recipe_history', 10),
            ('recipe_jobs', 1),
            ('pending_uploads', 5),
            ('config', 1),
        ):
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                score += count * weight
            except sqlite3.OperationalError:
                pass
        conn.close()
    except sqlite3.Error:
        return 0
    return score


def migrate_legacy_database() -> bool:
    """Copy the richest legacy SQLite file into the canonical pick-a-recipe.db.

    srv2 kept volume social_recipe_social-recipes across renames; the old stack
    wrote pick_a_recipe.db while the new app expects pick-a-recipe.db.
    """
    canonical_score = _db_activity_score(DB_FILE)
    best_legacy = None
    best_score = canonical_score

    for name in LEGACY_DB_FILES:
        path = os.path.join(DATA_DIR, name)
        score = _db_activity_score(path)
        if score > best_score:
            best_legacy = path
            best_score = score

    if not best_legacy:
        return False

    if os.path.exists(DB_FILE):
        backup = f'{DB_FILE}.pre-migration.bak'
        if not os.path.exists(backup):
            shutil.copy2(DB_FILE, backup)

    shutil.copy2(best_legacy, DB_FILE)
    return True


migrate_legacy_database()

# Default configuration values
DEFAULT_CONFIG = {
    "llm_provider": "openai",
    "openai_api_key": "",
    "openai_model": "gpt-5-mini-2025-08-07",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "recipe_lang": "hebrew",
    "mealie_api_key": "",
    "mealie_host": "",
    "tandoor_api_key": "",
    "tandoor_host": "",
    "target_language": "he",
    "mealie_enabled": "false",
    "tandoor_enabled": "true",
    "whisper_model": "small",
    "confirm_before_upload": "true",
    "hf_token": "",
    "usda_fdc_api_key": "",
    "yt_dlp_cookies_file": "",
    "yt_dlp_cookies_browser": "",
    "max_concurrent_jobs": "3",
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


def set_config_value(key: str, value: str) -> bool:
    """Persist a single config value to the SQLite store.

    Lives here (rather than only in ui/database.py) so non-UI entrypoints - the
    CLI and the LLM resilience layer - can write config without depending on the
    ui package being importable. Creates the config table on demand.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
            ''', (key, str(value), str(value)))
            conn.commit()
            return True
    except sqlite3.Error:
        return False


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
            rows = {row['key']: row['value'] for row in cursor.fetchall()}
            config.update(rows)
            _migrate_legacy_output_keys(config, rows)
    except sqlite3.OperationalError:
        # Table doesn't exist yet, use defaults
        pass

    return config


def _truthy(value: str) -> bool:
    return str(value).lower() in ('true', '1', 'yes', 'on')


def _migrate_legacy_output_keys(config: dict, db_rows: dict) -> None:
    """Derive per-target enabled flags for installs predating them.

    Old model: single `output_target` select plus an `export_to_both`
    checkbox. New model: independent `mealie_enabled` / `tandoor_enabled`
    flags. Fills only the flags missing from the database, so choices
    already saved by the new UI are never overwritten.
    """
    if 'mealie_enabled' in db_rows and 'tandoor_enabled' in db_rows:
        return

    legacy_both = _truthy(config.get('export_to_both', ''))
    legacy_target = (config.get('output_target') or 'tandoor').strip().lower()

    if 'mealie_enabled' not in db_rows:
        enable = legacy_both or legacy_target == 'mealie'
        config['mealie_enabled'] = 'true' if enable else 'false'

    if 'tandoor_enabled' not in db_rows:
        enable = legacy_both or legacy_target == 'tandoor'
        config['tandoor_enabled'] = 'true' if enable else 'false'


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
    def MEALIE_ENABLED(self) -> bool:
        return _truthy(self._get('mealie_enabled', DEFAULT_CONFIG['mealie_enabled']))

    @property
    def TANDOOR_ENABLED(self) -> bool:
        return _truthy(self._get('tandoor_enabled', DEFAULT_CONFIG['tandoor_enabled']))

    @property
    def WHISPER_MODEL(self) -> str:
        return self._get('whisper_model', DEFAULT_CONFIG['whisper_model'])

    @property
    def CONFIRM_BEFORE_UPLOAD(self) -> bool:
        return _truthy(self._get('confirm_before_upload', DEFAULT_CONFIG['confirm_before_upload']))

    @property
    def HF_TOKEN(self) -> str:
        return self._get('hf_token', DEFAULT_CONFIG['hf_token'])

    @property
    def USDA_FDC_API_KEY(self) -> str:
        """USDA FoodData Central key from Settings, else USDA_FDC_API_KEY env.

        Never hardcoded. Empty Settings falls through to the environment so
        Docker operators can keep using env-only configuration.
        """
        stored = self._get('usda_fdc_api_key', DEFAULT_CONFIG['usda_fdc_api_key'])
        if stored:
            return stored
        return (os.environ.get('USDA_FDC_API_KEY') or '').strip()

    @property
    def YT_DLP_COOKIES_FILE(self) -> str:
        return self._get('yt_dlp_cookies_file', DEFAULT_CONFIG['yt_dlp_cookies_file'])

    @property
    def YT_DLP_COOKIES_BROWSER(self) -> str:
        return self._get('yt_dlp_cookies_browser', DEFAULT_CONFIG['yt_dlp_cookies_browser'])

    @property
    def MAX_CONCURRENT_JOBS(self) -> int:
        raw = self._get('max_concurrent_jobs', DEFAULT_CONFIG['max_concurrent_jobs'])
        try:
            return max(1, min(16, int(raw)))
        except (TypeError, ValueError):
            return 3

    def reload(self):
        """Reload configuration from database."""
        self._db_config = _get_config_from_db()


# Global config instance
config = Config()
