"""
Database module for Pick-a-Recipe UI
Uses SQLite to store configuration, user data, jobs, and recipe history.
"""

import os
import json
import sqlite3
import uuid
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

# Import defaults from config module to avoid duplication
from config import DEFAULT_CONFIG

# Database file path - use /app/data for Docker persistence, fallback to local
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
from config import DB_FILE


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

        # WAL improves concurrent read/write behavior for the worker pool,
        # heartbeat and sweeper threads sharing this file.
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=5000')
        
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                oidc_sub TEXT UNIQUE,
                email TEXT,
                name TEXT,
                avatar_url TEXT,
                is_admin INTEGER DEFAULT 0,
                password_hash TEXT,
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
        
        # Create pending_uploads table for recipe confirmations
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_uploads (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                recipe_data TEXT NOT NULL,
                image_path TEXT,
                image_candidates TEXT,
                output_target TEXT,
                selected_image_index INTEGER DEFAULT 0,
                best_image_index INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES recipe_jobs(id)
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

        _migrate_schema(conn)

        # Initialize default config values if not exist
        cursor.execute('SELECT COUNT(*) FROM config')
        if cursor.fetchone()[0] == 0:
            for key, value in DEFAULT_CONFIG.items():
                set_config_value(key, value)

        # Migrate retired Gemini model ids in existing databases.
        # gemini-2.0-flash(-lite) were removed from the API and return 404,
        # so rewrite them to the current default.
        cursor.execute(
            "UPDATE config SET value = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'gemini_model' AND value IN ('gemini-2.0-flash', 'gemini-2.0-flash-lite')",
            (DEFAULT_CONFIG['gemini_model'],)
        )
        conn.commit()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    cursor = conn.cursor()

    # Local password accounts live alongside OIDC ones, so a user row may carry
    # a password_hash, an oidc_sub, or both. Nullable: OIDC users never get one.
    #
    # A previous migration dropped the whole users table on sight of this
    # column, from when local accounts were being retired in favour of
    # OIDC-only auth. It must not come back: _migrate_schema runs on every
    # boot, so it would wipe accounts on restart and orphan the user_id
    # ownership recorded against every job and upload.
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
    except sqlite3.OperationalError:
        pass

    job_columns = {
        'retry_from_history_id': 'INTEGER',
        'llm_tokens_used': 'INTEGER DEFAULT 0',
        'queue_priority': 'INTEGER DEFAULT 0',
        'user_id': 'TEXT',
        'attempts': 'INTEGER DEFAULT 0',
        'next_run_at': 'TIMESTAMP',
        'lease_expires_at': 'TIMESTAMP',
        'state_changed_at': 'TIMESTAMP',
    }
    for col, typedef in job_columns.items():
        try:
            cursor.execute(f'ALTER TABLE recipe_jobs ADD COLUMN {col} {typedef}')
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute('ALTER TABLE pending_uploads ADD COLUMN user_id TEXT')
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_auth_nonces (
            nonce TEXT PRIMARY KEY,
            redirect_uri TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP
        )
    ''')
    # Nonces are marked used rather than deleted so a replayed state can be
    # told apart from one that was never issued.
    try:
        cursor.execute('ALTER TABLE mobile_auth_nonces ADD COLUMN used_at TIMESTAMP')
    except sqlite3.OperationalError:
        pass

    # Legacy cleanup: retries used to accumulate one failed row per attempt.
    # Keep the newest failure per URL, then drop failures a success
    # already superseded.
    cursor.execute('''
        DELETE FROM recipe_history WHERE status = 'failed' AND id NOT IN (
            SELECT MAX(id) FROM recipe_history WHERE status = 'failed'
            GROUP BY url
        )
    ''')
    cursor.execute('''
        DELETE FROM recipe_history WHERE status = 'failed' AND url IN (
            SELECT url FROM recipe_history WHERE status = 'success'
        )
    ''')

    conn.commit()


# ===== User Functions =====

def upsert_oidc_user(
    sub: str,
    username: str,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    is_admin: bool = False,
) -> Dict[str, Any]:
    """Create or refresh the local cache record for an OIDC (Authentik) user.

    Users are identified by their stable OIDC subject (`sub`). On first login a
    unique username is derived from preferred_username/email; if taken by a
    different subject, a numeric suffix is appended. Admin flag mirrors group
    membership at login time.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE oidc_sub = ?', (sub,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                '''UPDATE users
                   SET email = ?, name = ?, avatar_url = ?, is_admin = ?
                   WHERE id = ?''',
                (email, name, avatar_url, int(is_admin), row['id']),
            )
            conn.commit()
            return get_user(row['username'])

        base = (username or sub)[:64]
        final_username = base
        n = 1
        while cursor.execute(
            'SELECT 1 FROM users WHERE username = ?', (final_username,)
        ).fetchone():
            final_username = f'{base}-{n}'
            n += 1

        cursor.execute(
            '''INSERT INTO users (username, oidc_sub, email, name, avatar_url, is_admin)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (final_username, sub, email, name, avatar_url, int(is_admin)),
        )
        conn.commit()
        return get_user(final_username)


