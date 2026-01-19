"""
Database module for Social Recipes UI
Uses SQLite to store configuration, user data, jobs, and recipe history.
"""

import os
import json
import sqlite3
import hashlib
import uuid
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

# Import defaults from config module to avoid duplication
from config import DEFAULT_CONFIG

# Database file path - use /app/data for Docker persistence, fallback to local
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'social_recipes.db')


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
        
        # Create recipe_jobs table for tracking active analysis jobs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                current_stage TEXT,
                stage_message TEXT,
                video_title TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create recipe_history table for completed recipes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS recipe_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                url TEXT NOT NULL,
                video_title TEXT,
                recipe_name TEXT,
                recipe_data TEXT,
                thumbnail_path TEXT,
                thumbnail_data TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                output_target TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES recipe_jobs(id)
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


# ===== Job Functions =====

def create_job(url: str) -> str:
    """Create a new analysis job and return its ID."""
    job_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO recipe_jobs (id, url, status, progress, current_stage, stage_message)
            VALUES (?, ?, 'pending', 0, 'pending', 'Waiting to start...')
        ''', (job_id, url))
        conn.commit()
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_active_jobs() -> List[Dict[str, Any]]:
    """Get all active (non-completed, non-failed, non-cancelled) jobs."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM recipe_jobs
            WHERE status NOT IN ('completed', 'failed', 'cancelled')
            ORDER BY created_at DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


def get_all_jobs() -> List[Dict[str, Any]]:
    """Get all jobs."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_jobs ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]


def update_job_progress(job_id: str, status: str, progress: int,
                        current_stage: str, stage_message: str,
                        video_title: Optional[str] = None) -> bool:
    """Update job progress."""
    with get_db() as conn:
        cursor = conn.cursor()
        if video_title:
            cursor.execute('''
                UPDATE recipe_jobs
                SET status = ?, progress = ?, current_stage = ?, stage_message = ?,
                    video_title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, progress, current_stage, stage_message, video_title, job_id))
        else:
            cursor.execute('''
                UPDATE recipe_jobs
                SET status = ?, progress = ?, current_stage = ?, stage_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, progress, current_stage, stage_message, job_id))
        conn.commit()
        return cursor.rowcount > 0


def fail_job(job_id: str, error_message: str) -> bool:
    """Mark a job as failed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (error_message, job_id))
        conn.commit()
        return cursor.rowcount > 0


def cancel_job(job_id: str) -> bool:
    """Mark a job as cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def complete_job(job_id: str) -> bool:
    """Mark a job as completed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE recipe_jobs
            SET status = 'completed', progress = 100, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_job(job_id: str) -> bool:
    """Delete a job record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_jobs WHERE id = ?', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


# ===== History Functions =====

def create_history_entry(job_id: str, url: str, video_title: Optional[str],
                         recipe_name: Optional[str], recipe_data: Optional[Dict],
                         thumbnail_path: Optional[str], thumbnail_data: Optional[str],
                         status: str, error_message: Optional[str] = None,
                         output_target: Optional[str] = None) -> Optional[int]:
    """Create a history entry for a completed/failed recipe extraction."""
    with get_db() as conn:
        cursor = conn.cursor()
        recipe_json = json.dumps(recipe_data) if recipe_data else None
        cursor.execute('''
            INSERT INTO recipe_history
            (job_id, url, video_title, recipe_name, recipe_data, thumbnail_path,
             thumbnail_data, status, error_message, output_target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, url, video_title, recipe_name, recipe_json, thumbnail_path,
              thumbnail_data, status, error_message, output_target))
        conn.commit()
        return cursor.lastrowid


def get_history(limit: int = 50, offset: int = 0,
                status_filter: Optional[str] = None,
                search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get recipe history with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = 'SELECT * FROM recipe_history WHERE 1=1'
        params = []
        
        if status_filter:
            query += ' AND status = ?'
            params.append(status_filter)
        
        if search:
            query += ' AND (recipe_name LIKE ? OR video_title LIKE ? OR url LIKE ?)'
            search_pattern = f'%{search}%'
            params.extend([search_pattern, search_pattern, search_pattern])
        
        query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            # Parse recipe_data JSON if present
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            results.append(item)
        return results


def get_history_entry(history_id: int) -> Optional[Dict[str, Any]]:
    """Get a single history entry by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM recipe_history WHERE id = ?', (history_id,))
        row = cursor.fetchone()
        if row:
            item = dict(row)
            # Parse recipe_data JSON if present
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            return item
    return None


def get_history_count(status_filter: Optional[str] = None,
                      search: Optional[str] = None) -> int:
    """Get total count of history entries with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        query = 'SELECT COUNT(*) FROM recipe_history WHERE 1=1'
        params = []
        
        if status_filter:
            query += ' AND status = ?'
            params.append(status_filter)
        
        if search:
            query += ' AND (recipe_name LIKE ? OR video_title LIKE ? OR url LIKE ?)'
            search_pattern = f'%{search}%'
            params.extend([search_pattern, search_pattern, search_pattern])
        
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def delete_history_entry(history_id: int) -> bool:
    """Delete a history entry."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_history WHERE id = ?', (history_id,))
        conn.commit()
        return cursor.rowcount > 0


def cleanup_old_jobs(hours: int = 24) -> int:
    """Clean up jobs older than specified hours that are completed/failed/cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM recipe_jobs
            WHERE status IN ('completed', 'failed', 'cancelled')
            AND updated_at < datetime('now', ? || ' hours')
        ''', (f'-{hours}',))
        conn.commit()
        return cursor.rowcount


# Initialize database on module import
init_db()
