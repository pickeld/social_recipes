"""
Fetch and parse recipe content from a web URL.

Tries Schema.org JSON-LD first (most recipe sites embed it), then falls back
to a plain-text extraction of the visible page content so the LLM can work
with whatever is available.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse

from helpers import create_http_session, setup_logger
from url_safety import safe_get

logger = setup_logger(__name__)

# Domains / URL fragments that are known video platforms — skip HTML scraping
_VIDEO_DOMAINS = frozenset([
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "reels",
    "facebook.com",
    "fb.watch",
    "twitter.com",
    "x.com",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",
    "clips.twitch.tv",
])

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_video_url(url: str) -> bool:
    """Return True when the URL clearly points to a video platform."""
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        host = ""
    return any(domain in host for domain in _VIDEO_DOMAINS)


def fetch_web_recipe(url: str) -> dict:
    """Fetch a recipe web page and return extracted content.

    Returns a dict with:
        title        str   – page / recipe title
        description  str   – short description or meta description
        structured   dict|None – Schema.org Recipe object if found in page
        page_text    str   – cleaned visible text (for LLM fallback)
        image_url    str|None – best candidate image URL
    """
    session = create_http_session()
    logger.info(f"[WebRecipeFetcher] Fetching {url}")

    resp = safe_get(session, url, headers=_BROWSER_HEADERS, timeout=20)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        raise ValueError(
            f"URL does not appear to be an HTML page (content-type: {content_type})"
        )

    html = resp.text

    structured = _extract_schema_recipe(html)
    title = _extract_title(html, structured)
    description = _extract_description(html, structured)
    image_url = _extract_image_url(html, structured, url)
    page_text = _extract_page_text(html)

    if structured:
        logger.info(
            f"[WebRecipeFetcher] Found Schema.org Recipe: '{structured.get('name', title)}'"
        )
    else:
        logger.info(
            f"[WebRecipeFetcher] No Schema.org Recipe found; will use page text ({len(page_text)} chars)"
        )

    return {
        "title": title,
        "description": description,
        "structured": structured,
        "page_text": page_text,
        "image_url": image_url,
    }


def download_image(image_url: str, dest_dir: str) -> str | None:
    """Download image_url into dest_dir and return the local path, or None."""
    if not image_url:
        return None
    try:
        session = create_http_session()
        resp = safe_get(
            session, image_url, headers=_BROWSER_HEADERS, timeout=15, stream=True
        )
        resp.raise_for_status()
        ext = _guess_image_ext(resp.headers.get("content-type", ""), image_url)
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"recipe_image{ext}")
        with open(path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        logger.info(f"[WebRecipeFetcher] Downloaded image → {path}")
        return path
    except Exception as exc:
        logger.warning(f"[WebRecipeFetcher] Could not download image {image_url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_schema_recipe(html: str) -> dict | None:
    """Find the first Schema.org Recipe object embedded as JSON-LD."""
    # Find all <script type="application/ld+json"> blocks
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue

        # Handle @graph arrays (common on Yoast SEO sites)
        if isinstance(data, dict) and "@graph" in data:
            for node in data["@graph"]:
                r = _as_recipe(node)
                if r:
                    return r

        # Handle top-level array
        if isinstance(data, list):
            for node in data:
                r = _as_recipe(node)
                if r:
                    return r

        r = _as_recipe(data)
        if r:
            return r

    return None


def _as_recipe(node: Any) -> dict | None:
    """Return node if it looks like a Schema.org Recipe, otherwise None."""
    if not isinstance(node, dict):
        return None
    type_val = node.get("@type", "")
    if isinstance(type_val, list):
        types = [t.lower() for t in type_val]
    else:
        types = [str(type_val).lower()]
    if "recipe" in types:
        return node
    return None


def _extract_title(html: str, structured: dict | None) -> str:
    if structured:
        name = structured.get("name") or structured.get("headline")
        if name and isinstance(name, str):
            return name.strip()

    # og:title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # <title>
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return "Untitled Recipe"


def _extract_description(html: str, structured: dict | None) -> str:
    if structured:
        desc = structured.get("description")
        if desc and isinstance(desc, str):
            return desc.strip()

    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    m = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    return ""


def _extract_image_url(html: str, structured: dict | None, page_url: str) -> str | None:
    if structured:
        img = structured.get("image")
        if isinstance(img, str):
            return _abs(img, page_url)
        if isinstance(img, list) and img:
            first = img[0]
            if isinstance(first, str):
                return _abs(first, page_url)
            if isinstance(first, dict):
                url_val = first.get("url") or first.get("@id")
                if url_val:
                    return _abs(url_val, page_url)
        if isinstance(img, dict):
            url_val = img.get("url") or img.get("@id")
            if url_val:
                return _abs(url_val, page_url)

    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return _abs(m.group(1).strip(), page_url)

    return None


def _extract_page_text(html: str) -> str:
    """Strip HTML tags and return cleaned visible text, capped at ~20 000 chars."""
    # Remove script / style blocks entirely
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    # Remove tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    # Cap length so we don't blow the LLM context window
    if len(cleaned) > 20000:
        cleaned = cleaned[:20000] + "\n[... truncated ...]"
    return cleaned


def _abs(url_val: str, base: str) -> str:
    """Make a URL absolute using the page base URL."""
    if url_val.startswith("http"):
        return url_val
    return urljoin(base, url_val)


def _guess_image_ext(content_type: str, url: str) -> str:
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    # Fall back to URL extension
    path = urlparse(url).path
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.lower().endswith(ext):
            return ext
    return ".jpg"
