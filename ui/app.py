"""
Social Recipes Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
Supports parallel job processing with progress persistence.
"""

import os
import sys
import base64
import secrets
import threading
import json
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room

from database import (
    init_db, load_config, save_config,
    verify_user, update_password, hash_password,
    get_history, get_history_entry, get_history_count, delete_history_entry,
    get_job, get_active_jobs
)
from job_manager import init_job_manager, get_job_manager

app = Flask(__name__)

# Serve manifest.json with correct MIME type and headers for PWA
@app.route('/manifest.json')
def serve_manifest():
    response = make_response(app.send_static_file('manifest.json'))
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

# Serve service worker with correct MIME type and scope
@app.route('/sw.js')
def serve_sw():
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

app.secret_key = _get_or_create_secret_key()

# Configure session cookie settings
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
    days=30)  # Remember for 30 days

# Use threading mode instead of eventlet to avoid monkey-patching issues with SSL/requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize database
init_db()

# Initialize job manager
job_manager = init_job_manager(socketio)

# Store pending recipe uploads waiting for confirmation
# Key: upload_id, Value: {'recipe': recipe_data, 'image_path': path, 'event': threading.Event(), 'confirmed': bool, 'job_id': str}
pending_uploads = {}


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def api_login_required(f):
    """Decorator to require login for API routes (returns JSON error)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
@login_required
def index():
    """Main page with URL input and progress display."""
    # Check for shared URL from multiple sources:
    # 1. Session (set by /share route for POST requests)
    # 2. shared_url query param (from service worker redirect)
    # 3. shared_text query param (from service worker redirect)
    # 4. url/text query params (legacy/direct)
    shared_url = (
        session.pop('shared_url', None) or
        request.args.get('shared_url') or
        request.args.get('shared_text') or
        request.args.get('url') or
        request.args.get('text') or
        ''
    )
    
    # Extract URL from shared text if needed (apps like TikTok share URLs in text)
    if shared_url and not shared_url.startswith('http'):
        import re
        url_match = re.search(r'(https?://[^\s]+)', shared_url)
        if url_match:
            shared_url = url_match.group(1)
    
    return render_template('index.html', shared_url=shared_url)


@app.route('/history')
@login_required
def history():
    """History page showing all past recipe extractions."""
    return render_template('history.html')


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
    
    # If user is not logged in, redirect to login (URL is preserved in session)
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # User is logged in, redirect to main page
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember_me = request.form.get('remember_me') == 'on'

        if verify_user(username, password):
            session['user'] = username
            # Make session permanent if "Remember Me" is checked
            if remember_me:
                session.permanent = True
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session."""
    session.pop('user', None)
    flash('You have been logged out.', 'info')
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
        config['output_target'] = request.form.get('output_target', 'tandoor')
        config['export_to_both'] = 'true' if request.form.get('export_to_both') else 'false'
        config['whisper_model'] = request.form.get('whisper_model', 'small')
        config['hf_token'] = request.form.get('hf_token', '')
        # Checkbox: present in form data only when checked
        config['confirm_before_upload'] = 'true' if request.form.get(
            'confirm_before_upload') else 'false'

        save_config(config)
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', config=config)


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password."""
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    username = session['user']

    if not verify_user(username, current_password):
        flash('Current password is incorrect', 'error')
    elif new_password != confirm_password:
        flash('New passwords do not match', 'error')
    elif len(new_password) < 6:
        flash('Password must be at least 6 characters', 'error')
    else:
        update_password(username, new_password)
        flash('Password changed successfully!', 'success')

    return redirect(url_for('settings'))


# ===== Job API Endpoints =====

@app.route('/api/jobs', methods=['POST'])
@api_login_required
def create_job():
    """Create a new analysis job."""
    data = request.get_json()
    url = data.get('url', '')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    # Create job and start processing
    jm = get_job_manager()
    job_id = jm.create_new_job(url)
    
    # Start the job processing
    jm.start_job(job_id, process_video_job)
    
    # Get job info
    job = get_job(job_id)
    
    return jsonify({
        'job_id': job_id,
        'status': 'pending',
        'url': url,
        'message': 'Job created and processing started'
    })


@app.route('/api/jobs', methods=['GET'])
@api_login_required
def list_jobs():
    """List all active jobs."""
    jobs = get_active_jobs()
    return jsonify({'jobs': jobs})


@app.route('/api/jobs/<job_id>', methods=['GET'])
@api_login_required
def get_job_status(job_id):
    """Get status of a specific job."""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@api_login_required
def cancel_job_api(job_id):
    """Cancel a running job."""
    jm = get_job_manager()
    result = jm.cancel_job(job_id)
    if result:
        return jsonify({'status': 'cancelled', 'job_id': job_id})
    return jsonify({'error': 'Job not found or already completed'}), 404


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
    image_path = item.get('thumbnail_path')
    
    # Get target from request or use default
    data = request.get_json() or {}
    target = data.get('target', config.OUTPUT_TARGET)
    
    try:
        config.reload()
        
        if target == 'tandoor':
            from tandoor import Tandoor
            tandoor = Tandoor()
            result = tandoor.create_recipe(recipe_data)
            if image_path and os.path.exists(image_path) and result.get("id"):
                tandoor.upload_image(result["id"], image_path)
        elif target == 'mealie':
            from mealie import Mealie
            mealie = Mealie()
            result = mealie.create_recipe(recipe_data)
            recipe_slug = result.get("slug") or result.get("id")
            if image_path and os.path.exists(image_path) and recipe_slug:
                mealie.upload_image(recipe_slug, image_path)
        else:
            return jsonify({'error': f'Unknown target: {target}'}), 400
        
        return jsonify({
            'status': 'success',
            'message': f'Recipe re-uploaded to {target}',
            'target': target
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== Settings Export/Import API Endpoints =====

@app.route('/api/settings/export', methods=['GET'])
@api_login_required
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
@api_login_required
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


# ===== Legacy API (kept for backward compatibility) =====

@app.route('/api/process', methods=['POST'])
@api_login_required
def process_video():
    """Start video processing (legacy endpoint - redirects to job system)."""
    data = request.get_json()
    url = data.get('url', '')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    # Create job using new system
    jm = get_job_manager()
    job_id = jm.create_new_job(url)
    jm.start_job(job_id, process_video_job)
    
    return jsonify({
        'status': 'started',
        'message': 'Processing started',
        'job_id': job_id
    })


def process_video_job(job_id, jm):
    """Background task to process video with job-based progress updates."""
    job = get_job(job_id)
    if not job:
        return
    
    url = job['url']
    
    try:
        # Import modules - they will read config from SQLite via config.py
        from config import config
        from video_downloader import VideoDownloader
        from transcriber import Transcriber
        from chef import Chef
        from image_extractor import extract_dish_image_candidates

        # Reload config to get latest values from database
        config.reload()

        # Check for cancellation
        if jm.is_cancelled(job_id):
            return

        # Step 1: Get video info
        jm.update_progress(job_id, 'info', 'Fetching video information...', 10)
        downloader = VideoDownloader(url)
        item = downloader._get_info()
        description = item.get("description", "No description available.")
        title = item.get("title", "Untitled")
        jm.update_progress(job_id, 'info', f'Video: {title}', 15, video_title=title)

        if jm.is_cancelled(job_id):
            return

        # Step 2: Download video
        jm.update_progress(job_id, 'download', 'Downloading video...', 20)
        vid_id, video_path = downloader._download_video()
        if vid_id is None:
            jm.fail_job(job_id, 'Failed to download video')
            return
        dish_dir = os.path.join("/tmp", vid_id)
        jm.update_progress(job_id, 'download', 'Video downloaded successfully', 30)

        if jm.is_cancelled(job_id):
            return

        # Step 3: Transcribe audio
        jm.update_progress(job_id, 'transcribe', 'Transcribing audio...', 35)
        
        transcriber = Transcriber(video_path)
        lang = config.TARGET_LANGUAGE

        audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")
        if os.path.exists(audio_cache):
            jm.update_progress(job_id, 'transcribe', 'Using cached transcription', 40)
            with open(audio_cache, "r") as f:
                transcription = f.read()
        else:
            transcription = transcriber.transcribe()
            with open(audio_cache, "w") as f:
                f.write(transcription)
        jm.update_progress(job_id, 'transcribe', 'Audio transcribed', 50)

        if jm.is_cancelled(job_id):
            return

        # Step 4: Extract visual text
        jm.update_progress(job_id, 'visual', 'Extracting on-screen text...', 55)
        visual_text = ""
        visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
        if os.path.exists(visual_cache):
            jm.update_progress(job_id, 'visual', 'Using cached visual text', 60)
            with open(visual_cache, "r") as f:
                visual_text = f.read()
        else:
            try:
                visual_text = transcriber.extract_visual_text()
                with open(visual_cache, "w") as f:
                    f.write(visual_text)
            except Exception as e:
                jm.update_progress(job_id, 'visual', f'Warning: Could not extract visual text: {e}', 60)
        jm.update_progress(job_id, 'visual', 'Visual text extracted', 65)

        if jm.is_cancelled(job_id):
            return

        # Combine transcription
        combined_transcription = transcription
        if visual_text:
            combined_transcription = f"""=== AUDIO TRANSCRIPTION ===
{transcription}

