"""
Pick-a-Recipe Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
Supports parallel job processing with progress persistence.
"""

import os
import sys
import base64
import secrets
import tempfile
import threading
import json
from datetime import datetime, timedelta
from functools import wraps

from urllib.parse import urlencode, urlsplit

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response, g, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

import mobile_auth
import passwords
import login_throttle
from helpers import setup_logger
from database import (
    init_db, load_config, save_config,
    get_user, upsert_oidc_user,
    local_account_exists, claim_first_local_admin, set_user_password,
    sole_passwordless_username,
    list_users, count_admins, create_local_user, set_user_admin, delete_user,
    get_history, get_history_entry, get_history_count, delete_history_entry,
    delete_history_entries_bulk, delete_job_entry, delete_jobs_bulk,
    get_combined_history_and_jobs, get_combined_history_and_jobs_count,
    get_job, get_active_jobs,
    list_jobs_by_states, count_jobs_by_states, update_job_priority,
    get_pending_upload, get_pending_upload_by_job, get_pending_uploads,
    cleanup_expired_pending_uploads, cleanup_old_jobs,
    save_push_subscription, get_push_subscriptions, delete_push_subscription,
    save_mobile_nonce, consume_mobile_nonce, delete_expired_mobile_nonces,
    mobile_nonce_exists,
)
from job_manager import (
    init_job_manager, get_job_manager, resolve_max_concurrent,
    prune_artifact_dirs,
)
from uploaders import upload_recipe_to_targets, get_enabled_targets, format_targets

app = Flask(__name__)

# Behind reverse proxy (Cloudflare / NPM): trust X-Forwarded-* so _external URLs use HTTPS.
if os.environ.get('TRUST_PROXY', 'true').lower() in ('true', '1', 'yes'):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Path to the Vite SPA build output (ui/frontend/dist/)
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist')

