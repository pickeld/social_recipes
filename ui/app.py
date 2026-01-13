"""
Social Recipes Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
"""

import os
import sys
import base64
import secrets
import threading
from datetime import timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from flask_socketio import SocketIO, emit

from database import (
    init_db, load_config, save_config,
    verify_user, update_password, hash_password
)

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

# Store pending recipe uploads waiting for confirmation
# Key: upload_id, Value: {'recipe': recipe_data, 'image_path': path, 'event': threading.Event(), 'confirmed': bool}
pending_uploads = {}


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
@login_required
def index():
    """Main page with URL input and progress display."""
    # Check for shared URL from session (set by /share route) or query params
    shared_url = session.pop('shared_url', None) or request.args.get('url') or request.args.get('text') or ''
    return render_template('index.html', shared_url=shared_url)


@app.route('/share', methods=['GET', 'POST'])
def share():
    """Handle shared URLs from PWA share_target.
    
    NOTE: This route intentionally does NOT require login so that Android's
    share_target can POST data before authentication. The URL is saved to
    session first, then user is redirected to login if needed.
    """
    # Get shared content from POST form data (Android) or query params (fallback)
    if request.method == 'POST':
        shared_url = request.form.get('url') or request.form.get('text') or ''
        shared_title = request.form.get('title', '')
    else:
        shared_url = request.args.get('url') or request.args.get('text') or ''
        shared_title = request.args.get('title', '')
    
    # Extract URL from text if it contains one
    if not shared_url and shared_title:
        shared_url = shared_title
    
    # Store in session BEFORE checking auth - this preserves the URL through login
    session['shared_url'] = shared_url
    
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
        config['whisper_model'] = request.form.get('whisper_model', 'small')
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


@app.route('/api/process', methods=['POST'])
@login_required
def process_video():
    """Start video processing."""
    data = request.get_json()
    url = data.get('url', '')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    # Start processing in background
    socketio.start_background_task(process_video_task, url)
    return jsonify({'status': 'started', 'message': 'Processing started'})