=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===
{visual_text}"""

        # Step 5: Extract dish image candidates
        jm.update_progress(job_id, 'image', 'Extracting dish image candidates...', 70)
        image_path = None
        image_candidates = []
        best_image_index = 0
        image_cache = os.path.join(dish_dir, "dish.jpg")
        frames_dir = os.path.join(dish_dir, "dish_frames")
        
        # Check if we have cached candidates
        if os.path.exists(frames_dir) and os.path.exists(image_cache):
            jm.update_progress(job_id, 'image', 'Using cached dish images', 75)
            image_path = image_cache
            # Load all cached candidate images
            candidate_files = sorted([
                os.path.join(frames_dir, f) for f in os.listdir(frames_dir)
                if f.startswith('dish_candidate_') and f.endswith('.jpg')
            ])
            image_candidates = candidate_files
        else:
            try:
                result = extract_dish_image_candidates(video_path)
                image_path = result.get('best_image')
                image_candidates = result.get('candidates', [])
                best_image_index = result.get('best_index', 0)
            except Exception as e:
                jm.update_progress(job_id, 'image', f'Warning: Could not extract image: {e}', 75)
        jm.update_progress(job_id, 'image', 'Image candidates extracted', 80)

        if jm.is_cancelled(job_id):
            return

        # Step 6: Create recipe with AI
        jm.update_progress(job_id, 'evaluate', 'Creating recipe with AI...', 85)
        chef = Chef(source_url=url, description=description,
                    transcription=combined_transcription)
        recipe_data = chef.create_recipe()

        if not recipe_data:
            jm.fail_job(job_id, 'Failed to create recipe')
            return

        jm.update_progress(job_id, 'evaluate', 'Recipe created successfully', 90)

        if jm.is_cancelled(job_id):
            return

        # Step 7: Upload to target (with optional preview confirmation)
        if config.CONFIRM_BEFORE_UPLOAD:
            # Show preview and wait for user confirmation
            jm.update_progress(job_id, 'preview', 'Waiting for your confirmation...', 90)

            # Prepare image data for preview if available (best image first, then candidates)
            image_data = None
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode('utf-8')
            
            # Prepare all candidate images as base64
            candidate_images_data = []
            for idx, candidate_path in enumerate(image_candidates):
                if os.path.exists(candidate_path):
                    with open(candidate_path, 'rb') as f:
                        candidate_images_data.append({
                            'index': idx,
                            'data': base64.b64encode(f.read()).decode('utf-8'),
                            'path': candidate_path,
                            'is_best': idx == best_image_index
                        })

            # Create event for waiting (using threading.Event)
            confirm_event = threading.Event()
            upload_id = secrets.token_hex(16)

            pending_uploads[upload_id] = {
                'recipe': recipe_data,
                'image_path': image_path,
                'image_candidates': image_candidates,
                'output_target': config.OUTPUT_TARGET,
                'event': confirm_event,
                'confirmed': None,
                'selected_image_index': best_image_index,
                'job_id': job_id
            }

            # Determine display target for preview
            if config.EXPORT_TO_BOTH:
                display_target = 'Tandoor & Mealie'
            else:
                display_target = config.OUTPUT_TARGET.capitalize()

            # Send preview to client with all candidate images
            socketio.emit('recipe_preview', {
                'job_id': job_id,
                'upload_id': upload_id,
                'recipe': recipe_data,
                'image_data': image_data,
                'candidate_images': candidate_images_data,
                'best_image_index': best_image_index,
                'output_target': display_target,
                'export_to_both': config.EXPORT_TO_BOTH
            }, room=f'job_{job_id}')
            
            # Also broadcast for legacy clients
            socketio.emit('recipe_preview', {
                'job_id': job_id,
                'upload_id': upload_id,
                'recipe': recipe_data,
                'image_data': image_data,
                'candidate_images': candidate_images_data,
                'best_image_index': best_image_index,
                'output_target': display_target,
                'export_to_both': config.EXPORT_TO_BOTH
            })

            # Wait for user response (with timeout)
            confirmed = confirm_event.wait(timeout=300)  # 5 minute timeout
            
            # Check if we actually got a confirmation or just timed out
            pending_data = pending_uploads.pop(upload_id, None)
            
            if not confirmed:
                jm.fail_job(job_id, 'Upload confirmation timed out')
                return
            
            if pending_data and not pending_data.get('confirmed', False):
                jm.update_progress(job_id, 'cancelled', 'Upload cancelled by user', 100)
                socketio.emit('recipe_cancelled', {
                    'job_id': job_id,
                    'message': 'Recipe upload was cancelled'
                }, room=f'job_{job_id}')
                socketio.emit('recipe_cancelled', {
                    'job_id': job_id,
                    'message': 'Recipe upload was cancelled'
                })
                return
            
            # Use the user-selected image if available
            selected_idx = pending_data.get('selected_image_index', best_image_index)
            if image_candidates and 0 <= selected_idx < len(image_candidates):
                image_path = image_candidates[selected_idx]

            jm.update_progress(job_id, 'upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)
        else:
            jm.update_progress(job_id, 'upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)

        if jm.is_cancelled(job_id):
            return

        # Determine upload targets
        upload_targets = []
        if config.EXPORT_TO_BOTH:
            # Export to both Tandoor and Mealie
            upload_targets = ['tandoor', 'mealie']
            jm.update_progress(job_id, 'upload', 'Uploading to Tandoor and Mealie...', 95)
        else:
            # Export to single target
            upload_targets = [config.OUTPUT_TARGET]

        upload_results = []
        
        for target in upload_targets:
            try:
                if target == 'tandoor':
                    from tandoor import Tandoor
                    tandoor = Tandoor()
                    result = tandoor.create_recipe(recipe_data)
                    if image_path and result.get("id"):
                        tandoor.upload_image(result["id"], image_path)
                    upload_results.append(('tandoor', True, None))
                elif target == 'mealie':
                    from mealie import Mealie
                    mealie = Mealie()
                    result = mealie.create_recipe(recipe_data)
                    recipe_slug = result.get("slug") or result.get("id")
                    if image_path and recipe_slug:
                        mealie.upload_image(recipe_slug, image_path)
                    upload_results.append(('mealie', True, None))
            except Exception as upload_error:
                upload_results.append((target, False, str(upload_error)))

        # Determine final output target for history
        final_target = ', '.join(upload_targets) if config.EXPORT_TO_BOTH else config.OUTPUT_TARGET
        
        # Check if any uploads failed
        failed_uploads = [r for r in upload_results if not r[1]]
        if failed_uploads and len(failed_uploads) == len(upload_targets):
            # All uploads failed
            error_msgs = '; '.join([f"{r[0]}: {r[2]}" for r in failed_uploads])
            jm.fail_job(job_id, f'All uploads failed: {error_msgs}')
            return
        elif failed_uploads:
            # Some uploads succeeded, some failed
            success_targets = [r[0] for r in upload_results if r[1]]
            failed_msgs = '; '.join([f"{r[0]}: {r[2]}" for r in failed_uploads])
            jm.complete_job(job_id, recipe_data, image_path, ', '.join(success_targets))
            jm.update_progress(job_id, 'complete',
                f'Recipe uploaded to {", ".join(success_targets)}. Failed: {failed_msgs}', 100)
        else:
            # All uploads succeeded
            jm.complete_job(job_id, recipe_data, image_path, final_target)
            jm.update_progress(job_id, 'complete', f'Recipe uploaded successfully to {final_target}!', 100)

    except Exception as e:
        jm.fail_job(job_id, f'Error: {str(e)}')


# ===== WebSocket Handlers =====

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    emit('connected', {'status': 'Connected to server'})


@socketio.on('subscribe_job')
def handle_subscribe_job(data):
    """Subscribe to a specific job's updates."""
    job_id = data.get('job_id')
    if job_id:
        join_room(f'job_{job_id}')
        emit('subscribed', {'job_id': job_id, 'status': 'subscribed'})


@socketio.on('unsubscribe_job')
def handle_unsubscribe_job(data):
    """Unsubscribe from a specific job's updates."""
    job_id = data.get('job_id')
    if job_id:
        leave_room(f'job_{job_id}')
        emit('unsubscribed', {'job_id': job_id, 'status': 'unsubscribed'})


@socketio.on('confirm_upload')
def handle_confirm_upload(data):
    """Handle user confirmation of recipe upload."""
    upload_id = data.get('upload_id')
    selected_image_index = data.get('selected_image_index')
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = True
        # Store the user's selected image index if provided
        if selected_image_index is not None:
            pending_uploads[upload_id]['selected_image_index'] = selected_image_index
        pending_uploads[upload_id]['event'].set()


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    """Handle user cancellation of recipe upload."""
    upload_id = data.get('upload_id')
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = False
        pending_uploads[upload_id]['event'].set()


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    socketio.run(app, debug=debug, host=host, port=port, allow_unsafe_werkzeug=True)
