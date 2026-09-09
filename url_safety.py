"""SSRF guards for outbound HTTP fetches of user-supplied recipe URLs.

Web recipe pages and dish images are fetched from attacker-controlled URLs.
Only http(s) to globally routed addresses on ports 80/443 is allowed, and
redirects are re-checked before they are followed.

DNS is resolved once per hop and the TCP connection is pinned to that public
IP so the host cannot rebind to a private address after the check.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse, urlunparse

from requests.adapters import HTTPAdapter

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


class TlsHostnameAdapter(HTTPAdapter):
    """HTTPS adapter that verifies TLS against ``hostname``, not the pinned IP."""

    def __init__(self, hostname: str, **kwargs):
        self._tls_hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_hostname"] = self._tls_hostname
        pool_kwargs["server_hostname"] = self._tls_hostname
        return super().init_poolmanager(
            connections, maxsize, block=block, **pool_kwargs
        )


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


def _literal_ip(host: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _format_ip_netloc(ip: ipaddress._BaseAddress, port: int | None) -> str:
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    if port is not None:
        return f"{host}:{port}"
    return host


def _host_header(parsed) -> str:
    host = parsed.hostname
    port = parsed.port
    default = 443 if parsed.scheme == "https" else 80
    if port and port != default:
        return f"{host}:{port}"
    return host


def validate_public_http_url(url: str) -> str:
    """Guardrail alias for assert_public_http_url (stable name for inventory)."""
    return assert_public_http_url(url)


def assert_public_http_url(url: str) -> str:
    """Raise UnsafeURLError unless ``url`` is a public http(s) URL.

    Credentials in the URL, non-80/443 ports, localhost, and any resolved
    address that is not globally routed are rejected.
    """
    pin_public_http_url(url)
    return url.strip()


def pin_public_http_url(url: str) -> tuple[str, str, str]:
    """Validate ``url`` and pin it to one public IP from this resolution.

    Returns ``(tls_hostname, url_with_ip_netloc, scheme)``. Connecting to the
    IP (with Host / SNI still set to ``tls_hostname``) closes the DNS-rebinding
    window where ``requests`` would otherwise resolve the name again.
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

    public: list[ipaddress._BaseAddress] = []
    for ip in _iter_resolved_ips(host):
        if not ip.is_global:
            raise UnsafeURLError(
                f"Host '{host}' resolves to a non-public address"
            )
        if ip not in public:
            public.append(ip)
    if not public:
        raise UnsafeURLError(f"Cannot resolve host '{host}'")

    if _literal_ip(host) is not None:
        return host, url, parsed.scheme

    pinned = urlunparse((
        parsed.scheme,
        _format_ip_netloc(public[0], parsed.port),
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))
    return host, pinned, parsed.scheme


def _session_get(session, url: str, scheme: str, hostname: str, kwargs: dict):
    if scheme != "https":
        return session.get(url, **kwargs)
    adapters = getattr(session, "adapters", None)
    previous = None
    if isinstance(adapters, dict):
        previous = adapters.get("https://")
    elif adapters is not None:
        get = getattr(adapters, "get", None)
        if callable(get):
            previous = get("https://")
    retry = getattr(previous, "max_retries", 0)
    adapter = TlsHostnameAdapter(hostname, max_retries=retry)
    session.mount("https://", adapter)
    try:
        return session.get(url, **kwargs)
    finally:
        if previous is not None:
            session.mount("https://", previous)


def safe_get(session, url: str, **kwargs):
    """``session.get`` that validates the URL, pins DNS, and re-checks redirects."""
    current = url
    kwargs = dict(kwargs)
    kwargs["allow_redirects"] = False
    extra_headers = dict(kwargs.pop("headers", None) or {})
    for _ in range(_MAX_REDIRECTS + 1):
        hostname, pinned_url, scheme = pin_public_http_url(current)
        headers = dict(extra_headers)
        headers["Host"] = _host_header(urlparse(current))
        response = _session_get(
            session, pinned_url, scheme, hostname, {**kwargs, "headers": headers}
        )
        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise UnsafeURLError("Redirect with no Location header")
            current = urljoin(current, location)
            logger.info("[URLSafety] Following redirect to %s", current)
            continue
        return response
    raise UnsafeURLError("Too many redirects")