def ensure_local_user(username: str, *, is_admin: bool = True) -> Dict[str, Any]:
    """Create or return a local account, without setting a password.

    Carries no OIDC subject, so enabling Authentik later cannot collide with it:
    `upsert_oidc_user` only ever matches rows by `oidc_sub`. An account left
    without a password cannot be signed into; `set_user_password` completes it.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if row:
            if bool(row['is_admin']) != is_admin:
                cursor.execute(
                    'UPDATE users SET is_admin = ? WHERE id = ?',
                    (int(is_admin), row['id']),
                )
                conn.commit()
            return get_user(username)

        cursor.execute(
            '''INSERT INTO users (username, oidc_sub, email, name, avatar_url, is_admin)
               VALUES (?, NULL, NULL, ?, NULL, ?)''',
            (username, username, int(is_admin)),
        )
        conn.commit()
        return get_user(username)


def local_account_exists() -> bool:
    """True once any account has a password set.

    Drives first-run setup: while this is False in local mode, the instance has
    no way for anyone to sign in, so the setup page is open. Once it is True the
    setup page closes permanently.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM users WHERE password_hash IS NOT NULL "
            "AND password_hash != '' LIMIT 1"
        )
        return cursor.fetchone() is not None


def sole_passwordless_username() -> Optional[str]:
    """Username of the only account, when it has no password yet.

    Identifies the implicit account that AUTH_MODE=none used to seed, so setup
    can offer its name. Job and upload ownership is recorded as the username
    string rather than the row id, so keeping that name is what keeps the
    history attached — renaming the row would orphan it. Returns None as soon
    as there is more than one account, since then there is nothing obvious to
    adopt.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username, password_hash FROM users LIMIT 2')
        rows = cursor.fetchall()
        if len(rows) != 1 or rows[0]['password_hash']:
            return None
        return rows[0]['username']


def claim_first_local_admin(username: str, password_hash: str) -> Optional[Dict[str, Any]]:
    """Create the first password account, or adopt a passwordless one.

    Returns None if another request got there first. The guard and the write
    share one transaction and the check is re-run inside it, so two concurrent
    setup submissions cannot both succeed — whoever loses gets None rather than
    silently overwriting the winner's password.

    Adoption matters for instances upgrading from AUTH_MODE=none: that mode
    seeded a passwordless row, and every job and upload records ownership
    against its username. Reusing the row keeps that history attached to the
    account instead of orphaning it under a new name.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute(
            "SELECT 1 FROM users WHERE password_hash IS NOT NULL "
            "AND password_hash != '' LIMIT 1"
        )
        if cursor.fetchone() is not None:
            conn.rollback()
            return None

        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                'UPDATE users SET password_hash = ?, is_admin = 1 WHERE id = ?',
                (password_hash, row['id']),
            )
        else:
            cursor.execute(
                '''INSERT INTO users (username, oidc_sub, email, name, avatar_url,
                                      is_admin, password_hash)
                   VALUES (?, NULL, NULL, ?, NULL, 1, ?)''',
                (username, username, password_hash),
            )
        conn.commit()
        return get_user(username)


def set_user_password(username: str, password_hash: str) -> bool:
    """Store a new password hash for an existing account."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (password_hash, username),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Get user record by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_sub(sub: str) -> Optional[Dict[str, Any]]:
    """Get user record by OIDC subject claim."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE oidc_sub = ?', (sub,))
        row = cursor.fetchone()
        return dict(row) if row else None


# ===== User Administration =====

def list_users() -> List[Dict[str, Any]]:
    """Every account, oldest first, without the password hashes.

    Deliberately does not select password_hash: nothing outside authentication
    needs it, and leaving it out means it cannot be leaked by a caller that
    forgets to strip it.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, email, name, avatar_url, is_admin, created_at,
                   oidc_sub IS NOT NULL AS is_oidc,
                   (password_hash IS NOT NULL AND password_hash != '') AS has_password
            FROM users
            ORDER BY created_at, id
        ''')
        return [dict(row) for row in cursor.fetchall()]


def count_admins() -> int:
    """How many accounts can administer the instance."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1')
        return cursor.fetchone()[0]


