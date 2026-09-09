"""SSRF guards for outbound HTTP fetches of user-supplied recipe URLs.

Web recipe pages and dish images are fetched from attacker-controlled URLs.
Only http(s) to globally routed addresses on ports 80/443 is allowed, and
redirects are re-checked before they are followed.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from helpers import setup_logger

logger = setup_logger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})
_BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.",
    "metadata.google.internal",
    "metadata.google.internal.",
})
_MAX_REDIRECTS = 5
_MAX_URL_LEN = 2048


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to fetch from this process."""


def _hostname_blocked(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered in _BLOCKED_HOSTS or lowered == "localhost":
        return True
    return lowered.endswith(".localhost") or lowered.endswith(".local")


def _iter_resolved_ips(host: str):
    ips: list[ipaddress._BaseAddress] = []
    try:
        ips.append(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Cannot resolve host '{host}'") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise UnsafeURLError(f"Cannot resolve host '{host}'")
    return ips


def assert_public_http_url(url: str) -> str:
    """Raise UnsafeURLError unless ``url`` is a public http(s) URL.

    Credentials in the URL, non-80/443 ports, localhost, and any resolved
    address that is not globally routed are rejected.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL is required")
    url = url.strip()
    if len(url) > _MAX_URL_LEN:
        raise UnsafeURLError("URL is too long")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"URL scheme '{parsed.scheme or '(none)'}' is not allowed"
        )
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL is missing a host")
    if _hostname_blocked(host):
        raise UnsafeURLError(f"Host '{host}' is not allowed")
    if parsed.port is not None and parsed.port not in _ALLOWED_PORTS:
        raise UnsafeURLError(f"Port {parsed.port} is not allowed")

    for ip in _iter_resolved_ips(host):
        if not ip.is_global:
            raise UnsafeURLError(
                f"Host '{host}' resolves to a non-public address"
            )
    return url


def safe_get(session, url: str, **kwargs):
    """``session.get`` that validates the URL and every redirect hop."""
    current = url
    kwargs = dict(kwargs)
    kwargs["allow_redirects"] = False
    for _ in range(_MAX_REDIRECTS + 1):
        assert_public_http_url(current)
        response = session.get(current, **kwargs)
        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise UnsafeURLError("Redirect with no Location header")
            current = urljoin(current, location)
            logger.info("[URLSafety] Following redirect to %s", current)
            continue
        return response
    raise UnsafeURLError("Too many redirects")
