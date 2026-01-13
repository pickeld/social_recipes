# Social Recipes

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker Hub](https://img.shields.io/docker/v/pickeld/social_recipes?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/pickeld/social_recipes)
[![Docker Pulls](https://img.shields.io/docker/pulls/pickeld/social_recipes?logo=docker)](https://hub.docker.com/r/pickeld/social_recipes)
[![Flask](https://img.shields.io/badge/Flask-Web_UI-green.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Extract recipes from social media videos (TikTok, YouTube, Instagram, etc.) and automatically import them into your self-hosted recipe manager.

## Overview

Social Recipes is a Python application that:

1. **Downloads videos** from TikTok, YouTube, Instagram, and other platforms using `yt-dlp`
2. **Transcribes audio** using Whisper AI (via `faster-whisper`)
3. **Extracts on-screen text** (ingredients, instructions) using vision-capable LLMs
4. **Generates structured recipes** using AI (OpenAI GPT or Google Gemini)
5. **Uploads to recipe managers** - supports [Tandoor](https://tandoor.dev/) and [Mealie](https://mealie.io/)

### Features

- 🎥 Multi-platform video support (TikTok, YouTube, Instagram, etc.)
- 🎙️ Audio transcription with language detection
- 👁️ Visual text extraction from video frames
- 🤖 AI-powered recipe generation with structured ingredients
- 🍽️ Automatic nutrition and serving size estimation
- 🖼️ Dish image extraction with manual selection option
- 🌐 Web UI with real-time progress updates
- 🔐 User authentication and settings management
- 🐳 Docker support for easy deployment

## Requirements

- Python 3.11+
- FFmpeg (for video/audio processing)
- API key for OpenAI or Google Gemini
- Self-hosted Tandoor or Mealie instance (optional)

## Installation

### Using Docker (Recommended)

**Option 1: Pull from Docker Hub (Easiest)**

```bash
docker run -d \
  --name social-recipes \
  -p 5006:5006 \
  -e FLASK_SECRET_KEY="your-secure-secret-key" \
  -v social-recipes-data:/app/data \
  pickeld/social_recipes:latest
```

Access the web UI at `http://localhost:5006`

**Option 2: Using Docker Compose**

Create a `docker-compose.yml` file:

```yaml
version: "3.8"

services:
  social-recipes:
    image: pickeld/social_recipes:latest
    container_name: social-recipes
    restart: unless-stopped
    ports:
      - "5006:5006"
    environment:
      - FLASK_SECRET_KEY=your-secure-secret-key
    volumes:
      - social-recipes-data:/app/data

volumes:
  social-recipes-data:
```

Then run:

```bash
docker-compose up -d
```

Access the web UI at `http://localhost:5006`

### Manual Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/social_recipes.git
   cd social_recipes
   ```

2. Install system dependencies:
   ```bash
   # macOS
   brew install ffmpeg

   # Ubuntu/Debian
   sudo apt-get install ffmpeg
   ```

3. Create a virtual environment and install Python dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python ui/app.py
   ```

5. Access the web UI at `http://localhost:5006`

## Configuration

All configuration is managed through the web UI settings page (`/settings`). On first run, use the default credentials:

- **Username:** `admin`
- **Password:** `admin123`

> ⚠️ **Important:** Change the default password immediately after first login!

### Settings

| Setting | Description |
|---------|-------------|
| **LLM Provider** | Choose between OpenAI or Google Gemini |
| **OpenAI API Key** | Your OpenAI API key (required if using OpenAI) |
| **OpenAI Model** | Model to use (default: `gpt-5-mini-2025-08-07`) |
| **Gemini API Key** | Your Google Gemini API key (required if using Gemini) |
| **Gemini Model** | Model to use (default: `gemini-2.0-flash`) |
| **Recipe Language** | Target language for recipe output (e.g., `hebrew`, `english`) |
| **Target Language Code** | ISO language code for transcription (e.g., `he`, `en`) |
| **Whisper Model** | Whisper model size (`tiny`, `small`, `medium`, `large`) |
| **Output Target** | Recipe manager: `tandoor` or `mealie` |
| **Tandoor Host** | URL of your Tandoor instance |
| **Tandoor API Key** | API token from Tandoor |
| **Mealie Host** | URL of your Mealie instance |
| **Mealie API Key** | API token from Mealie |
| **Confirm Before Upload** | Show recipe preview before uploading |

## Usage

### Web UI

1. Navigate to `http://localhost:5006`
2. Log in with your credentials
3. Paste a video URL (TikTok, YouTube, Instagram, etc.)
4. Click "Extract Recipe"
5. Watch the real-time progress as the video is processed
6. If "Confirm Before Upload" is enabled, review and optionally edit the recipe
7. The recipe is automatically uploaded to your configured recipe manager

### Command Line

For testing or batch processing, you can use the CLI:

```bash
# Basic usage
python main.py "https://www.tiktok.com/@user/video/1234567890"

# Skip upload (just generate recipe JSON)
python main.py --no-upload "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Project Structure

```
social_recipes/
├── main.py              # CLI entry point
├── chef.py              # AI recipe generation
├── config.py            # Configuration management
├── video_downloader.py  # Video downloading (yt-dlp)
├── transcriber.py       # Audio transcription (Whisper)
├── image_extractor.py   # Dish image extraction
├── mealie.py            # Mealie API integration
├── tandoor.py           # Tandoor API integration
├── recipe_exporter.py   # Recipe export utilities
├── helpers.py           # Utility functions and prompts
├── llm_providers/       # LLM provider implementations
│   ├── base.py
│   ├── openai.py
│   └── gemini.py
├── ui/                  # Flask web UI
│   ├── app.py           # Flask application
│   ├── database.py      # SQLite database management
│   ├── templates/       # HTML templates
│   └── static/          # CSS and JavaScript
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Docker Deployment

### Docker Hub Image

The official image is available on Docker Hub: [`pickeld/social_recipes`](https://hub.docker.com/r/pickeld/social_recipes)

```bash
# Pull the latest image
docker pull pickeld/social_recipes:latest

# Or pull a specific version
docker pull pickeld/social_recipes:v1.0.0
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HOST` | Host to bind to | `0.0.0.0` |
| `PORT` | Port to listen on | `5006` |
| `FLASK_SECRET_KEY` | Secret key for session cookies | Auto-generated |
| `FLASK_DEBUG` | Enable debug mode | `false` |

### Docker Compose (Using Docker Hub)

```yaml
version: "3.8"

services:
  social-recipes:
    image: pickeld/social_recipes:latest
    container_name: social-recipes
    restart: unless-stopped
    ports:
      - "5006:5006"
    environment:
      - HOST=0.0.0.0
      - PORT=5006
      - FLASK_SECRET_KEY=your-secure-secret-key
    volumes:
      - social-recipes-data:/app/data

volumes:
  social-recipes-data:
```

### Building from Source

If you prefer to build the image yourself:

```bash
git clone https://github.com/pickeld/social_recipes.git
cd social_recipes
docker build -t social-recipes .
docker run -p 5006:5006 -e FLASK_SECRET_KEY="your-secret" social-recipes
```

## Supported Platforms

Social Recipes uses `yt-dlp` for video downloading, which supports:

- TikTok
- YouTube
- Instagram Reels
- Facebook Videos
- Twitter/X Videos
- And [many more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## GitHub Repository

### Description

AI-powered recipe extraction from TikTok, YouTube & Instagram videos with automatic import to Tandoor/Mealie recipe managers.

### Topics

```
recipe-extraction tiktok youtube instagram whisper openai gemini tandoor mealie self-hosted docker flask python ai llm video-processing transcription cooking food automation
```

**To configure:** Go to your repository on GitHub → Click the ⚙️ gear icon next to "About" → Add the description and topics above.
