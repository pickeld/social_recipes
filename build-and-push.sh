#!/bin/bash
#
# Build and push multi-architecture Docker image to Docker Hub
# Supports: linux/amd64 (Intel/AMD) and linux/arm64 (Apple Silicon, Raspberry Pi 4, etc.)
#
# Usage: ./build-and-push.sh [tag]
#   tag: 'latest' (default), 'dev', or any custom tag
#
# Examples:
#   ./build-and-push.sh           # Builds and pushes with 'latest' tag
#   ./build-and-push.sh dev       # Builds and pushes with 'dev' tag
#   ./build-and-push.sh v1.2.0    # Builds and pushes with 'v1.2.0' tag
#
# Requirements:
#   - Docker with buildx support (Docker Desktop includes this)
#   - Logged in to Docker Hub (run 'docker login' first)
#

set -e

# Configuration
DOCKER_REPO="pickeld/social_recipes"
DEFAULT_TAG="latest"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER_NAME="social-recipes-builder"

# Get tag from argument or use default
TAG="${1:-$DEFAULT_TAG}"

# Validate tag
if [[ ! "$TAG" =~ ^[a-zA-Z0-9._-]+$ ]]; then
    echo "Error: Invalid tag format. Tags can only contain letters, numbers, dots, underscores, and hyphens."
    exit 1
fi

IMAGE_NAME="${DOCKER_REPO}:${TAG}"

echo "============================================"
echo "Building Multi-Arch Docker Image"
echo "============================================"
echo "Image:      ${IMAGE_NAME}"
echo "Platforms:  ${PLATFORMS}"
echo "============================================"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH"
    exit 1
fi

# Check if buildx is available
if ! docker buildx version &> /dev/null; then
    echo "Error: Docker buildx is not available."
    echo "Please install Docker Desktop or enable buildx manually."
    exit 1
fi

# Check if logged in to Docker Hub
echo ""
echo "Checking Docker Hub login..."
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo ""
    echo "Warning: You may not be logged in to Docker Hub."
    echo "Please run 'docker login' first."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create or use buildx builder
echo ""
echo "Setting up multi-platform builder..."

# Check if our builder exists
if docker buildx inspect "${BUILDER_NAME}" &> /dev/null; then
    echo "Using existing builder: ${BUILDER_NAME}"
    docker buildx use "${BUILDER_NAME}"
else
    echo "Creating new builder: ${BUILDER_NAME}"
    docker buildx create --name "${BUILDER_NAME}" --use --bootstrap
fi

# Build and push the image for multiple platforms
echo ""
echo "Building and pushing multi-arch image..."
echo "This may take several minutes on first build..."
echo ""

docker buildx build \
    --platform "${PLATFORMS}" \
    --tag "${IMAGE_NAME}" \
    --push \
    .

if [ $? -ne 0 ]; then
    echo ""
    echo "Error: Build failed"
    exit 1
fi

echo ""
echo "============================================"
echo "✓ Successfully built and pushed!"
echo "============================================"
echo "Image: ${IMAGE_NAME}"
echo "Platforms: ${PLATFORMS}"
echo ""
echo "Pull with: docker pull ${IMAGE_NAME}"
echo "============================================"

# Show manifest to confirm multi-arch
echo ""
echo "Verifying multi-arch manifest..."
docker buildx imagetools inspect "${IMAGE_NAME}" --raw 2>/dev/null | head -20 || echo "(Manifest inspection skipped)"

# Tips
if [ "$TAG" = "latest" ]; then
    echo ""
    echo "Tip: To also push a dev tag, run: ./build-and-push.sh dev"
elif [ "$TAG" = "dev" ]; then
    echo ""
    echo "Tip: To push as latest, run: ./build-and-push.sh latest"
fi