def create_local_user(username: str, password_hash: str, *,
                      is_admin: bool = False) -> Optional[Dict[str, Any]]:
    """Add a password account. None if the username is already taken.

    Relies on the UNIQUE constraint rather than a check-then-insert, so two
    concurrent creations cannot both succeed.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''INSERT INTO users (username, oidc_sub, email, name, avatar_url,
                                      is_admin, password_hash)
                   VALUES (?, NULL, NULL, ?, NULL, ?, ?)''',
                (username, username, int(is_admin), password_hash),
            )
        except sqlite3.IntegrityError:
            return None
        conn.commit()
        return get_user(username)


def set_user_admin(username: str, is_admin: bool) -> bool:
    """Grant or revoke admin rights. False if there is no such account."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET is_admin = ? WHERE username = ?',
            (int(is_admin), username),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_user(username: str) -> bool:
    """Remove an account and the work it owns. False if there is no such account.

    Ownership is recorded as the username string, not the row id, so leaving the
    rows behind would hand them to the next account created with the same name.
    Queued jobs and pending uploads go with the account; entries in
    recipe_history do not, because that table has no owner column — extracted
    recipes belong to the instance and outlive whoever queued them.

    One transaction, so a failure part-way cannot leave an account deleted with
    its jobs still addressable, or vice versa.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('SELECT 1 FROM users WHERE username = ?', (username,))
        if cursor.fetchone() is None:
            conn.rollback()
            return False

        # Also by job, not just by user_id: an upload created before ownership
        # was recorded carries a NULL user_id, and leaving it would point at a
        # job that is about to disappear.
        cursor.execute(
            '''DELETE FROM pending_uploads
               WHERE user_id = ?
                  OR job_id IN (SELECT id FROM recipe_jobs WHERE user_id = ?)''',
            (username, username),
        )
        cursor.execute('DELETE FROM recipe_jobs WHERE user_id = ?', (username,))
        cursor.execute('DELETE FROM push_subscriptions WHERE username = ?', (username,))
        cursor.execute('DELETE FROM users WHERE username = ?', (username,))
        conn.commit()
        return True


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

def _owner_filter(user_id: Optional[str], is_admin: bool,
                  column: str = 'user_id') -> tuple[str, list]:
    """Return (sql_fragment, params) restricting rows to an owner.

    Unscoped when user_id is None and is_admin is False (legacy behavior).
    Jobs with NULL user_id remain visible to their querying scope so legacy
    rows are never orphaned; pending_uploads uses strict equality.
    """
    if is_admin or not user_id:
        return '', []
    return f' AND ({column} IS NULL OR {column} = ?)', [user_id]


def create_job(url: str, *, retry_from_history_id: int | None = None,
               priority: int = 0, user_id: Optional[str] = None) -> str:
    """Create a new analysis job and return its ID."""
    job_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO recipe_jobs
            (id, url, status, progress, current_stage, stage_message,
             retry_from_history_id, queue_priority, user_id)
            VALUES (?, ?, 'queued', 0, 'queued', 'Waiting in queue...', ?, ?, ?)
        ''', (job_id, url, retry_from_history_id, priority, user_id))
        conn.commit()
    return job_id


def get_queue_position(job_id: str) -> int:
    """Return 1-based queue position (0 if processing or not queued)."""
    job = get_job(job_id)
    if not job or job.get('status') != 'queued':
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM recipe_jobs q
            WHERE q.status = 'queued'
            AND (
                q.queue_priority > ?
                OR (
                    q.queue_priority = ?
                    AND (q.created_at, q.rowid) < (
                        SELECT created_at, rowid FROM recipe_jobs WHERE id = ?
                    )
                )
            )
        ''', (job.get('queue_priority', 0), job.get('queue_priority', 0), job_id))
        ahead = cursor.fetchone()[0]
        return ahead + 1


def count_queued_jobs() -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM recipe_jobs WHERE status = 'queued'")
        return cursor.fetchone()[0]


def get_queued_jobs(*, user_id: Optional[str] = None,
                    is_admin: bool = False) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        owner_sql, owner_params = _owner_filter(user_id, is_admin)
        cursor.execute(f'''
            SELECT * FROM recipe_jobs WHERE status = 'queued'{owner_sql}
            ORDER BY queue_priority DESC, created_at ASC, rowid ASC
        ''', owner_params)
        return [dict(row) for row in cursor.fetchall()]


def update_job_tokens(job_id: str, tokens: int) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE recipe_jobs SET llm_tokens_used = ? WHERE id = ?',
            (tokens, job_id),
        )
        conn.commit()


def get_job(job_id: str, *, user_id: Optional[str] = None,
            is_admin: bool = False) -> Optional[Dict[str, Any]]:
    """Get a job by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        owner_sql, owner_params = _owner_filter(user_id, is_admin)
        cursor.execute(
            f'SELECT * FROM recipe_jobs WHERE id = ?{owner_sql}',
            [job_id] + owner_params,
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_active_jobs(*, user_id: Optional[str] = None,
                    is_admin: bool = False) -> List[Dict[str, Any]]:
    """Get all active (non-completed, non-failed, non-cancelled) jobs."""
    with get_db() as conn:
        cursor = conn.cursor()
        owner_sql, owner_params = _owner_filter(user_id, is_admin)
        cursor.execute(f'''
            SELECT * FROM recipe_jobs
            WHERE status NOT IN ('completed', 'failed', 'cancelled'){owner_sql}
            ORDER BY
                CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
                queue_priority DESC,
                created_at ASC,
                rowid ASC
        ''', owner_params)
        jobs = [dict(row) for row in cursor.fetchall()]
        for job in jobs:
            if job.get('status') == 'queued':
                job['queue_position'] = get_queue_position(job['id'])
        return jobs


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
    """Create a history entry for a completed/failed recipe extraction.

    Maintains a per-URL cleanliness invariant at write time:
    - a success entry removes all older failed entries for the same URL
      (a retry that landed supersedes the attempts that didn't);
    - a failed entry replaces any previous failure for the same URL
      (only the latest error is worth keeping).

    Read paths can therefore trust there is at most one relevant row per
    outcome per URL — no EXISTS-based hiding needed.
    """
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
        new_id = cursor.lastrowid

        if status in ('success', 'failed'):
            cursor.execute(
                "DELETE FROM recipe_history "
                "WHERE url = ? AND status = 'failed' AND id != ?",
                (url, new_id),
            )
        conn.commit()
        return new_id


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


def delete_history_entries_bulk(history_ids: List[int]) -> int:
    """Delete multiple history entries. Returns count of deleted entries."""
    if not history_ids:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in history_ids])
        cursor.execute(f'DELETE FROM recipe_history WHERE id IN ({placeholders})', history_ids)
        conn.commit()
        return cursor.rowcount