@app.route('/manifest.json')
def serve_manifest():
    dist_manifest = os.path.join(FRONTEND_DIST, 'manifest.webmanifest')
    if os.path.exists(dist_manifest):
        response = make_response(send_from_directory(FRONTEND_DIST, 'manifest.webmanifest'))
        response.headers['Content-Type'] = 'application/manifest+json'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    response = make_response(app.send_static_file('manifest.json'))
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/sw.js')
def serve_sw():
    dist_sw = os.path.join(FRONTEND_DIST, 'sw.js')
    if os.path.exists(dist_sw):
        response = make_response(send_from_directory(FRONTEND_DIST, 'sw.js'))
        response.headers['Content-Type'] = 'application/javascript'
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    response = make_response(app.send_static_file('sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# Secret key for session cookies - MUST be persistent across restarts
# Generate a stable key based on a file if FLASK_SECRET_KEY is not set
def _get_or_create_secret_key():
    """Get secret key from env or generate and persist one."""
    env_key = os.environ.get('FLASK_SECRET_KEY')
    if env_key:
        return env_key
    
    # Store the key in a file so it persists across restarts
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            return f.read().strip()
    
    # Generate and save a new key
    new_key = secrets.token_hex(32)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
        os.chmod(key_file, 0o600)  # Restrict permissions
    except (IOError, OSError):
        pass  # If we can't write, still use the key for this session
    return new_key


# Health endpoints (intentionally unauthenticated so container/orchestrator
# health probes can reach them). /healthz is a cheap liveness ping; /api/health
# runs the yt-dlp + LLM checks and returns 200 when healthy, 503 otherwise.
@app.route('/healthz')
def healthz():
    return jsonify({'status': 'ok'}), 200


@app.route('/api/health')
def api_health():
    probe = request.args.get('probe') in ('1', 'true', 'yes')
    try:
        from health import run_health_checks
        report = run_health_checks(probe_network=probe)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 503
    return jsonify(report), (200 if report['ok'] else 503)


app.secret_key = _get_or_create_secret_key()

# Configure session cookie settings
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('true', '1', 'yes'):
    app.config['SESSION_COOKIE_SECURE'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')


@app.context_processor
def inject_template_globals():
    """Expose debug flag, cache-bust version and approvals badge count."""
    import time
    return {
        'flask_debug': FLASK_DEBUG,
        'static_version': str(int(time.time())) if FLASK_DEBUG else '7',
        'approvals_count': _count_pending_approvals(),
    }


def _count_pending_approvals() -> int:
    """Live count for the sidebar badge, scoped to the signed-in user."""
    if not _is_logged_in():
        return 0
    try:
        from database import get_db
        params = []
        sql = ("SELECT COUNT(*) FROM pending_uploads "
               "WHERE status = 'pending' "
               "AND (expires_at IS NULL OR expires_at > datetime('now'))")
        if not _current_user_is_admin():
            sql += ' AND (user_id IS NULL OR user_id = ?)'
            params.append(_current_username())
        with get_db() as conn:
            return conn.execute(sql, params).fetchone()[0]
    except Exception as exc:
        print(f'[Badge] approvals count failed: {exc}')
        return 0


# Use threading mode instead of eventlet to avoid monkey-patching issues with SSL/requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize database
init_db()

# Run startup health checks (yt-dlp + configured LLM). Non-fatal: logs clear,
# actionable errors so operators catch the top outage classes (PIC-34/PIC-42)
# before a user does. Results are also exposed via /api/health.
try:
    from health import run_startup_health_check
    run_startup_health_check()
except Exception as _health_exc:  # never let health checks block startup
    print(f"[Health] startup health check skipped: {_health_exc}")

# Initialize job manager (process func registered after definition below)
job_manager = init_job_manager(socketio)

# ===== Authentication mode =====
# 'local' (default) keeps username and password accounts in this app's own
# database, so a fresh container works with no configuration beyond choosing a
# password on first run. 'authentik' delegates to Authentik single sign-on.
#
# There is no way to turn authentication off. Settings holds the LLM, Mealie and
# Tandoor API keys, so an open instance hands those to anyone who can reach the
# port.
_DEFAULT_LOCAL_USERNAME = 'admin'
AUTH_MODE = (os.environ.get('AUTH_MODE') or 'local').strip().lower()

# Account lifecycle goes through a logger rather than print(): these are the
# records you go looking for after something unexpected, and stdout buffering
# means a print can still be sitting unflushed when the process is killed.
auth_log = setup_logger('auth')

if AUTH_MODE == 'none':
    # Accepted rather than rejected so existing deployments still boot. They
    # land on first-run setup, which adopts the passwordless account that this
    # mode created and keeps its job history attached.
    print("[Auth] WARNING: AUTH_MODE=none is no longer supported and has been "
          "treated as AUTH_MODE=local. Authentication is now always on. Open "
          "the app to set a password for the existing account; its history is "
          "preserved. Update your configuration to AUTH_MODE=local.")
    AUTH_MODE = 'local'

if AUTH_MODE not in ('local', 'authentik'):
    raise RuntimeError(
        f"Invalid AUTH_MODE={AUTH_MODE!r}. Use 'local' for username and password "
        "accounts stored by this app (default), or 'authentik' for Authentik "
        "single sign-on."
    )

LOCAL_AUTH = AUTH_MODE == 'local'
# Prefills the username field on the setup page. Only a default: whatever is
# submitted there wins, so this is convenience, not configuration.
LOCAL_USERNAME = (
    os.environ.get('AUTH_LOCAL_USERNAME') or _DEFAULT_LOCAL_USERNAME
).strip() or _DEFAULT_LOCAL_USERNAME

# ===== Authentication via Authentik (OIDC) =====
# Single sign-on through the self-hosted Authentik instance at auth.pickel.me.
# Access requires membership in AUTHENTIK_USER_GROUP (admins additionally in
# AUTHENTIK_ADMIN_GROUP). In Authentik, add the "authentik read groups" scope
# mapping to the provider so the `groups` claim is included in tokens.
AUTHENTIK_ISSUER_URL = os.environ.get(
    'AUTHENTIK_ISSUER_URL',
    'https://auth.pickel.me/application/o/pick-a-recipe',
).rstrip('/')
AUTHENTIK_USER_GROUP = os.environ.get('AUTHENTIK_USER_GROUP', 'pick-a-recipe-users')
AUTHENTIK_ADMIN_GROUP = os.environ.get('AUTHENTIK_ADMIN_GROUP', 'admins')

oauth = None
if LOCAL_AUTH:
    print('[Auth] AUTH_MODE=local — sign in with an account stored by this app.')
elif os.environ.get('AUTHENTIK_CLIENT_ID') and os.environ.get('AUTHENTIK_CLIENT_SECRET'):
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    oauth.register(
        name='authentik',
        client_id=os.environ['AUTHENTIK_CLIENT_ID'],
        client_secret=os.environ['AUTHENTIK_CLIENT_SECRET'],
        server_metadata_url=f'{AUTHENTIK_ISSUER_URL}/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile groups'},
    )
else:
    print('[Auth] WARNING: AUTH_MODE=authentik but AUTHENTIK_CLIENT_ID / '
          'AUTHENTIK_CLIENT_SECRET are not set — nobody can sign in. Configure '
          'Authentik OIDC, or drop AUTH_MODE to use local accounts instead.')


def _bearer_identity() -> dict | None:
    """Resolve Authorization: Bearer <jwt> to {'username', 'is_admin'}, cached per request.

    Cookie sessions take precedence everywhere; Bearer is only consulted when
    no session exists, so the web/PWA path behaves exactly as before.
    """
    if hasattr(g, '_mobile_identity'):
        return g._mobile_identity
    identity = None
    try:
        header = request.headers.get('Authorization', '')
    except RuntimeError:
        header = ''
    if header.startswith('Bearer '):
        payload = mobile_auth.decode_token(header[len('Bearer '):].strip(), expected_type='access')
        if payload:
            user = get_user(payload.get('sub') or '')
            if user:
                identity = {'username': user['username'], 'is_admin': bool(user['is_admin'])}
    g._mobile_identity = identity
    return identity


def _session_account() -> dict | None:
    """The account the session cookie names, re-read from the database.

    Cached per request. Local mode has to consult the database rather than
    trusting the cookie: an admin who deletes or demotes someone expects that
    to take effect now, and a signed cookie would otherwise keep working until
    it expired. Returns None once the account is gone or has lost its password.

    Authentik mode keeps trusting the cookie. Admin there comes from group
    membership resolved at sign-in, and the local row is a cache of the last
    login rather than the authority, so re-reading it would decide access from
    stale data.
    """
    if not LOCAL_AUTH:
        return None
    if hasattr(g, '_session_account'):
        return g._session_account

    username = session.get('user')
    account = get_user(username) if username else None
    if account is not None and not account.get('password_hash'):
        # Left without a password, so nothing can authenticate as it any more.
        account = None
    g._session_account = account
    return account


def _session_identity() -> dict | None:
    """{'username', 'is_admin'} for a cookie session, or None if it is not valid."""
    if 'user' not in session:
        return None
    if not LOCAL_AUTH:
        return {
            'username': session['user'],
            'is_admin': bool(session.get('is_admin')),
        }
    account = _session_account()
    if account is None:
        return None
    return {
        'username': account['username'],
        'is_admin': bool(account['is_admin']),
    }


def _current_identity() -> dict | None:
    """Who is making this request: the cookie session, else a Bearer token."""
    try:
        identity = _session_identity()
    except RuntimeError:  # outside a request context
        identity = None
    if identity is not None:
        return identity
    return _bearer_identity_safe()


def _is_logged_in() -> bool:
    return _current_identity() is not None


def _current_user_is_admin() -> bool:
    identity = _current_identity()
    return bool(identity and identity['is_admin'])


def _bearer_identity_safe() -> dict | None:
    try:
        return _bearer_identity()
    except RuntimeError:
        return None


def _current_username() -> str | None:
    """Effective owner for the request: the session user, or a Bearer identity."""
    identity = _current_identity()
    return identity['username'] if identity else None


def _setup_required() -> bool:
    """True while local mode has no account anyone can sign in to.

    Only ever True before the first password is set. Authentik mode never needs
    setup: accounts arrive from the identity provider.
    """
    if not LOCAL_AUTH:
        return False
    return not local_account_exists()


def _scope() -> tuple[str | None, bool]:
    """Owner scope for the current request: (user_id, is_admin)."""
    return _current_username(), _current_user_is_admin()


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def api_login_required(f):
    """Decorator to require login for API routes (returns JSON error)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def api_admin_required(f):
    """Require an admin for API routes.

    401 when nobody is signed in and 403 when someone is but lacks the rights,
    so a client can tell "log in again" from "you cannot do this".
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        identity = _current_identity()
        if identity is None:
            return jsonify({'error': 'Authentication required'}), 401
        if not identity['is_admin']:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


def _socketio_authenticated() -> bool:
    return _is_logged_in()


def _start_job_for_url(url: str, *, retry_from_history_id: int | None = None,
                       priority: int = 0, user_id: str | None = None) -> dict:
    jm = get_job_manager()
    if user_id is None:
        user_id = _current_username()
    job_id = jm.create_new_job(
        url, retry_from_history_id=retry_from_history_id,
        priority=priority, user_id=user_id,
    )
    jm.start_job(job_id, process_video_job)
    job = get_job(job_id)
    return {
        'job_id': job_id,
        'status': job.get('status', 'queued'),
        'url': url,
        'queue_position': job.get('queue_position', get_queue_position_safe(job_id)),
        'message': 'Job queued for processing',
    }


def get_queue_position_safe(job_id: str) -> int:
    from database import get_queue_position
    return get_queue_position(job_id)


def _run_cleanup_scheduler() -> None:
    """Periodic cleanup of old jobs and expired pending uploads."""
    def loop():
        import time
        while True:
            time.sleep(3600)
            try:
                cleanup_old_jobs(hours=72)
                cleanup_expired_pending_uploads()
                delete_expired_mobile_nonces()
                prune_artifact_dirs()
            except Exception as exc:
                print(f"[Cleanup] error: {exc}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()


_run_cleanup_scheduler()


@app.route('/')
@login_required
def index():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')

    shared_url = (
        session.pop('shared_url', None) or
        request.args.get('shared_url') or
        request.args.get('shared_text') or
        request.args.get('url') or
        request.args.get('text') or
        ''
    )
    auto_from_share = session.pop('auto_start_extraction', False)

    if shared_url and not shared_url.startswith('http'):
        import re
        url_match = re.search(r'(https?://[^\s]+)', shared_url)
        if url_match:
            shared_url = url_match.group(1)

    return render_template(
        'index.html',
        shared_url=shared_url,
        auto_start=(
            request.args.get('auto') in ('1', 'true', 'yes')
            or auto_from_share
        ),
        max_concurrent=resolve_max_concurrent(),
    )


@app.route('/jobs/<job_id>')
@login_required
def job_detail(job_id):
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')

    job = get_job(job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect(url_for('index'))
    return render_template('job.html', job=job, max_concurrent=resolve_max_concurrent())


@app.route('/tasks')
@login_required
def tasks_page():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return render_template('tasks.html')


@app.route('/share', methods=['GET', 'POST'])
def share():
    """Handle shared URLs from PWA share_target.
    
    NOTE: This route intentionally does NOT require login so that Android's
    share_target can POST data before authentication. The URL is saved to
    session first, then user is redirected to login if needed.
    """
    import re
    
    # Get shared content from POST form data (Android) or query params (fallback)
    if request.method == 'POST':
        shared_url = request.form.get('url') or ''
        shared_text = request.form.get('text') or ''
        shared_title = request.form.get('title', '')
    else:
        shared_url = request.args.get('url') or ''
        shared_text = request.args.get('text') or ''
        shared_title = request.args.get('title', '')
    
    # Try to extract URL from various sources
    # Priority: url param > text param > title param
    final_url = shared_url
    
    if not final_url and shared_text:
        # Apps like TikTok/Instagram often share URL in text field
        url_match = re.search(r'(https?://[^\s]+)', shared_text)
        if url_match:
            final_url = url_match.group(1)
        else:
            final_url = shared_text
    
    if not final_url and shared_title:
        url_match = re.search(r'(https?://[^\s]+)', shared_title)
        if url_match:
            final_url = url_match.group(1)
    
    # Store in session BEFORE checking auth - this preserves the URL through login
    session['shared_url'] = final_url
    session['auto_start_extraction'] = True
    
    # If user is not logged in, redirect to login (URL is preserved in session)
    if not _is_logged_in():
        return redirect(url_for('login'))
    
    # User is logged in, redirect to main page
    return redirect(url_for('index'))


# One message for every credential failure. Saying "no such user" or "wrong
# password" would let anyone enumerate which accounts exist.
_INVALID_CREDENTIALS = 'Invalid username or password.'


def _client_ip() -> str:
    """Best available client address, for throttling.

    ProxyFix (applied above when TRUST_PROXY is on) has already resolved
    X-Forwarded-For, so remote_addr is the caller rather than the proxy.
    """
    return request.remote_addr or 'unknown'


def _wants_json() -> bool:
    """True when the caller is the SPA rather than a plain form submission."""
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept['application/json'] > accept['text/html']


def _establish_session(username: str, *, is_admin: bool) -> None:
    """Start an authenticated session, discarding whatever preceded it.

    The session is cleared before it is repopulated so that a fixated or stale
    cookie cannot carry state across the privilege change. Anything the
    anonymous /share flow parked is deliberately carried over: it is the whole
    reason a user was sent to sign in.
    """
    pending_shared_url = session.get('shared_url')
    pending_auto_start = session.get('auto_start_extraction')

    session.clear()
    session['user'] = username
    session['is_admin'] = is_admin
    session.permanent = True

    if pending_shared_url:
        session['shared_url'] = pending_shared_url
    if pending_auto_start:
        session['auto_start_extraction'] = True


def _login_failed(message: str, status: int, *, retry_after: int | None = None):
    """Report a failed sign-in, as JSON or a redirect depending on the caller."""
    if _wants_json():
        response = jsonify({'error': message})
        response.status_code = status
        if retry_after is not None:
            response.headers['Retry-After'] = str(retry_after)
        return response
    flash(message, 'error')
    response = redirect(url_for('login'))
    if retry_after is not None:
        response.headers['Retry-After'] = str(retry_after)
    return response


@app.route('/login')
def login():
    """Login page: a password form in local mode, an SSO button in Authentik mode."""
    if _is_logged_in():
        return redirect(url_for('index'))
    if _setup_required():
        return redirect(url_for('setup'))
    spa_login = os.path.join(FRONTEND_DIST, 'index.html')
    if os.path.exists(spa_login):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return render_template(
        'login.html',
        sso_enabled=oauth is not None,
        local_auth=LOCAL_AUTH,
    )


def _adoptable_username() -> str | None:
    """The account first-run setup should offer to take over, if there is one.

    An instance upgrading from AUTH_MODE=none already owns jobs and uploads under
    the name that mode seeded, and ownership is recorded as the username rather
    than the row id. Offering that name means the obvious path through the form
    keeps the history; typing a different one starts fresh.
    """
    return sole_passwordless_username()


def _validate_username(username: str) -> str | None:
    """Reason the username is unacceptable, or None if it is fine.

    Control characters are rejected rather than stripped: a name containing a
    newline could forge entries in the account audit log, and one containing
    other invisibles would be impossible to type at the sign-in form.
    """
    if not username:
        return 'Choose a username.'
    if len(username) > 64:
        return 'Username must be at most 64 characters.'
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in username):
        return 'Username cannot contain spaces or control characters.'
    return None


def _validate_new_account(username: str, password: str, confirm: str) -> str | None:
    """Reason the proposed first account is unacceptable, or None if it is fine."""
    problem = _validate_username(username)
    if problem:
        return problem
    if password != confirm:
        return 'The two passwords do not match.'
    try:
        passwords.validate(password)
    except passwords.WeakPassword as exc:
        return str(exc)
    return None


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Create the first account, while the instance has none.

    Falls back to a server-rendered form when no frontend bundle is present, so
    a source checkout without a `npm run build` can still be set up — this is
    the only way into a fresh instance.
    """
    if not _setup_required():
        # Closed for good once an account exists, so this cannot be used to
        # add a second admin or overwrite the first one's password.
        return redirect(url_for('login'))

    adoptable = _adoptable_username()
    suggested = adoptable or LOCAL_USERNAME

    if request.method == 'GET':
        spa = os.path.join(FRONTEND_DIST, 'index.html')
        if os.path.exists(spa):
            return send_from_directory(FRONTEND_DIST, 'index.html')
        return render_template(
            'setup.html', suggested_username=suggested, adopting=adoptable
        )

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    confirm = request.form.get('confirm_password') or ''

    error = _validate_new_account(username, password, confirm)
    if error:
        return render_template(
            'setup.html',
            suggested_username=username or suggested,
            adopting=adoptable,
            error=error,
        ), 400

    user = claim_first_local_admin(username, passwords.hash_password(password))
    if user is None:
        # Another request completed setup between the guard and the write.
        return redirect(url_for('login'))

    auth_log.info('first account created: %s', user['username'])
    _establish_session(user['username'], is_admin=True)
    return redirect(url_for('index'))


@app.route('/api/setup', methods=['GET', 'POST'])
def api_setup():
    """JSON counterpart of /setup, for the SPA.

    Unauthenticated by necessity — it exists precisely because no account does
    yet — but it stops answering the moment one exists, so it is not a way to
    read or change an established instance.
    """
    if not _setup_required():
        return jsonify({'error': 'Setup has already been completed.'}), 409

    if request.method == 'GET':
        adoptable = _adoptable_username()
        return jsonify({
            'suggested_username': adoptable or LOCAL_USERNAME,
            # Named so the client can say what carrying the name over means.
            'adopting': adoptable,
        })

    payload = request.get_json(silent=True)
    source = payload if isinstance(payload, dict) else request.form
    username = str(source.get('username') or '').strip()
    password = str(source.get('password') or '')
    confirm = str(source.get('confirm_password') or '')

    error = _validate_new_account(username, password, confirm)
    if error:
        return jsonify({'error': error}), 400

    user = claim_first_local_admin(username, passwords.hash_password(password))
    if user is None:
        return jsonify({'error': 'Setup has already been completed.'}), 409

    auth_log.info('first account created: %s', user['username'])
    _establish_session(user['username'], is_admin=True)
    return jsonify({'user': user['username'], 'is_admin': True})


class _LoginRejected(Exception):
    """A refused sign-in, carrying what the client should be told."""

    def __init__(self, message: str, status: int, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


def _authenticate_password(username: str, password: str) -> dict:
    """Return the account for valid credentials, or raise _LoginRejected.

    Shared by the browser form and the Android app so both are slowed by the
    same throttle ladder — otherwise the app endpoint would be an unthrottled
    way around the form's.
    """
    # Keyed on both, so guessing many passwords for one account and one password
    # across many accounts are both slowed. The client cannot influence the key
    # beyond the username it is already trying.
    throttle_key = f'{_client_ip()}|{username.lower()}'
    wait = login_throttle.retry_after(throttle_key)
    if wait > 0:
        auth_log.warning('sign-in throttled for %r from %s',
                         username, _client_ip())
        raise _LoginRejected(
            f'Too many attempts. Try again in {int(wait) + 1} seconds.',
            429, retry_after=int(wait) + 1)

    # Logged as separate reasons on purpose. The response cannot distinguish
    # them without telling an attacker which usernames exist, but the operator
    # reading the log needs to: many misses across distinct names is credential
    # stuffing, while repeated misses on one real name is a targeted guess.
    user = get_user(username) if username else None
    if user is None:
        # Spend comparable time to a real check: otherwise a fast rejection
        # reveals that the username does not exist.
        passwords.dummy_verify()
        login_throttle.record_failure(throttle_key)
        auth_log.warning('sign-in failed: no such account %r from %s',
                         username, _client_ip())
        raise _LoginRejected(_INVALID_CREDENTIALS, 401)

    if not passwords.verify(user.get('password_hash'), password):
        login_throttle.record_failure(throttle_key)
        auth_log.warning('sign-in failed: wrong password for %s from %s',
                         user['username'], _client_ip())
        raise _LoginRejected(_INVALID_CREDENTIALS, 401)

    login_throttle.reset(throttle_key)

    # Cost parameters may have risen since this hash was stored; the password is
    # in hand exactly once, so this is the only chance to upgrade it.
    if passwords.needs_rehash(user['password_hash']):
        set_user_password(user['username'], passwords.hash_password(password))
    return user


@app.route('/auth/local/login', methods=['POST'])
def auth_local_login():
    """Sign in with a username and password held by this app."""
    if not LOCAL_AUTH:
        return _login_failed('Password sign-in is disabled on this instance.', 400)
    if _setup_required():
        return redirect(url_for('setup'))

    payload = request.get_json(silent=True) if request.is_json else None
    source = payload if isinstance(payload, dict) else request.form
    username = str(source.get('username') or '').strip()
    password = str(source.get('password') or '')

    try:
        user = _authenticate_password(username, password)
    except _LoginRejected as rejected:
        return _login_failed(rejected.message, rejected.status,
                             retry_after=rejected.retry_after)

    auth_log.info('signed in: %s from %s', user['username'], _client_ip())
    _establish_session(user['username'], is_admin=bool(user['is_admin']))
    if _wants_json():
        return jsonify({
            'user': user['username'],
            'is_admin': bool(user['is_admin']),
        })
    return redirect(url_for('index'))


def _authentik_redirect_uri() -> str:
    """OIDC callback URL — must match the redirect URI configured in Authentik."""
    explicit = os.environ.get('AUTHENTIK_REDIRECT_URI', '').strip()
    if explicit:
        return explicit
    public = os.environ.get('PUBLIC_URL', '').strip().rstrip('/')
    if public:
        return f'{public}/auth/callback'
    return url_for('auth_callback', _external=True)


@app.route('/auth/login')
def auth_login():
    """Redirect the user to Authentik for authentication."""
    if oauth is None:
        if LOCAL_AUTH:
            # Nothing to redirect to; the password form is on the login page.
            return redirect(url_for('login'))
        flash('Single sign-on is not configured. Set AUTHENTIK_CLIENT_ID and '
              'AUTHENTIK_CLIENT_SECRET, or drop AUTH_MODE to use local '
              'accounts instead.', 'error')
        return redirect(url_for('login'))
    return oauth.authentik.authorize_redirect(_authentik_redirect_uri())


def _resolve_oidc_identity(userinfo: dict) -> tuple[str | None, str | None, bool]:
    """Map IdP claims to (sub, username, is_admin), shared by web and mobile.

    Returns username None when group membership does not grant access. Both
    sign-in paths go through here so the mobile flow can never become a way
    around the group check.
    """
    sub = userinfo.get('sub')
    if not sub:
        return None, None, False

    groups = set(userinfo.get('groups') or [])
    is_admin = AUTHENTIK_ADMIN_GROUP in groups
    if not is_admin and AUTHENTIK_USER_GROUP not in groups:
        auth_log.warning('denied login for sub=%s: groups=%s', sub, sorted(groups))
        return sub, None, False

    username = (
        userinfo.get('preferred_username')
        or (userinfo.get('email') or '').split('@')[0]
        or sub
    )
    return sub, username, is_admin


def _mobile_deep_link_schemes() -> set[str]:
    raw = os.environ.get('MOBILE_DEEP_LINK_SCHEMES', 'par')
    return {s.strip().lower() for s in raw.split(',') if s.strip()}


def _is_allowed_deep_link(uri: str) -> bool:
    """Only allow the app's own custom scheme, so the callback cannot be
    redirected to an attacker-controlled target carrying live tokens."""
    if not uri:
        return False
    parts = urlsplit(uri)
    return parts.scheme.lower() in _mobile_deep_link_schemes() and not parts.netloc.startswith('.')


def _authentik_token_endpoints() -> tuple[str, str]:
    """(token_endpoint, userinfo_endpoint), preferring OIDC discovery.

    Falls back to Authentik's documented layout under the issuer so that app
    sign-in does not hinge on a second outbound request succeeding mid-redirect.
    """
    try:
        meta = oauth.authentik.load_server_metadata()
        return meta['token_endpoint'], meta['userinfo_endpoint']
    except Exception as exc:
        print(f'[MobileAuth] OIDC discovery unavailable ({exc}); '
              f'falling back to issuer-derived endpoints')
        return f'{AUTHENTIK_ISSUER_URL}/token/', f'{AUTHENTIK_ISSUER_URL}/userinfo/'


# The set RFC 6749 §4.1.2.1 defines. Anything outside it is reported
# generically rather than reflected, so a provider cannot put text of its
# choosing into a redirect aimed at the app.
_OAUTH_ERROR_CODES = frozenset({
    'invalid_request', 'unauthorized_client', 'access_denied',
    'unsupported_response_type', 'invalid_scope', 'server_error',
    'temporarily_unavailable',
})


def _mobile_idp_error(nonce_row: dict, idp_error: str):
    """Hand an identity-provider error back to the app over its deep link."""
    code = idp_error if idp_error in _OAUTH_ERROR_CODES else 'server_error'
    print(f'[MobileAuth] identity provider returned error={code}')
    return redirect(f'{nonce_row["redirect_uri"]}#{urlencode({"error": code})}')


def _mobile_oidc_callback(code: str | None, nonce_row: dict):
    """Finish sign-in for the Android app and hand tokens back via deep link.

    Runs the authorization-code exchange server-side (the client secret must
    not ship in the app), then redirects to the app's custom scheme with a
    freshly minted JWT pair in the URL fragment. Fragments are not sent to
    servers or logged in the Referer header, unlike query parameters.
    """
    redirect_uri = nonce_row['redirect_uri']

    def fail(reason: str, log: str):
        print(f'[MobileAuth] {log}')
        return redirect(f'{redirect_uri}#{urlencode({"error": reason})}')

    if not code:
        return fail('invalid_request', 'callback without an authorization code')
    if not mobile_auth.mobile_auth_enabled():
        return fail('server_misconfigured', 'JWT_SECRET_KEY is not set')

    token_endpoint, userinfo_endpoint = _authentik_token_endpoints()
    try:
        token_resp = requests.post(
            token_endpoint,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': _authentik_redirect_uri(),
                'client_id': os.environ['AUTHENTIK_CLIENT_ID'],
                'client_secret': os.environ['AUTHENTIK_CLIENT_SECRET'],
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        idp_access_token = (token_resp.json() or {}).get('access_token')
        if not idp_access_token:
            return fail('invalid_grant', 'token endpoint returned no access_token')

        userinfo_resp = requests.get(
            userinfo_endpoint,
            headers={'Authorization': f'Bearer {idp_access_token}'},
            timeout=15,
        )
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json() or {}
    except Exception as exc:
        return fail('token_exchange_failed', f'token exchange failed: {exc}')

    sub, username, is_admin = _resolve_oidc_identity(userinfo)
    if not sub:
        return fail('invalid_identity', 'identity provider returned no subject claim')
    if username is None:
        return fail('not_authorized', f'sub={sub} is not in an authorized group')

    user = upsert_oidc_user(
        sub=sub,
        username=username,
        email=userinfo.get('email'),
        name=userinfo.get('name'),
        avatar_url=userinfo.get('picture'),
        is_admin=is_admin,
    )
    pair = mobile_auth.issue_token_pair(user['username'], is_admin=is_admin)
    print(f"[MobileAuth] issued token pair for {user['username']}")
    return redirect(f'{redirect_uri}#{urlencode(pair)}')


@app.route('/auth/callback')
def auth_callback():
    """Handle the OIDC callback from Authentik."""
    if oauth is None:
        return redirect(url_for('login'))

    idp_error = request.args.get('error')

    # Which flow this callback belongs to has to be settled before the error is
    # handled. A state we issued as a mobile nonce belongs to the app, and its
    # errors have to travel back over the deep link; flashing them would strand
    # the user in a browser on the web login page with the app none the wiser.
    # Recognising a spent nonce also lets us reject a replay outright instead of
    # falling through to the browser flow and failing with a session error.
    state = request.args.get('state') or ''
    if state:
        nonce_row = consume_mobile_nonce(state)
        if nonce_row is not None:
            if idp_error:
                return _mobile_idp_error(nonce_row, idp_error)
            return _mobile_oidc_callback(request.args.get('code'), nonce_row)
        if mobile_nonce_exists(state):
            print('[MobileAuth] rejected replayed or expired sign-in state')
            return jsonify({'error': 'This sign-in link has already been used or '
                                     'has expired. Please sign in again.'}), 400

    if idp_error:
        reason = request.args.get('error_description') or idp_error
        flash(f'Sign-in failed: {reason}', 'error')
        return redirect(url_for('login'))

    try:
        token = oauth.authentik.authorize_access_token()
    except Exception as exc:
        print(f"[Auth] token exchange failed: {exc}")
        flash('Sign-in failed. Please try again.', 'error')
        return redirect(url_for('login'))

    userinfo = token.get('userinfo') or oauth.authentik.parse_id_token(token)
    sub, username, is_admin = _resolve_oidc_identity(userinfo)
    if not sub:
        flash('Sign-in failed: identity provider did not provide a subject claim.', 'error')
        return redirect(url_for('login'))
    if username is None:
        flash('Your account is not authorized to use Pick-a-Recipe. Ask an '
              'administrator to add you to the appropriate group in Authentik.', 'error')
        return redirect(url_for('login'))

    user = upsert_oidc_user(
        sub=sub,
        username=username,
        email=userinfo.get('email'),
        name=userinfo.get('name'),
        avatar_url=userinfo.get('picture'),
        is_admin=is_admin,
    )

    _establish_session(user['username'], is_admin=is_admin)

    flash(f"Welcome, {user.get('name') or user['username']}!", 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Log out: clear the local session and end the Authentik session."""
    # Everything, not just the identity keys: a stale share or auto-start left
    # behind would be picked up by whoever signs in next on this browser.
    session.clear()

    end_session_url = None
    if oauth is not None:
        try:
            end_session_url = oauth.authentik.load_server_metadata().get('end_session_endpoint')
        except Exception as exc:
            print(f"[Auth] could not load OIDC metadata for logout: {exc}")

    if end_session_url:
        return redirect(end_session_url)
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Settings page for configuration."""
    config = load_config()

    if request.method == 'POST':
        # Update configuration from form
        config['llm_provider'] = request.form.get('llm_provider', 'openai')
        config['openai_api_key'] = request.form.get('openai_api_key', '')
        config['openai_model'] = request.form.get('openai_model', '')
        config['gemini_api_key'] = request.form.get('gemini_api_key', '')
        config['gemini_model'] = request.form.get('gemini_model', '')
        config['recipe_lang'] = request.form.get('recipe_lang', 'hebrew')
        config['mealie_api_key'] = request.form.get('mealie_api_key', '')
        config['mealie_host'] = request.form.get('mealie_host', '')
        config['tandoor_api_key'] = request.form.get('tandoor_api_key', '')
        config['tandoor_host'] = request.form.get('tandoor_host', '')
        config['target_language'] = request.form.get('target_language', 'he')
        config['tandoor_enabled'] = 'true' if request.form.get('tandoor_enabled') else 'false'
        config['mealie_enabled'] = 'true' if request.form.get('mealie_enabled') else 'false'
        config['whisper_model'] = request.form.get('whisper_model', 'small')
        config['hf_token'] = request.form.get('hf_token', '')
        config['usda_fdc_api_key'] = request.form.get('usda_fdc_api_key', '')
        config['yt_dlp_cookies_file'] = request.form.get('yt_dlp_cookies_file', '')
        config['yt_dlp_cookies_browser'] = request.form.get('yt_dlp_cookies_browser', '')
        # Checkbox: present in form data only when checked
        config['confirm_before_upload'] = 'true' if request.form.get(
            'confirm_before_upload') else 'false'
        config['max_concurrent_jobs'] = request.form.get('max_concurrent_jobs', '3')

        save_config(config)
        get_job_manager().refresh_concurrency()
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings'))

    return render_template(
        'settings.html',
        config=config,
        max_concurrent=resolve_max_concurrent(),
    )


# ===== Job API Endpoints =====

@app.route('/api/jobs', methods=['POST'])
@api_login_required
def create_job():
    """Create a new analysis job."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    result = _start_job_for_url(url)
    return jsonify(result)


@app.route('/api/jobs/batch', methods=['POST'])
@api_login_required
def create_jobs_batch():
    """Create multiple jobs from a list of URLs."""
    data = request.get_json() or {}
    urls = data.get('urls') or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.replace(',', '\n').split('\n') if u.strip()]
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400

    jobs = []
    for url in urls[:50]:
        jobs.append(_start_job_for_url(url.strip()))
    return jsonify({'jobs': jobs, 'count': len(jobs)})


@app.route('/api/jobs/retry', methods=['POST'])
@api_login_required
def retry_job():
    """Retry a failed extraction — starts immediately with live progress."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    history_id = data.get('history_id')

    if not url and history_id:
        item = get_history_entry(int(history_id))
        if item:
            url = item.get('url', '')

    if not url:
        return jsonify({'error': 'URL or history_id is required'}), 400

    result = _start_job_for_url(
        url,
        retry_from_history_id=int(history_id) if history_id else None,
        priority=1,
        user_id=session['user'],
    )
    result['auto_start'] = True
    return jsonify(result)


@app.route('/api/jobs/queue', methods=['GET'])
@api_login_required
def queue_stats():
    jm = get_job_manager()
    return jsonify(jm.get_queue_stats())


@app.route('/api/jobs', methods=['GET'])
@api_login_required
def list_jobs():
    """List all active jobs visible to the requesting user."""
    user_id, is_admin = _scope()
    jobs = get_active_jobs(user_id=user_id, is_admin=is_admin)
    return jsonify({'jobs': jobs})


@app.route('/api/jobs/<job_id>', methods=['GET'])
@api_login_required
def get_job_status(job_id):
    """Get status of a specific job."""
    jm = get_job_manager()
    user_id, is_admin = _scope()
    job = jm.get_job_status(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@api_login_required
def cancel_job_api(job_id):
    """Cancel a running job."""
    jm = get_job_manager()
    user_id, is_admin = _scope()
    job = get_job(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    result = jm.cancel_job(job_id)
    if result:
        return jsonify({'status': 'cancelled', 'job_id': job_id})
    return jsonify({'error': 'Job not found or already completed'}), 404


_TASK_STATE_GROUPS = {
    'pending': ['queued'],
    'processing': ['running', 'uploading'],
    'awaiting_approval': ['awaiting_approval'],
    'active': ['queued', 'running', 'awaiting_approval', 'uploading'],
}
_ALL_STATES = ['queued', 'running', 'awaiting_approval', 'uploading',
               'completed', 'failed', 'cancelled', 'expired']


@app.route('/api/tasks', methods=['GET'])
@api_login_required
def list_tasks():
    """Unified task listing for the dashboard."""
    user_id, is_admin = _scope()
    scope = request.args.get('scope', 'mine')
    if scope == 'all' and not is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    state = request.args.get('state', 'active')
    limit = min(request.args.get('limit', 100, type=int), 500)
    offset = max(request.args.get('offset', 0, type=int), 0)

    scoped_user = None if scope == 'all' else user_id
    if state == 'recent':
        states = ['completed', 'failed', 'cancelled', 'expired']
    elif state == 'all':
        states = _ALL_STATES
    else:
        states = _TASK_STATE_GROUPS.get(state)
        if states is None:
            return jsonify({'error': f'Unknown state: {state}'}), 400

    tasks = list_jobs_by_states(
        states,
        user_id=scoped_user, is_admin=is_admin,
        limit=limit, offset=offset,
        updated_since_hours=72 if state == 'recent' else None,
    )
    counts = count_jobs_by_states(user_id=scoped_user, is_admin=is_admin)
    return jsonify({'tasks': tasks, 'counts': counts})


@app.route('/api/tasks/bulk', methods=['POST'])
@api_login_required
def bulk_task_action():
    """Apply an action to many jobs at once: cancel | approve | reject."""
    data = request.get_json() or {}
    action = data.get('action')
    ids = data.get('ids') or []
    if action not in ('cancel', 'approve', 'reject'):
        return jsonify({'error': 'action must be cancel, approve or reject'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty list'}), 400

    jm = get_job_manager()
    user_id, is_admin = _scope()
    results = []
    for raw_id in ids[:100]:
        job_id = str(raw_id)
        job = get_job(job_id, user_id=user_id, is_admin=is_admin)
        if not job:
            results.append({'id': job_id, 'ok': False, 'error': 'not found'})
            continue
        if action == 'cancel':
            ok = jm.cancel_job(job['id'])
            results.append({'id': job['id'], 'ok': bool(ok),
                            **({} if ok else {'error': 'not cancellable'})})
            continue

        upload = get_pending_upload_by_job(job['id'])
        if not upload or upload['status'] != 'pending':
            results.append({'id': job['id'], 'ok': False,
                            'error': 'no pending approval'})
            continue
        outcome = (jm.confirm_approval(upload['id'])
                   if action == 'approve' else jm.reject_approval(upload['id']))
        entry = {'id': job['id'], 'ok': bool(outcome.get('ok'))}
        if not outcome.get('ok'):
            entry['error'] = outcome.get('error')
        results.append(entry)

    succeeded = sum(1 for r in results if r['ok'])
    return jsonify({'results': results, 'succeeded': succeeded,
                    'failed': len(results) - succeeded})


@app.route('/api/jobs/<job_id>/priority', methods=['PATCH'])
@api_login_required
def set_job_priority(job_id):
    """Reposition a queued job."""
    data = request.get_json() or {}
    try:
        priority = int(data.get('priority'))
    except (TypeError, ValueError):
        return jsonify({'error': 'priority must be an integer'}), 400

    user_id, is_admin = _scope()
    job = get_job(job_id, user_id=user_id, is_admin=is_admin)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if job['status'] != 'queued':
        return jsonify({'error': 'Only queued jobs can be reordered'}), 409

    if not update_job_priority(job_id, priority,
                               user_id=user_id, is_admin=is_admin):
        return jsonify({'error': 'Job not found'}), 404
    get_job_manager().refresh_queue_positions()
    return jsonify({'job_id': job_id, 'priority': priority})


# ===== History API Endpoints =====

@app.route('/api/history', methods=['GET'])
@api_login_required
def get_history_api():
    """Get recipe history with pagination and filtering."""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status = request.args.get('status')
    search = request.args.get('search')
    
    items = get_history(limit=limit, offset=offset, status_filter=status, search=search)
    total = get_history_count(status_filter=status, search=search)
    
    return jsonify({
        'items': items,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@app.route('/api/history/<int:history_id>', methods=['GET'])
@api_login_required
def get_history_item(history_id):
    """Get a single history entry."""
    item = get_history_entry(history_id)
    if not item:
        return jsonify({'error': 'History entry not found'}), 404
    return jsonify(item)


@app.route('/api/history/<int:history_id>', methods=['DELETE'])
@api_login_required
def delete_history_item(history_id):
    """Delete a history entry."""
    result = delete_history_entry(history_id)
    if result:
        return jsonify({'status': 'deleted', 'id': history_id})
    return jsonify({'error': 'History entry not found'}), 404


@app.route('/api/history/bulk-delete', methods=['POST'])
@api_login_required
def bulk_delete_history():
    """Delete multiple history entries and/or job entries at once."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    history_ids = data.get('history_ids', [])
    job_ids = data.get('job_ids', [])
    
    if not history_ids and not job_ids:
        return jsonify({'error': 'No items to delete'}), 400
    
    deleted_history = 0
    deleted_jobs = 0
    
    if history_ids:
        deleted_history = delete_history_entries_bulk(history_ids)
    
    if job_ids:
        # Ownership gate: silently skip jobs the caller may not touch.
        user_id, is_admin = _scope()
        owned = [
            str(jid) for jid in job_ids
            if get_job(str(jid), user_id=user_id, is_admin=is_admin)
        ]
        if owned:
            deleted_jobs = delete_jobs_bulk(owned)
    
    total_deleted = deleted_history + deleted_jobs
    return jsonify({
        'status': 'deleted',
        'deleted_count': total_deleted,
        'deleted_history': deleted_history,
        'deleted_jobs': deleted_jobs
    })


@app.route('/api/recipes', methods=['GET'])
@api_login_required
def get_recipes_api():
    """Get combined recipe history and active jobs with pagination and filtering.
    
    This endpoint provides a unified view of:
    - Completed/failed recipes from history
    - In-progress jobs
    - Cancelled jobs
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status = request.args.get('status')
    search = request.args.get('search')
    
    items = get_combined_history_and_jobs(limit=limit, offset=offset, status_filter=status, search=search)
    total = get_combined_history_and_jobs_count(status_filter=status, search=search)
    
    return jsonify({
        'items': items,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@app.route('/api/jobs/<job_id>/delete', methods=['DELETE'])
@api_login_required
def delete_job_api(job_id):
    """Delete a job entry (for cancelled/failed jobs that aren't in history)."""
    user_id, is_admin = _scope()
    if not get_job(job_id, user_id=user_id, is_admin=is_admin):
        return jsonify({'error': 'Job not found'}), 404
    result = delete_job_entry(job_id)
    if result:
        return jsonify({'status': 'deleted', 'job_id': job_id})
    return jsonify({'error': 'Job not found'}), 404


def _resolve_history_image(item):
    """
    Resolve a usable image file for a history entry.

    Prefers the original file on disk, but falls back to the base64 thumbnail
    stored in the database (the on-disk copy under /tmp does not survive a
    container restart).

    Returns:
        (path, is_temp) where is_temp means the caller must delete the file.
    """
    image_path = item.get('thumbnail_path')
    if image_path and os.path.exists(image_path):
        return image_path, False

    thumbnail_data = item.get('thumbnail_data')
    if not thumbnail_data:
        return None, False

    try:
        raw = base64.b64decode(thumbnail_data)
    except (ValueError, TypeError):
        return None, False

    # Keep the .jpg suffix: both exporters derive the content type from it,
    # and thumbnails are stored as JPEG.
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(raw)
            return tmp.name, True
    except OSError:
        return None, False


@app.route('/api/history/<int:history_id>/reupload', methods=['POST'])
@api_login_required
def reupload_recipe(history_id):
    """Re-upload a recipe from history to the target."""
    from config import config
    
    item = get_history_entry(history_id)
    if not item:
        return jsonify({'error': 'History entry not found'}), 404
    
    if not item.get('recipe_data'):
        return jsonify({'error': 'No recipe data available for this entry'}), 400
    
    recipe_data = item['recipe_data']

    # Get target from request or use the first enabled one
    data = request.get_json() or {}
    enabled = get_enabled_targets()
    target = data.get('target', enabled[0] if enabled else '')

    # The original image lives under /tmp, which is not persisted across
    # container restarts. Fall back to the base64 copy kept in history so the
    # image still gets pushed. Returns (path, cleanup_flag).
    image_path, image_is_temp = _resolve_history_image(item)

    try:
        config.reload()
        image_uploaded = False

        if target == 'tandoor':
            from tandoor import Tandoor
            tandoor = Tandoor()
            result = tandoor.create_recipe(recipe_data)
            if image_path and result.get("id"):
                image_uploaded = bool(tandoor.upload_image(result["id"], image_path))
        elif target == 'mealie':
            from mealie import Mealie
            mealie = Mealie()
            result = mealie.create_recipe(recipe_data)
            recipe_slug = result.get("slug") or result.get("id")
            if image_path and recipe_slug:
                image_uploaded = bool(mealie.upload_image(recipe_slug, image_path))
        else:
            hint = ('no recipe manager is enabled — enable Mealie and/or '
                    'Tandoor in Settings') if not target else f'Unknown target: {target}'
            return jsonify({'error': hint}), 400

        message = f'Recipe re-uploaded to {target}'
        if image_path and not image_uploaded:
            message += ' (image upload failed)'
        elif not image_path:
            message += ' (no image available)'

        return jsonify({
            'status': 'success',
            'message': message,
            'target': target,
            'image_uploaded': image_uploaded,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if image_is_temp and image_path:
            try:
                os.remove(image_path)
            except OSError:
                pass


# ===== SPA Support API Endpoints =====

@app.route('/api/me', methods=['GET'])
@api_login_required
def api_me():
    # From the resolved identity rather than straight out of the cookie, so a
    # demotion applied while the user was signed in is reflected here — this is
    # what the UI keys admin-only sections off.
    identity = _current_identity()
    return jsonify({
        'user': identity['username'],
        'is_admin': identity['is_admin'],
        'auth_mode': AUTH_MODE,
        'local_auth_enabled': LOCAL_AUTH,
        # Retained so an older cached frontend bundle keeps working; there is no
        # longer a mode in which authentication is off.
        'auth_disabled': False,
        'shared_url': session.pop('shared_url', None),
        'auto_start': bool(session.pop('auto_start_extraction', False)),
    })


@app.route('/api/auth/status', methods=['GET'])
def api_auth_status():
    return jsonify({
        'auth_mode': AUTH_MODE,
        'local_auth_enabled': LOCAL_AUTH,
        'sso_enabled': oauth is not None,
        # True only before the first account exists, so a client can send the
        # user to setup instead of a sign-in form nobody can satisfy yet.
        'setup_required': _setup_required(),
        'mobile_auth_enabled': mobile_auth.mobile_auth_enabled(),
        'auth_disabled': False,
    })


# ===== Mobile (Android app) auth =====
# Two ways in, matching the two AUTH_MODEs. Under `local` the app posts a
# username and password straight to /api/mobile/auth/login. Under `authentik`
# the app cannot hold the OIDC client secret, so it opens the system browser and
# the server completes the code exchange, handing back a JWT pair over a
# custom-scheme deep link. Both are disabled unless JWT_SECRET_KEY is set.

@app.route('/api/mobile/auth/login', methods=['POST'])
def api_mobile_password_login():
    """Exchange a username and password for a token pair.

    Only under AUTH_MODE=local. Under Authentik the password, if an account even
    has one, is not what governs access — group membership is — so accepting one
    here would be a way around single sign-on.
    """
    if not mobile_auth.mobile_auth_enabled():
        return jsonify({'error': 'Mobile auth is not configured. Set JWT_SECRET_KEY.'}), 503
    if not LOCAL_AUTH:
        return jsonify({
            'error': 'This server uses single sign-on. Sign in with Authentik.',
        }), 400
    if _setup_required():
        return jsonify({
            'error': 'This server has no account yet. Open it in a browser to '
                     'finish setup.',
            'setup_required': True,
        }), 409

    payload = request.get_json(silent=True)
    source = payload if isinstance(payload, dict) else {}
    username = str(source.get('username') or '').strip()
    password = str(source.get('password') or '')

    try:
        user = _authenticate_password(username, password)
    except _LoginRejected as rejected:
        response = jsonify({'error': rejected.message})
        if rejected.retry_after is not None:
            response.headers['Retry-After'] = str(rejected.retry_after)
        return response, rejected.status

    auth_log.info('app sign-in: %s from %s', user['username'], _client_ip())
    return jsonify(mobile_auth.issue_token_pair(
        user['username'], is_admin=bool(user['is_admin'])))


@app.route('/api/mobile/auth/login-url', methods=['GET'])
def api_mobile_login_url():
    """Start app sign-in: return the Authentik URL to open in the browser."""
    if not mobile_auth.mobile_auth_enabled():
        return jsonify({'error': 'Mobile auth is not configured. Set JWT_SECRET_KEY.'}), 503
    if oauth is None:
        return jsonify({'error': 'Single sign-on is not configured on this server.'}), 503

    redirect_uri = (request.args.get('redirect') or '').strip()
    if not _is_allowed_deep_link(redirect_uri):
        return jsonify({'error': 'redirect must use an allowed app scheme'}), 400

    nonce = secrets.token_urlsafe(32)
    if not save_mobile_nonce(nonce, redirect_uri):
        return jsonify({'error': 'Could not start sign-in. Please try again.'}), 500

    meta = oauth.authentik.load_server_metadata()
    params = {
        'response_type': 'code',
        'client_id': os.environ['AUTHENTIK_CLIENT_ID'],
        'redirect_uri': _authentik_redirect_uri(),
        'scope': 'openid email profile groups',
        'state': nonce,
    }
    return jsonify({
        'auth_url': f"{meta['authorization_endpoint']}?{urlencode(params)}",
        'state': nonce,
    })


@app.route('/api/mobile/auth/refresh', methods=['POST'])
def api_mobile_refresh():
    """Exchange a valid refresh token for a fresh token pair."""
    if not mobile_auth.mobile_auth_enabled():
        return jsonify({'error': 'Mobile auth is not configured. Set JWT_SECRET_KEY.'}), 503

    body = request.get_json(silent=True) or {}
    payload = mobile_auth.decode_token(body.get('refresh_token') or '', expected_type='refresh')
    if payload is None:
        return jsonify({'error': 'Invalid or expired refresh token'}), 401

    # Re-read the user so revoked accounts and changed admin rights take effect
    # on refresh rather than persisting for the life of the refresh token.
    user = get_user(payload.get('sub') or '')
    if not user:
        return jsonify({'error': 'Invalid or expired refresh token'}), 401

    return jsonify(mobile_auth.issue_token_pair(
        user['username'], is_admin=bool(user['is_admin'])))


@app.route('/api/mobile/me', methods=['GET'])
def api_mobile_me():
    """Identity behind the presented Bearer token."""
    identity = _bearer_identity_safe()
    if identity is None:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify({'username': identity['username'], 'is_admin': identity['is_admin']})


# ===== User administration =====
#
# Local mode only. Under Authentik the identity provider owns accounts: it
# decides who exists, group membership decides who administers, and both are
# reapplied at every sign-in — so creating an account here would be unusable
# (password sign-in is refused), and deleting one would not keep anyone out.

_LOCAL_ONLY = ('User administration is managed by your identity provider in '
               'this mode.')


def _public_user(user: dict | None) -> dict | None:
    """An account as the API exposes it: never the password hash."""
    if user is None:
        return None
    return {
        'username': user['username'],
        'email': user.get('email'),
        'name': user.get('name'),
        'is_admin': bool(user['is_admin']),
        'is_oidc': user.get('oidc_sub') is not None,
        'has_password': bool(user.get('password_hash')),
        'created_at': user.get('created_at'),
    }


def _users_payload():
    # SQLite hands back 0/1 for the boolean expressions in the query; coerce so
    # clients get real booleans rather than numbers that are truthy either way.
    users = [
        {**row,
         'is_admin': bool(row['is_admin']),
         'is_oidc': bool(row['is_oidc']),
         'has_password': bool(row['has_password'])}
        for row in list_users()
    ]
    return jsonify({'users': users, 'admin_count': count_admins()})


@app.route('/api/users', methods=['GET'])
@api_admin_required
def api_list_users():
    return _users_payload()


@app.route('/api/users', methods=['POST'])
@api_admin_required
def api_create_user():
    """Add an account. Only an admin can; there is no self-registration."""
    if not LOCAL_AUTH:
        return jsonify({'error': _LOCAL_ONLY}), 400

    body = request.get_json(silent=True) or {}
    username = str(body.get('username') or '').strip()
    password = str(body.get('password') or '')
    is_admin = bool(body.get('is_admin'))

    problem = _validate_username(username)
    if problem:
        return jsonify({'error': problem}), 400
    try:
        passwords.validate(password)
    except passwords.WeakPassword as exc:
        return jsonify({'error': str(exc)}), 400

    user = create_local_user(
        username, passwords.hash_password(password), is_admin=is_admin
    )
    if user is None:
        return jsonify({'error': f'An account named {username} already exists.'}), 409

    auth_log.info('account created: %s (admin=%s) by %s',
                  username, is_admin, _current_username())
    return jsonify({'user': _public_user(user)}), 201


@app.route('/api/users/<username>', methods=['PATCH'])
@api_admin_required
def api_update_user(username: str):
    """Change another account's admin rights or password."""
    if not LOCAL_AUTH:
        return jsonify({'error': _LOCAL_ONLY}), 400

    user = get_user(username)
    if user is None:
        return jsonify({'error': 'No such account.'}), 404

    body = request.get_json(silent=True) or {}
    actor = _current_username()

    if 'is_admin' in body:
        is_admin = bool(body['is_admin'])
        # Demoting yourself is how an instance ends up with no admin at all,
        # and you would lose the rights needed to undo it.
        if username == actor and not is_admin:
            return jsonify({'error': 'You cannot remove your own admin rights.'}), 400
        if not is_admin and bool(user['is_admin']) and count_admins() <= 1:
            return jsonify({'error': 'The last admin cannot be demoted.'}), 400
        set_user_admin(username, is_admin)

    if 'password' in body:
        password = str(body.get('password') or '')
        try:
            passwords.validate(password)
        except passwords.WeakPassword as exc:
            return jsonify({'error': str(exc)}), 400
        set_user_password(username, passwords.hash_password(password))
        # Their sessions keep working: the cookie is checked against the account
        # existing, not against the password. Signing them out here would be
        # security theatre if they are the ones who asked for the reset, and a
        # nuisance if they are not.
        auth_log.info('password reset for %s by %s', username, actor)

    return jsonify({'user': _public_user(get_user(username))})


@app.route('/api/users/<username>', methods=['DELETE'])
@api_admin_required
def api_delete_user(username: str):
    """Remove an account, along with the jobs and uploads it owns."""
    if not LOCAL_AUTH:
        return jsonify({'error': _LOCAL_ONLY}), 400

    actor = _current_username()
    user = get_user(username)
    if user is None:
        return jsonify({'error': 'No such account.'}), 404
    if username == actor:
        return jsonify({'error': 'You cannot delete your own account.'}), 400
    if bool(user['is_admin']) and count_admins() <= 1:
        return jsonify({'error': 'The last admin cannot be deleted.'}), 400

    delete_user(username)
    auth_log.info('account deleted: %s by %s', username, actor)
    return jsonify({'status': 'deleted', 'username': username})


@app.route('/api/me/password', methods=['POST'])
@api_login_required
def api_change_own_password():
    """Change your own password, proving you know the current one.

    Requiring the current password means a borrowed session or a stolen cookie
    cannot be used to lock the real owner out of their account.
    """
    if not LOCAL_AUTH:
        return jsonify({'error': 'Passwords are managed by your identity '
                                 'provider in this mode.'}), 400

    username = _current_username()
    user = get_user(username) if username else None
    if user is None:
        return jsonify({'error': 'Authentication required'}), 401

    body = request.get_json(silent=True) or {}
    current = str(body.get('current_password') or '')
    new_password = str(body.get('new_password') or '')

    throttle_key = f'{_client_ip()}|{(username or "").lower()}|change'
    wait = login_throttle.retry_after(throttle_key)
    if wait > 0:
        return jsonify({
            'error': f'Too many attempts. Try again in {int(wait) + 1} seconds.'
        }), 429

    if not passwords.verify(user.get('password_hash'), current):
        login_throttle.record_failure(throttle_key)
        return jsonify({'error': 'Current password is incorrect.'}), 403
    login_throttle.reset(throttle_key)

    try:
        passwords.validate(new_password)
    except passwords.WeakPassword as exc:
        return jsonify({'error': str(exc)}), 400

    set_user_password(user['username'], passwords.hash_password(new_password))
    auth_log.info('password changed by %s', user['username'])
    return jsonify({'status': 'changed'})


# Configuration is instance-wide and holds the LLM, Mealie and Tandoor API keys,
# so reading it is as sensitive as writing it: a non-admin who could GET this
# would walk away with the keys. Admin-only in both directions.
@app.route('/api/config', methods=['GET'])
@api_admin_required
def api_get_config():
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
@api_admin_required
def api_post_config():
    from config import DEFAULT_CONFIG
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'JSON object required'}), 400
    valid_keys = set(DEFAULT_CONFIG.keys())
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    if not filtered:
        return jsonify({'error': 'No valid configuration keys provided'}), 400
    current = load_config()
    current.update(filtered)
    save_config(current)
    return jsonify({'status': 'success', 'saved_keys': list(filtered.keys())})


# ===== Settings Export/Import API Endpoints =====

@app.route('/api/settings/export', methods=['GET'])
@api_admin_required
def export_settings():
    """Export all settings as JSON for backup/transfer."""
    config = load_config()
    
    # Create export data with metadata
    export_data = {
        'version': '1.0',
        'exported_at': datetime.now().isoformat(),
        'settings': config
    }
    
    return jsonify(export_data)


@app.route('/api/settings/import', methods=['POST'])
@api_admin_required
def import_settings():
    """Import settings from a JSON backup file."""
    from config import DEFAULT_CONFIG
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # Handle both direct settings and wrapped format
    if 'settings' in data:
        settings = data['settings']
    else:
        settings = data
    
    # Validate that we have a dictionary
    if not isinstance(settings, dict):
        return jsonify({'error': 'Invalid settings format'}), 400
    
    # Only import known configuration keys
    valid_keys = set(DEFAULT_CONFIG.keys())
    filtered_settings = {k: v for k, v in settings.items() if k in valid_keys}
    
    if not filtered_settings:
        return jsonify({'error': 'No valid settings found in import data'}), 400
    
    # Save the imported settings
    current_config = load_config()
    current_config.update(filtered_settings)
    save_config(current_config)
    
    return jsonify({
        'status': 'success',
        'message': f'Imported {len(filtered_settings)} settings',
        'imported_keys': list(filtered_settings.keys())
    })


# Cookies authenticate yt-dlp as whoever exported them, and the path is written
# into instance-wide config, so this is an admin operation like the rest of it.
@app.route('/api/cookies/upload', methods=['POST'])
@api_admin_required
def upload_cookies_file():
    """Upload a cookies.txt file for yt-dlp authentication.
    
    Saves the uploaded file to the data directory and updates the config.
    """
    if 'cookies_file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['cookies_file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Validate file extension
    if not file.filename.endswith('.txt'):
        return jsonify({'error': 'File must be a .txt file'}), 400
    
    # Read and validate content looks like a cookies file
    content = file.read().decode('utf-8', errors='ignore')
    
    # Basic validation: Netscape cookies files typically start with a comment
    # or have tab-separated values with domain names
    if not content.strip():
        return jsonify({'error': 'File is empty'}), 400
    
    # Check for basic cookies file structure (domain, flag, path, secure, expiration, name, value)
    lines = content.strip().split('\n')
    valid_lines = 0
    for line in lines:
        line = line.strip()
        if line.startswith('#') or not line:
            continue  # Comment or empty line
        parts = line.split('\t')
        if len(parts) >= 7:
            valid_lines += 1
    
    if valid_lines == 0:
        return jsonify({'error': 'File does not appear to be a valid Netscape cookies.txt format'}), 400
    
    # Save the file to the data directory
    from config import DATA_DIR
    cookies_path = os.path.join(DATA_DIR, 'cookies.txt')
    
    try:
        with open(cookies_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except IOError as e:
        return jsonify({'error': f'Failed to save cookies file: {str(e)}'}), 500
    
    # Update the configuration
    config = load_config()
    config['yt_dlp_cookies_file'] = cookies_path
    save_config(config)
    
    return jsonify({
        'status': 'success',
        'message': f'Cookies file uploaded ({valid_lines} cookies found)',
        'path': cookies_path
    })


@app.route('/api/cookies/delete', methods=['DELETE'])
@api_admin_required
def delete_cookies_file():
    """Delete the uploaded cookies file."""
    from config import DATA_DIR
    
    cookies_path = os.path.join(DATA_DIR, 'cookies.txt')
    
    if os.path.exists(cookies_path):
        try:
            os.remove(cookies_path)
        except IOError as e:
            return jsonify({'error': f'Failed to delete cookies file: {str(e)}'}), 500
    
    # Clear the configuration
    config = load_config()
    config['yt_dlp_cookies_file'] = ''
    save_config(config)
    
    return jsonify({
        'status': 'success',
        'message': 'Cookies file deleted'
    })


# ===== Pending Uploads API Endpoints =====

@app.route('/api/pending-uploads', methods=['GET'])
@api_login_required
def get_pending_uploads_api():
    """Get pending recipe uploads waiting for confirmation, scoped to the
    requesting user (admins see all).

    This allows any device/session of the owner to see pending uploads and
    confirm/cancel them.
    """
    # Clean up expired uploads first
    cleanup_expired_pending_uploads()
    jm = get_job_manager()
    user_id, is_admin = _scope()
    pending = jm.get_approvals(user_id=user_id, is_admin=is_admin)
    
    # Prepare response with image data for each pending upload
    results = []
    for upload in pending:
        item = {
            'upload_id': upload['id'],
            'job_id': upload['job_id'],
            'recipe': upload['recipe_data'],
            'output_target': _display_target(upload.get('output_target')),
            'best_image_index': upload.get('best_image_index', 0),
            'selected_image_index': upload.get('selected_image_index', 0),
            'url': upload.get('url'),
            'video_title': upload.get('video_title'),
            'created_at': upload.get('created_at'),
            'expires_at': upload.get('expires_at'),
        }
        
        # Load image data if available
        image_path = upload.get('image_path')
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
        
        # Load candidate images
        image_candidates = upload.get('image_candidates') or []
        candidate_images_data = []
        for idx, candidate_path in enumerate(image_candidates):
            if os.path.exists(candidate_path):
                with open(candidate_path, 'rb') as f:
                    candidate_images_data.append({
                        'index': idx,
                        'data': base64.b64encode(f.read()).decode('utf-8'),
                        'path': candidate_path,
                        'is_best': idx == upload.get('best_image_index', 0)
                    })
        item['candidate_images'] = candidate_images_data

        results.append(item)

    return jsonify({'pending_uploads': results})


@app.route('/api/pending-uploads/<upload_id>', methods=['GET'])
@api_login_required
def get_pending_upload_api(upload_id):
    """Get a specific pending upload by ID."""
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload or upload['status'] != 'pending':
        return jsonify({'error': 'Pending upload not found'}), 404
    
    item = {
        'upload_id': upload['id'],
        'job_id': upload['job_id'],
        'recipe': upload['recipe_data'],
        'output_target': _display_target(upload.get('output_target')),
        'best_image_index': upload.get('best_image_index', 0),
        'selected_image_index': upload.get('selected_image_index', 0),
        'created_at': upload.get('created_at'),
        'expires_at': upload.get('expires_at'),
    }
    
    # Load image data if available
    image_path = upload.get('image_path')
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
    
    # Load candidate images
    image_candidates = upload.get('image_candidates') or []
    candidate_images_data = []
    for idx, candidate_path in enumerate(image_candidates):
        if candidate_path and os.path.exists(candidate_path):
            with open(candidate_path, 'rb') as f:
                candidate_images_data.append({
                    'index': idx,
                    'data': base64.b64encode(f.read()).decode('utf-8'),
                    'path': candidate_path,
                    'is_best': idx == upload.get('best_image_index', 0)
                })
    item['candidate_images'] = candidate_images_data

    return jsonify(item)


@app.route('/api/pending-uploads/<upload_id>/confirm', methods=['POST'])
@api_login_required
def confirm_pending_upload_api(upload_id):
    """Confirm a pending upload via REST API (works from any device/session)."""
    data = request.get_json() or {}
    selected_image_index = data.get('selected_image_index')

    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404

    jm = get_job_manager()
    result = jm.confirm_approval(upload_id, selected_image_index)
    if not result.get('ok'):
        return jsonify({'error': result.get('error', 'Already processed')}), 404
    return jsonify({'status': 'confirmed', 'upload_id': upload_id,
                    'job_id': result.get('job_id')})


@app.route('/api/pending-uploads/<upload_id>/cancel', methods=['POST'])
@api_login_required
def cancel_pending_upload_api(upload_id):
    """Cancel a pending upload via REST API (works from any device/session)."""
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        return jsonify({'error': 'Pending upload not found or already processed'}), 404

    jm = get_job_manager()
    result = jm.reject_approval(upload_id)
    if not result.get('ok'):
        return jsonify({'error': result.get('error', 'Already processed')}), 404
    return jsonify({'status': 'cancelled', 'upload_id': upload_id,
                    'job_id': result.get('job_id')})


# ===== Legacy API (kept for backward compatibility) =====

@app.route('/api/push/subscribe', methods=['POST'])
@api_login_required
def push_subscribe():
    data = request.get_json() or {}
    sub = data.get('subscription') or data
    endpoint = sub.get('endpoint')
    keys = sub.get('keys') or {}
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'error': 'Invalid subscription'}), 400
    ok = save_push_subscription(session['user'], endpoint, keys['p256dh'], keys['auth'])
    return jsonify({'status': 'subscribed' if ok else 'error'})


@app.route('/api/push/unsubscribe', methods=['POST'])
@api_login_required
def push_unsubscribe():
    data = request.get_json() or {}
    endpoint = data.get('endpoint')
    if endpoint:
        delete_push_subscription(endpoint)
    return jsonify({'status': 'unsubscribed'})


@app.route('/api/process', methods=['POST'])
@api_login_required
def process_video():
    """Start video processing (legacy endpoint - redirects to job system)."""
    data = request.get_json() or {}
    url = data.get('url', '')
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    result = _start_job_for_url(url)
    return jsonify({'status': 'started', 'message': 'Processing started', **result})


def _emit_preview_payload(payload: dict) -> None:
    """Emit a recipe preview only to the owning user's rooms."""
    rooms = [f"job_{payload.get('job_id')}"]
    owner = payload.get('owner')
    if owner:
        rooms.append(f'user_{owner}')
    for room in rooms:
        socketio.emit('recipe_preview', payload, room=room)


def _display_target(raw: str | None) -> str:
    """Pretty label for a stored output_target (legacy keys or new labels)."""
    from uploaders import TARGET_LABELS
    if not raw:
        return 'recipe manager'
    return TARGET_LABELS.get(raw.strip().lower(), raw)


def _build_preview_payload(upload: dict) -> dict:
    """Reconstruct a recipe_preview payload from a stored pending upload."""
    item = {
        'job_id': upload['job_id'],
        'upload_id': upload['id'],
        'recipe': upload.get('recipe_data'),
        'image_data': None,
        'candidate_images': [],
        'best_image_index': upload.get('best_image_index', 0),
        'output_target': _display_target(upload.get('output_target')),
        'owner': upload.get('user_id'),
    }
    image_path = upload.get('image_path')
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            item['image_data'] = base64.b64encode(f.read()).decode('utf-8')
    for idx, candidate_path in enumerate(upload.get('image_candidates') or []):
        if candidate_path and os.path.exists(candidate_path):
            with open(candidate_path, 'rb') as f:
                item['candidate_images'].append({
                    'index': idx,
                    'data': base64.b64encode(f.read()).decode('utf-8'),
                    'path': candidate_path,
                    'is_best': idx == upload.get('best_image_index', 0),
                })
    return item


def resume_upload_job(job_id: str, jm) -> None:
    """Upload-phase worker: push an approved artifact to its targets."""
    upload = get_pending_upload_by_job(job_id)
    job = get_job(job_id)
    if not upload or not job or job['status'] != 'uploading':
        return

    recipe_data = upload.get('recipe_data')
    if not recipe_data:
        jm.fail_job(job_id, 'Approved recipe artifact is missing or corrupt')
        return

    jm.update_progress(job_id, 'upload',
                       f"Uploading to {upload.get('output_target') or 'recipe manager'}...", 95)

    candidates = upload.get('image_candidates') or []
    selected = upload.get('selected_image_index', upload.get('best_image_index', 0)) or 0
    image_path = upload.get('image_path')
    if candidates and 0 <= selected < len(candidates):
        image_path = candidates[selected]

    target_count = len(get_enabled_targets())
    final_target, failures = upload_recipe_to_targets(recipe_data, image_path)

    if failures and len(failures) == target_count:
        msgs = '; '.join(f"{t}: {msg}" for t, msg in failures)
        jm.fail_job(job_id, f'All uploads failed: {msgs}')
        return

    if failures:
        msgs = '; '.join(f"{t}: {msg}" for t, msg in failures)
        jm.update_progress(job_id, 'complete',
                           f'Uploaded to {final_target}. Failed: {msgs}', 100)
    else:
        jm.update_progress(job_id, 'complete',
                           f'Recipe uploaded successfully to {final_target}!', 100)

    jm.complete_job(job_id, recipe_data, image_path, final_target)


def process_video_job(job_id, jm):
    """Background task — delegates to shared pipeline module."""
    from pipeline import (
        PipelineStats,
        PreviewWaiter,
        run_url_pipeline,
    )
    from config import config as app_config

    job = get_job(job_id)
    if not job:
        return

    stats = PipelineStats()

    class Reporter:
        def is_cancelled(self):
            return jm.is_cancelled(job_id)

        def update(self, stage, message, percent, video_title=None):
            jm.update_progress(job_id, stage, message, percent, video_title)

    reporter = Reporter()

    preview = None
    if app_config.CONFIRM_BEFORE_UPLOAD:
        def emit_preview(payload):
            payload['owner'] = job.get('user_id')
            _emit_preview_payload(payload)

        preview = PreviewWaiter(
            job_id=job_id,
            target_label=format_targets(get_enabled_targets()),
            emit_preview=emit_preview,
            open_approval_fn=lambda **kw: jm.open_approval(**kw),
            is_cancelled=reporter.is_cancelled,
        )

    result = run_url_pipeline(job['url'], reporter, stats=stats, preview=preview)

    if result.awaiting_approval:
        # Slot-free approval: worker returns; the slot is now free and the
        # upload happens in a fresh worker once the user approves.
        return
    if result.error == 'cancelled':
        return
    if result.error:
        jm.fail_job(job_id, result.error, stats.llm_tokens_estimate)
        return

    jm.complete_job(
        job_id,
        result.recipe_data,
        result.image_path,
        result.output_target,
        llm_tokens=result.llm_tokens_estimate or stats.llm_tokens_estimate,
    )


# ===== WebSocket Handlers =====

@socketio.on('connect')
def handle_connect():
    if not _socketio_authenticated():
        return False
    join_room(f"user_{_current_username()}")
    emit('connected', {'status': 'Connected to server'})


@socketio.on('subscribe_job')
def handle_subscribe_job(data):
    if not _socketio_authenticated():
        return False
    job_id = data.get('job_id')
    if job_id:
        user_id, is_admin = _scope()
        job = get_job(job_id, user_id=user_id, is_admin=is_admin)
        if not job:
            emit('error', {'message': 'Job not found'})
            return
        join_room(f'job_{job_id}')
        emit('subscribed', {'job_id': job_id, 'status': 'subscribed'})


@socketio.on('unsubscribe_job')
def handle_unsubscribe_job(data):
    if not _socketio_authenticated():
        return False
    job_id = data.get('job_id')
    if job_id:
        leave_room(f'job_{job_id}')
        emit('unsubscribed', {'job_id': job_id, 'status': 'unsubscribed'})


@socketio.on('confirm_upload')
def handle_confirm_upload(data):
    if not _socketio_authenticated():
        return False
    upload_id = data.get('upload_id')
    selected_image_index = data.get('selected_image_index')
    if not upload_id:
        return
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        emit('error', {'message': 'Pending upload not found'})
        return
    get_job_manager().confirm_approval(upload_id, selected_image_index)


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    if not _socketio_authenticated():
        return False
    upload_id = data.get('upload_id')
    if not upload_id:
        return
    user_id, is_admin = _scope()
    upload = get_pending_upload(upload_id, user_id=user_id, is_admin=is_admin)
    if not upload:
        emit('error', {'message': 'Pending upload not found'})
        return
    get_job_manager().reject_approval(upload_id)


# Register pipeline handler and restore queued jobs from DB
job_manager.set_process_func(process_video_job)
job_manager.set_resume_func(resume_upload_job)


def _reemit_parked_previews() -> None:
    """After a restart, re-emit previews for jobs parked in awaiting_approval."""
    jm = get_job_manager()
    try:
        for upload in jm.get_approvals():
            _emit_preview_payload(_build_preview_payload(upload))
    except Exception as exc:
        print(f'[Jobs] failed to re-emit parked previews: {exc}')


_reemit_parked_previews()


# ===== SPA Catch-All Route =====
# Must be registered AFTER every explicit route so those keep precedence.

@app.route('/<path:path>')
def spa_catch_all(path):
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    full_path = os.path.join(FRONTEND_DIST, path)
    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIST, path)
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return jsonify({'error': 'Not found'}), 404


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    socketio.run(app, debug=debug, host=host, port=port, allow_unsafe_werkzeug=True, use_reloader=debug)
