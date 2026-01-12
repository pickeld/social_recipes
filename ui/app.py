"""
Social Recipes Web UI
A Flask-based web interface for video recipe extraction with authentication and configuration management.
"""

# Monkey-patch for async support - MUST be at the very top before other imports
import eventlet
eventlet.monkey_patch()

import os
import sys
import secrets
import base64
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_socketio import SocketIO, emit

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import database module
from database import (
    init_db, load_config, save_config,
    verify_user, update_password, hash_password
)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Initialize database
init_db()

# Store pending recipe uploads waiting for confirmation
# Key: session_id, Value: {'recipe': recipe_data, 'image_path': path, 'event': eventlet.event.Event(), 'confirmed': bool}
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
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if verify_user(username, password):
            session['user'] = username
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
        config['confirm_before_upload'] = 'true' if request.form.get('confirm_before_upload') else 'false'

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
        from image_extractor import extract_dish_image

        # Reload config to get latest values from database
        config.reload()

        # Step 1: Get video info
        emit_progress('info', 'Fetching video information...', 10)
        eventlet.sleep(0)  # Yield to event loop
        downloader = VideoDownloader(url)
        item = downloader._get_info()
        description = item.get("description", "No description available.")
        title = item.get("title", "Untitled")
        emit_progress('info', f'Video: {title}', 15)

        # Step 2: Download video
        emit_progress('download', 'Downloading video...', 20)
        eventlet.sleep(0)  # Yield to event loop
        vid_id, video_path = downloader._download_video()
        if vid_id is None:
            emit_progress('error', 'Failed to download video', 100)
            return
        dish_dir = os.path.join("tmp", vid_id)
        emit_progress('download', 'Video downloaded successfully', 30)

        # Step 3: Transcribe audio
        emit_progress('transcribe', 'Transcribing audio...', 35)
        eventlet.sleep(0)  # Yield to event loop
        transcriber = Transcriber(video_path)
        lang = config.TARGET_LANGUAGE

        audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")
        if os.path.exists(audio_cache):
            emit_progress('transcribe', 'Using cached transcription', 40)
            with open(audio_cache, "r") as f:
                transcription = f.read()
        else:
            transcription = transcriber.transcribe()
            eventlet.sleep(0)  # Yield after CPU-intensive transcription
            with open(audio_cache, "w") as f:
                f.write(transcription)
        emit_progress('transcribe', 'Audio transcribed', 50)

        # Step 4: Extract visual text
        emit_progress('visual', 'Extracting on-screen text...', 55)
        eventlet.sleep(0)  # Yield to event loop
        visual_text = ""
        visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
        if os.path.exists(visual_cache):
            emit_progress('visual', 'Using cached visual text', 60)
            with open(visual_cache, "r") as f:
                visual_text = f.read()
        else:
            try:
                visual_text = transcriber.extract_visual_text()
                eventlet.sleep(0)  # Yield after LLM call
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

        # Step 5: Extract dish image
        emit_progress('image', 'Extracting dish image...', 70)
        eventlet.sleep(0)  # Yield to event loop
        image_path = None
        image_cache = os.path.join(dish_dir, "dish.jpg")
        if os.path.exists(image_cache):
            emit_progress('image', 'Using cached dish image', 75)
            image_path = image_cache
        else:
            try:
                image_path = extract_dish_image(video_path)
                eventlet.sleep(0)  # Yield after image extraction
            except Exception as e:
                emit_progress(
                    'image', f'Warning: Could not extract image: {e}', 75)
        emit_progress('image', 'Image extracted', 80)

        # Step 6: Create recipe with AI
        emit_progress('evaluate', 'Creating recipe with AI...', 85)
        eventlet.sleep(0)  # Yield to event loop
        chef = Chef(source_url=url, description=description,
                    transcription=combined_transcription)
        recipe_data = chef.create_recipe()
        eventlet.sleep(0)  # Yield after LLM call

        if not recipe_data:
            emit_progress('error', 'Failed to create recipe', 100)
            return

        emit_progress('evaluate', 'Recipe created successfully', 90)

        # Step 7: Upload to target (with optional preview confirmation)
        if config.CONFIRM_BEFORE_UPLOAD:
            # Show preview and wait for user confirmation
            emit_progress('preview', 'Waiting for your confirmation...', 90)
            
            # Prepare image data for preview if available
            image_data = None
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode('utf-8')
            
            # Create event for waiting
            confirm_event = eventlet.event.Event()
            upload_id = secrets.token_hex(16)
            
            pending_uploads[upload_id] = {
                'recipe': recipe_data,
                'image_path': image_path,
                'output_target': config.OUTPUT_TARGET,
                'event': confirm_event,
                'confirmed': None
            }
            
            # Send preview to client
            socketio.emit('recipe_preview', {
                'upload_id': upload_id,
                'recipe': recipe_data,
                'image_data': image_data,
                'output_target': config.OUTPUT_TARGET
            })
            
            # Wait for user response (with timeout)
            try:
                confirmed = confirm_event.wait(timeout=300)  # 5 minute timeout
            except eventlet.Timeout:
                confirmed = False
                emit_progress('error', 'Upload confirmation timed out', 100)
                del pending_uploads[upload_id]
                return
            
            # Clean up pending upload
            pending_data = pending_uploads.pop(upload_id, None)
            
            if not confirmed:
                emit_progress('cancelled', 'Upload cancelled by user', 100)
                socketio.emit('recipe_cancelled', {'message': 'Recipe upload was cancelled'})
                return
            
            emit_progress('upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)
        else:
            emit_progress('upload', f'Uploading to {config.OUTPUT_TARGET}...', 95)
        
        eventlet.sleep(0)  # Yield to event loop

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
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = True
        pending_uploads[upload_id]['event'].send(True)


@socketio.on('cancel_upload')
def handle_cancel_upload(data):
    """Handle user cancellation of recipe upload."""
    upload_id = data.get('upload_id')
    if upload_id and upload_id in pending_uploads:
        pending_uploads[upload_id]['confirmed'] = False
        pending_uploads[upload_id]['event'].send(False)


if __name__ == '__main__':
    load_dotenv()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5006'))
    socketio.run(app, debug=True, host=host, port=port)