def get_combined_history_and_jobs(limit: int = 50, offset: int = 0,
                                   status_filter: Optional[str] = None,
                                   search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get combined view of recipe history and active/cancelled jobs.
    
    This provides a unified view showing:
    - Completed/failed recipes from recipe_history
    - In-progress jobs from recipe_jobs
    - Cancelled jobs from recipe_jobs
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Build query for recipe_history
        history_query = '''
            SELECT
                'history' as source_type,
                rh.id,
                rh.job_id,
                rh.url,
                rh.video_title,
                rh.recipe_name,
                rh.recipe_data,
                rh.thumbnail_path,
                rh.thumbnail_data,
                rh.status,
                rh.error_message,
                rh.output_target,
                rh.created_at,
                NULL as progress,
                NULL as current_stage,
                NULL as stage_message,
                rh.created_at as updated_at
            FROM recipe_history rh
        '''
        history_params = []

        # Build query for recipe_jobs (only active jobs that don't have history entries)
        # Exclude completed/failed jobs as they should have history entries
        jobs_query = '''
            SELECT
                'job' as source_type,
                NULL as id,
                rj.id as job_id,
                rj.url,
                rj.video_title,
                NULL as recipe_name,
                NULL as recipe_data,
                NULL as thumbnail_path,
                NULL as thumbnail_data,
                rj.status,
                rj.error_message,
                NULL as output_target,
                rj.created_at,
                rj.progress,
                rj.current_stage,
                rj.stage_message,
                rj.updated_at
            FROM recipe_jobs rj
            LEFT JOIN recipe_history rh ON rj.id = rh.job_id
            WHERE rh.id IS NULL AND rj.status NOT IN ('completed', 'failed')
        '''
        jobs_params = []
        
        # Apply status filter
        if status_filter:
            if status_filter == 'success':
                history_query += ' AND rh.status = ?'
                history_params.append('success')
                jobs_query += ' AND 1=0'  # No jobs can be "success" without history
            elif status_filter == 'failed':
                history_query += ' AND rh.status = ?'
                history_params.append('failed')
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('failed')
            elif status_filter == 'cancelled':
                history_query += ' AND 1=0'  # No history entries for cancelled
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('cancelled')
            elif status_filter == 'pending':
                history_query += ' AND 1=0'
                jobs_query += ' AND rj.status IN (?, ?)'
                jobs_params.extend(['pending', 'queued'])
            elif status_filter == 'processing':
                history_query += ' AND 1=0'  # No history entries for processing
                jobs_query += ' AND rj.status NOT IN (?, ?, ?, ?)'
                jobs_params.extend(['completed', 'failed', 'cancelled', 'pending'])
        
        # Apply search filter
        if search:
            search_pattern = f'%{search}%'
            history_query += ' AND (rh.recipe_name LIKE ? OR rh.video_title LIKE ? OR rh.url LIKE ?)'
            history_params.extend([search_pattern, search_pattern, search_pattern])
            jobs_query += ' AND (rj.video_title LIKE ? OR rj.url LIKE ?)'
            jobs_params.extend([search_pattern, search_pattern])
        
        # Combine queries with UNION ALL
        combined_query = f'''
            SELECT * FROM (
                {history_query}
                UNION ALL
                {jobs_query}
            ) combined
            ORDER BY
                CASE
                    WHEN status IN ('pending', 'processing', 'info', 'download', 'transcribe',
                                    'visual', 'image', 'evaluate', 'preview', 'upload') THEN 0
                    WHEN status = 'cancelled' THEN 1
                    ELSE 2
                END,
                updated_at DESC
            LIMIT ? OFFSET ?
        '''
        
        all_params = history_params + jobs_params + [limit, offset]
        cursor.execute(combined_query, all_params)
        
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


def get_combined_history_and_jobs_count(status_filter: Optional[str] = None,
                                         search: Optional[str] = None) -> int:
    """Get total count of combined history and jobs with optional filtering."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Count from recipe_history
        # Exclude failed entries if there's a successful entry for the same URL
        history_query = "SELECT COUNT(*) FROM recipe_history rh WHERE 1=1"
        history_params = []
        
        # Count from recipe_jobs (only active jobs that don't have history entries)
        # Exclude completed/failed jobs as they should have history entries
        jobs_query = '''
            SELECT COUNT(*) FROM recipe_jobs rj
            LEFT JOIN recipe_history rh ON rj.id = rh.job_id
            WHERE rh.id IS NULL AND rj.status NOT IN ('completed', 'failed')
        '''
        jobs_params = []
        
        # Apply status filter
        if status_filter:
            if status_filter == 'success':
                history_query += ' AND rh.status = ?'
                history_params.append('success')
                jobs_query = 'SELECT 0'  # No jobs can be "success" without history
                jobs_params = []
            elif status_filter == 'failed':
                history_query += ' AND rh.status = ?'
                history_params.append('failed')
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('failed')
            elif status_filter == 'cancelled':
                history_query = 'SELECT 0'  # No history entries for cancelled
                history_params = []
                jobs_query += ' AND rj.status = ?'
                jobs_params.append('cancelled')
            elif status_filter == 'pending':
                history_query = 'SELECT 0'
                history_params = []
                jobs_query += ' AND rj.status IN (?, ?)'
                jobs_params.extend(['pending', 'queued'])
            elif status_filter == 'processing':
                history_query = 'SELECT 0'  # No history entries for processing
                history_params = []
                jobs_query += ' AND rj.status NOT IN (?, ?, ?, ?)'
                jobs_params.extend(['completed', 'failed', 'cancelled', 'pending'])
        
        # Apply search filter
        if search:
            search_pattern = f'%{search}%'
            if 'SELECT 0' not in history_query:
                history_query += ' AND (rh.recipe_name LIKE ? OR rh.video_title LIKE ? OR rh.url LIKE ?)'
                history_params.extend([search_pattern, search_pattern, search_pattern])
            if 'SELECT 0' not in jobs_query:
                jobs_query += ' AND (rj.video_title LIKE ? OR rj.url LIKE ?)'
                jobs_params.extend([search_pattern, search_pattern])
        
        # Execute history count
        cursor.execute(history_query, history_params)
        history_count = cursor.fetchone()[0]
        
        # Execute jobs count
        cursor.execute(jobs_query, jobs_params)
        jobs_count = cursor.fetchone()[0]
        
        return history_count + jobs_count


def delete_job_entry(job_id: str) -> bool:
    """Delete a job entry."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM recipe_jobs WHERE id = ?', (job_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_jobs_bulk(job_ids: List[str]) -> int:
    """Delete multiple job entries. Returns count of deleted entries."""
    if not job_ids:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in job_ids])
        cursor.execute(f'DELETE FROM recipe_jobs WHERE id IN ({placeholders})', job_ids)
        conn.commit()
        return cursor.rowcount


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


