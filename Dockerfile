FROM python:3.11-slim

# Install system dependencies for faster-whisper and video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube extraction)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the web UI port
EXPOSE 5006

# Default environment variables
ENV FLASK_DEBUG=false
ENV PYTHONPATH=/app

# Run the Flask application
CMD ["python", "ui/app.py"]
