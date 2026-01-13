FROM python:3.11-slim

# Install system dependencies for faster-whisper and video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create tmp directory for video processing
RUN mkdir -p /tmp

# Expose the web UI port
EXPOSE 5006

# Run the Flask application
CMD ["python", "ui/app.py"]