def find_stranded_approvals() -> List[str]:
    """Awaiting-approval jobs whose approval row resolved without them.

    Catches jobs stranded between the sweeper's two writes (upload row
    flipped, job state not yet updated) or any similar partial teardown.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT rj.id FROM recipe_jobs rj
            WHERE rj.status = 'awaiting_approval'
            AND NOT EXISTS (
                SELECT 1 FROM pending_uploads pu
                WHERE pu.job_id = rj.id AND pu.status = 'pending'
            )
        ''')
        return [r['id'] for r in cursor.fetchall()]


def extend_leases(job_ids: List[str], *, minutes: int = 10) -> int:
    """Heartbeat: push the liveness lease forward for live workers."""
    if not job_ids:
        return 0
    marks = ','.join('?' for _ in job_ids)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f'''
            UPDATE recipe_jobs SET lease_expires_at = datetime('now', ?)
            WHERE id IN ({marks})
            AND status IN ('running', 'uploading')
        ''', [f'+{int(minutes)} minutes', *job_ids])
        conn.commit()
        return cursor.rowcount


def sweep_stale_leases(*, max_attempts: int = 3,
                       backoff_base_seconds: int = 60) -> Dict[str, int]:
    """Requeue running jobs whose worker lease lapsed; fail repeat losers."""
    requeued = given_up = 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, attempts FROM recipe_jobs
            WHERE status = 'running'
            AND lease_expires_at IS NOT NULL
            AND lease_expires_at < datetime('now')
        ''')
        stale = cursor.fetchall()
        for row in stale:
            attempts = (row['attempts'] or 0) + 1
            if attempts <= max_attempts:
                delay = backoff_base_seconds * (2 ** (attempts - 1))
                cursor.execute('''
                    UPDATE recipe_jobs SET
                        status = 'queued',
                        attempts = ?,
                        next_run_at = datetime('now', ?),
                        lease_expires_at = NULL,
                        progress = 0,
                        stage_message = 'Recovered; waiting to retry',
                        error_message = 'Worker lease lost - retry scheduled',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (attempts, f'+{delay} seconds', row['id']))
                requeued += 1
            else:
                cursor.execute('''
                    UPDATE recipe_jobs SET
                        status = 'failed',
                        attempts = ?,
                        lease_expires_at = NULL,
                        error_message = 'Worker lost repeatedly; giving up',
                        state_changed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (attempts, row['id']))
                given_up += 1
        conn.commit()
    return {'requeued': requeued, 'failed': given_up}


def expire_due_approvals() -> List[str]:
    """Flip due pending uploads to 'expired'; return affected job ids."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, job_id FROM pending_uploads
            WHERE status = 'pending'
            AND expires_at IS NOT NULL
            AND expires_at <= datetime('now')
        ''')
        due = cursor.fetchall()
        if not due:
            return []
        ids = [r['id'] for r in due]
        marks = ','.join('?' for _ in ids)
        cursor.execute(
            f"UPDATE pending_uploads SET status = 'expired' "
            f'WHERE id IN ({marks})', ids)
        conn.commit()
        return [r['job_id'] for r in due]


def list_jobs_by_states(states: List[str], *, user_id: Optional[str] = None,
                        is_admin: bool = False, limit: int = 100,
                        offset: int = 0,
                        updated_since_hours: Optional[int] = None) -> List[Dict[str, Any]]:
    """Unified task listing across explicit status values."""
    if not states:
        return []
    placeholders = ','.join('?' for _ in states)
    owner_sql, owner_params = _owner_filter(user_id, is_admin)
    since_sql, since_param = '', []
    if updated_since_hours is not None:
        since_sql = (" AND updated_at >= datetime('now', ?) ")
        since_param = [f'-{int(updated_since_hours)} hours']
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT * FROM recipe_jobs
            WHERE status IN ({placeholders}){owner_sql}{since_sql}
            ORDER BY
                CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
                queue_priority DESC, created_at ASC, rowid ASC
            LIMIT ? OFFSET ?
        ''', [*states, *owner_params, *since_param, limit, offset])
        jobs = [dict(row) for row in cursor.fetchall()]

    waiting_ids = [j['id'] for j in jobs if j['status'] == 'awaiting_approval']
    uploads_by_job: Dict[str, Dict[str, Any]] = {}
    if waiting_ids:
        marks = ','.join('?' for _ in waiting_ids)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f'SELECT id, job_id, expires_at FROM pending_uploads '
                f"WHERE job_id IN ({marks}) AND status = 'pending'",
                waiting_ids,
            )
            uploads_by_job = {r['job_id']: dict(r) for r in cursor.fetchall()}

    for job in jobs:
        if job['status'] == 'queued':
            job['queue_position'] = get_queue_position(job['id'])
        if job['status'] == 'awaiting_approval' and job['id'] in uploads_by_job:
            up = uploads_by_job[job['id']]
            job['pending_upload_id'] = up['id']
            job['approval_expires_at'] = up['expires_at']
    return jobs


