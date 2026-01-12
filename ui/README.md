# Social Recipes Web UI

A modern web interface for the Social Recipes video recipe extractor.

## Features

- 🔐 **Simple Authentication** - Secure login with username/password
- 📹 **URL Input** - Paste video URLs from TikTok, YouTube, Instagram, etc.
- 📊 **Real-time Progress** - Watch the extraction process with live updates
- ⚙️ **Configuration Management** - Save all settings through the web interface
- 🎨 **Modern Dark Theme** - Beautiful, responsive UI

## Installation

1. Install the required dependencies:

```bash
pip install -r requirements.txt
```

2. Run the UI server:

```bash
cd ui
python app.py
```

3. Open your browser and navigate to: `http://localhost:5000`

## Default Login

- **Username:** `admin`
- **Password:** `admin123`

⚠️ **Important:** Change the default password after first login!

## Configuration

All settings can be configured through the web interface by clicking the gear icon (⚙️) in the sidebar:

### LLM Provider
- Choose between OpenAI and Google Gemini
- Configure API keys and model names

### Recipe Output
- Select output target (Tandoor or Mealie)
- Set recipe language preferences

### Mealie Settings
- Mealie server URL
- API key

### Tandoor Settings
- Tandoor server URL
- API key

### Whisper Transcription
- Choose transcription model size (tiny, base, small, medium, large-v3)

## File Structure

```
ui/
├── app.py                 # Flask application with SocketIO
├── database.py            # SQLite database module
├── templates/
│   ├── base.html         # Base template with sidebar navigation
│   ├── index.html        # Main page with URL input and progress
│   ├── login.html        # Login page
│   └── settings.html     # Settings/configuration page
└── static/
    ├── css/
    │   └── style.css     # All styling
    └── js/
        └── main.js       # Frontend JavaScript for progress tracking
```

## Database

The UI uses SQLite for data storage. A single database file is created in the project root:

- `social_recipes.db` - SQLite database containing:
  - `users` table - User credentials (hashed passwords)
  - `config` table - Configuration key-value pairs

## WebSocket Progress Events

The UI uses Socket.IO for real-time progress updates. The stages are:

1. `info` - Getting video information
2. `download` - Downloading video
3. `transcribe` - Transcribing audio
4. `visual` - Extracting on-screen text
5. `image` - Extracting dish image
6. `evaluate` - Creating recipe with AI
7. `upload` - Uploading to recipe manager
8. `complete` / `error` - Final status

## Security Notes

- Passwords are hashed using SHA-256
- Session management uses Flask's secure sessions
- API keys are stored in the local configuration file
- For production use, consider using a proper database and stronger hashing (bcrypt)