def process_video_task(url):
    """Background task to process video with progress updates."""
    try:
        # Import modules - they will read config from SQLite via config.py
        from config import config
        from video_downloader import VideoDownloader
        from transcriber import Transcriber
        from chef import Chef
        from image_extractor import extract_dish_image_candidates

        # Reload config to get latest values from database
        config.reload()

        # Step 1: Get video info
        emit_progress('info', 'Fetching video information...', 10)
        downloader = VideoDownloader(url)
        item = downloader._get_info()
        description = item.get("description", "No description available.")
        title = item.get("title", "Untitled")
        emit_progress('info', f'Video: {title}', 15)

        # Step 2: Download video
        emit_progress('download', 'Downloading video...', 20)
        vid_id, video_path = downloader._download_video()
        if vid_id is None:
            emit_progress('error', 'Failed to download video', 100)
            return
        dish_dir = os.path.join("/tmp", vid_id)
        emit_progress('download', 'Video downloaded successfully', 30)

        # Step 3: Transcribe audio
        emit_progress('transcribe', 'Transcribing audio...', 35)
        transcriber = Transcriber(video_path)
        lang = config.TARGET_LANGUAGE

        audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")
        if os.path.exists(audio_cache):
            emit_progress('transcribe', 'Using cached transcription', 40)
            with open(audio_cache, "r") as f:
                transcription = f.read()
        else:
            transcription = transcriber.transcribe()
            with open(audio_cache, "w") as f:
                f.write(transcription)
        emit_progress('transcribe', 'Audio transcribed', 50)

        # Step 4: Extract visual text
        emit_progress('visual', 'Extracting on-screen text...', 55)
        visual_text = ""
        visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
        if os.path.exists(visual_cache):
            emit_progress('visual', 'Using cached visual text', 60)
            with open(visual_cache, "r") as f:
                visual_text = f.read()
        else:
            try:
                visual_text = transcriber.extract_visual_text()
                with open(visual_cache, "w") as f:
                    f.write(visual_text)
            except Exception as e:
                emit_progress(
                    'visual', f'Warning: Could not extract visual text: {e}', 60)
        emit_progress('visual', 'Visual text extracted', 65)

        # Combine transcription
        combined_transcription = transcription
        if visual_text:
            combined_transcription = f"""=== AUDIO TRANSCRIPTION ===
{transcription}

=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===
{visual_text}"""

        # Step 5: Extract dish image candidates
        emit_progress('image', 'Extracting dish image candidates...', 70)
        image_path = None
        image_candidates = []
        best_image_index = 0
        image_cache = os.path.join(dish_dir, "dish.jpg")
        frames_dir = os.path.join(dish_dir, "dish_frames")
        
        # Check if we have cached candidates
        if os.path.exists(frames_dir) and os.path.exists(image_cache):
            emit_progress('image', 'Using cached dish images', 75)
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
                emit_progress(
                    'image', f'Warning: Could not extract image: {e}', 75)
        emit_progress('image', 'Image candidates extracted', 80)

        # Step 6: Create recipe with AI
        emit_progress('evaluate', 'Creating recipe with AI...', 85)
        chef = Chef(source_url=url, description=description,
                    transcription=combined_transcription)
        recipe_data = chef.create_recipe()

        if not recipe_data:
            emit_progress('error', 'Failed to create recipe', 100)
            return

        emit_progress('evaluate', 'Recipe created successfully', 90)

        # Step 7: Upload to target (with optional preview confirmation)
        if config.CONFIRM_BEFORE_UPLOAD:
            # Show preview and wait for user confirmation
            emit_progress('preview', 'Waiting for your confirmation...', 90)

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
                'selected_image_index': best_image_index  # Default to AI-selected best
            }

            # Send preview to client with all candidate images
            socketio.emit('recipe_preview', {
                'upload_id': upload_id,
                'recipe': recipe_data,
                'image_data': image_data,
                'candidate_images': candidate_images_data,
                'best_image_index': best_image_index,
                'output_target': config.OUTPUT_TARGET
            })

            # Wait for user response (with timeout) - threading.Event.wait returns True if set, False on timeout
            confirmed = confirm_event.wait(timeout=300)  # 5 minute timeout
            
            # Check if we actually got a confirmation or just timed out
            pending_data = pending_uploads.pop(upload_id, None)
            
            if not confirmed:
                emit_progress('error', 'Upload confirmation timed out', 100)
                return
            
            if pending_data and not pending_data.get('confirmed', False):
                emit_progress('cancelled', 'Upload cancelled by user', 100)
                socketio.emit('recipe_cancelled', {
                              'message': 'Recipe upload was cancelled'})
                return
            
            # Use the user-selected image if available
            selected_idx = pending_data.get('selected_image_index', best_image_index)
            if image_candidates and 0 <= selected_idx < len(image_candidates):
                image_path = image_candidates[selected_idx]

            emit_progress(
                'upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)
        else:
            emit_progress(
                'upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)

        if config.OUTPUT_TARGET == 'tandoor':
            from tandoor import Tandoor
            tandoor = Tandoor()
            result = tandoor.create_recipe(recipe_data)
            if image_path and result.get("id"):
                tandoor.upload_image(result["id"], image_path)
        elif config.OUTPUT_TARGET == 'mealie':
            from mealie import Mealie
            mealie = Mealie()
            result = mealie.create_recipe(recipe_data)
            recipe_slug = result.get("slug") or result.get("id")
            if image_path and recipe_slug:
                mealie.upload_image(recipe_slug, image_path)

        emit_progress('complete', 'Recipe uploaded successfully!', 100)
        socketio.emit('recipe_complete', {'recipe': recipe_data})

    except Exception as e:
        emit_progress('error', f'Error: {str(e)}', 100)


def emit_progress(stage, message, percent):
    """Emit progress update via WebSocket."""
    socketio.emit('progress', {
        'stage': stage,
        'message': message,
        'percent': percent
    })


@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    emit('connected', {'status': 'Connected to server'})


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
        pending_uploads[upload_id]['event'].set()  # threading.Event uses set()


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    """Handle user cancellation of recipe upload."""
    upload_id = data.get('upload_id')
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = False
        pending_uploads[upload_id]['event'].set()  # threading.Event uses set()


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
    socketio.run(app, debug=debug, host=host, port=port, allow_unsafe_werkzeug=True)