def count_jobs_by_states(*, user_id: Optional[str] = None,
                         is_admin: bool = False) -> Dict[str, int]:
    """Status -> count map, owner-scoped."""
    owner_sql, owner_params = _owner_filter(user_id, is_admin)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT status, COUNT(*) AS n FROM recipe_jobs '
            f'WHERE 1=1{owner_sql} GROUP BY status',
            owner_params,
        )
        return {row['status']: row['n'] for row in cursor.fetchall()}


def update_job_priority(job_id: str, priority: int, *,
                        user_id: Optional[str] = None,
                        is_admin: bool = False) -> bool:
    """Reorder a still-queued job."""
    owner_sql, owner_params = _owner_filter(user_id, is_admin)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recipe_jobs SET queue_priority = ?, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'queued'" + owner_sql,
            [priority, job_id, *owner_params],
        )
        conn.commit()
        return cursor.rowcount > 0


# ===== Pending Upload Functions =====

def create_pending_upload(upload_id: str, job_id: str, recipe_data: Dict,
                          image_path: Optional[str], image_candidates: List[str],
                          output_target: str, best_image_index: int = 0,
                          timeout_minutes: int = 5,
                          user_id: Optional[str] = None) -> bool:
    """Create a pending upload waiting for confirmation."""
    with get_db() as conn:
        cursor = conn.cursor()
        recipe_json = json.dumps(recipe_data)
        candidates_json = json.dumps(image_candidates) if image_candidates else None
        cursor.execute('''
            INSERT INTO pending_uploads
            (id, job_id, recipe_data, image_path, image_candidates, output_target,
             selected_image_index, best_image_index, status, expires_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                    datetime('now', '+' || ? || ' minutes'), ?)
        ''', (upload_id, job_id, recipe_json, image_path, candidates_json,
              output_target, best_image_index, best_image_index, timeout_minutes,
              user_id))
        conn.commit()
        return True


