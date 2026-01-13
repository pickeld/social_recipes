"""
Database module for Social Recipes UI
Uses SQLite to store configuration and user data.
"""

import os
import sqlite3
import hashlib
from contextlib import contextmanager

# Import defaults from config module to avoid duplication
from config import DEFAULT_CONFIG

# Database file path
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'social_recipes.db')


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database with required tables."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create config table (key-value store)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        
        # Create default admin user if no users exist
        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            create_user('admin', 'admin123')
        
        # Initialize default config values if not exist
        cursor.execute('SELECT COUNT(*) FROM config')
        if cursor.fetchone()[0] == 0:
            for key, value in DEFAULT_CONFIG.items():
                set_config_value(key, value)


def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


# ===== User Functions =====

def create_user(username: str, password: str) -> bool:
    """Create a new user."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, hash_password(password))
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def verify_user(username: str, password: str) -> bool:
    """Verify user credentials."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT password_hash FROM users WHERE username = ?',
            (username,)
        )
        row = cursor.fetchone()
        if row and row['password_hash'] == hash_password(password):
            return True
        return False


def update_password(username: str, new_password: str) -> bool:
    """Update user password."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (hash_password(new_password), username)
        )
        conn.commit()
        return cursor.rowcount > 0


# ===== Config Functions =====

def set_config_value(key: str, value: str) -> bool:
    """Set a single config value."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
        ''', (key, str(value), str(value)))
        conn.commit()
        return True


def load_config() -> dict:
    """Load all configuration values."""
    config = DEFAULT_CONFIG.copy()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT key, value FROM config')
        for row in cursor.fetchall():
            config[row['key']] = row['value']
    return config


def save_config(config: dict) -> bool:
    """Save all configuration values."""
    with get_db() as conn:
        cursor = conn.cursor()
        for key, value in config.items():
            cursor.execute('''
                INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
            ''', (key, str(value), str(value)))
        conn.commit()
        return True


# Initialize database on module import
init_db()