def get_pending_upload(upload_id: str, *, user_id: Optional[str] = None,
                       is_admin: bool = False) -> Optional[Dict[str, Any]]:
    """Get a pending upload by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        owner_sql, owner_params = _owner_filter(user_id, is_admin,
                                                column='pu.user_id')
        cursor.execute(
            f'SELECT * FROM pending_uploads pu WHERE pu.id = ?{owner_sql}',
            [upload_id] + owner_params,
        )
        row = cursor.fetchone()
        if row:
            item = dict(row)
            # Parse JSON fields
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            if item.get('image_candidates'):
                try:
                    item['image_candidates'] = json.loads(item['image_candidates'])
                except json.JSONDecodeError:
                    item['image_candidates'] = []
            return item
    return None


def get_pending_upload_by_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get the most recent pending upload record for a job."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM pending_uploads WHERE job_id = ? '
            'ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (job_id,),
        )
        row = cursor.fetchone()
        if row:
            item = dict(row)
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            if item.get('image_candidates'):
                try:
                    item['image_candidates'] = json.loads(item['image_candidates'])
                except json.JSONDecodeError:
                    item['image_candidates'] = []
            return item
    return None


def get_pending_uploads(*, user_id: Optional[str] = None,
                        is_admin: bool = False) -> List[Dict[str, Any]]:
    """Get all pending uploads that haven't expired."""
    with get_db() as conn:
        cursor = conn.cursor()
        owner_sql, owner_params = _owner_filter(user_id, is_admin,
                                                column='pu.user_id')
        cursor.execute(f'''
            SELECT pu.*, rj.url, rj.video_title
            FROM pending_uploads pu
            LEFT JOIN recipe_jobs rj ON pu.job_id = rj.id
            WHERE pu.status = 'pending'
            AND (pu.expires_at IS NULL OR pu.expires_at > datetime('now')){owner_sql}
            ORDER BY pu.created_at DESC
        ''', owner_params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            # Parse JSON fields
            if item.get('recipe_data'):
                try:
                    item['recipe_data'] = json.loads(item['recipe_data'])
                except json.JSONDecodeError:
                    pass
            if item.get('image_candidates'):
                try:
                    item['image_candidates'] = json.loads(item['image_candidates'])
                except json.JSONDecodeError:
                    item['image_candidates'] = []
            results.append(item)
        return results


def update_pending_upload_recipe(upload_id: str, recipe_data: Dict) -> bool:
    """Replace recipe JSON for a still-pending confirmation."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE pending_uploads
            SET recipe_data = ?
            WHERE id = ? AND status = 'pending'
            ''',
            (json.dumps(recipe_data), upload_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def confirm_pending_upload(upload_id: str, selected_image_index: Optional[int] = None) -> bool:
    """Mark a pending upload as confirmed."""
    with get_db() as conn:
        cursor = conn.cursor()
        if selected_image_index is not None:
            cursor.execute('''
                UPDATE pending_uploads
                SET status = 'confirmed', selected_image_index = ?
                WHERE id = ? AND status = 'pending'
            ''', (selected_image_index, upload_id))
        else:
            cursor.execute('''
                UPDATE pending_uploads
                SET status = 'confirmed'
                WHERE id = ? AND status = 'pending'
            ''', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def cancel_pending_upload(upload_id: str) -> bool:
    """Mark a pending upload as cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pending_uploads
            SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
        ''', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_pending_upload(upload_id: str) -> bool:
    """Delete a pending upload record."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
        conn.commit()
        return cursor.rowcount > 0


def cleanup_expired_pending_uploads() -> int:
    """Clean up expired pending uploads."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pending_uploads
            SET status = 'expired'
            WHERE status = 'pending'
            AND expires_at IS NOT NULL
            AND expires_at < datetime('now')
        ''')
        conn.commit()
        return cursor.rowcount


def save_push_subscription(username: str, endpoint: str, p256dh: str, auth_key: str) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO push_subscriptions (username, endpoint, p256dh, auth)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    username = excluded.username,
                    p256dh = excluded.p256dh,
                    auth = excluded.auth
            ''', (username, endpoint, p256dh, auth_key))
            conn.commit()
            return True
    except sqlite3.Error:
        return False


def get_push_subscriptions(username: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE username = ?',
            (username,),
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_push_subscription(endpoint: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', (endpoint,))
        conn.commit()
        return cursor.rowcount > 0


# ===== Mobile Auth Nonces (single-use OIDC state for the Android app) =====

def delete_expired_mobile_nonces(replay_grace_hours: int = 24) -> None:
    """Prune nonces well past expiry.

    Spent nonces are kept for `replay_grace_hours` beyond their expiry so that
    `mobile_nonce_exists` can still recognise a replay rather than silently
    treating it as an unknown state.
    """
    with get_db() as conn:
        conn.execute(
            "DELETE FROM mobile_auth_nonces WHERE expires_at <= datetime('now', ?)",
            (f'-{int(replay_grace_hours)} hours',),
        )
        conn.commit()


def save_mobile_nonce(nonce: str, redirect_uri: str, ttl_minutes: int = 10) -> bool:
    try:
        with get_db() as conn:
            conn.execute(
                '''INSERT INTO mobile_auth_nonces (nonce, redirect_uri, expires_at)
                   VALUES (?, ?, datetime('now', ?))''',
                (nonce, redirect_uri, f'+{int(ttl_minutes)} minutes'),
            )
            conn.commit()
            return True
    except sqlite3.Error:
        return False


def consume_mobile_nonce(nonce: str) -> Dict[str, Any] | None:
    """Atomically claim a live, unused nonce; None if unknown/expired/spent.

    The single UPDATE ... RETURNING is what makes the nonce single-use: two
    concurrent callbacks racing on the same state can never both win.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE mobile_auth_nonces
               SET used_at = CURRENT_TIMESTAMP
               WHERE nonce = ?
                 AND used_at IS NULL
                 AND expires_at > CURRENT_TIMESTAMP
               RETURNING redirect_uri''',
            (nonce,),
        )
        row = cursor.fetchone()
        conn.commit()
        if not row:
            return None
        return {'redirect_uri': row[0]}


def mobile_nonce_exists(nonce: str) -> bool:
    """True if this state was ever issued as a mobile nonce, spent or not.

    Lets the callback answer a replayed or expired mobile state with an error
    instead of falling through to the browser sign-in flow.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM mobile_auth_nonces WHERE nonce = ?', (nonce,))
        return cursor.fetchone() is not None


# Initialize database on module import
init_db()
