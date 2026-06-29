from __future__ import annotations

import ipaddress
from urllib.parse import ParseResult, parse_qs, urlparse  # noqa: TID251

_LOCAL_HOSTNAMES: set[str] = {"localhost"}


def safe_urlparse(candidate: str) -> ParseResult | None:
    """Parse a URL, returning None when urlsplit rejects it (bracket-broken
    authorities in arbitrary code literals); None means the candidate is not a
    URL and callers must not treat it as a path or authority."""
    # urlparse(None) silently coerces through the bytes path instead of raising,
    # which would bypass every `parsed is None` guard downstream.
    if not isinstance(candidate, str):
        raise TypeError(f"expected str, got {type(candidate).__name__}")
    try:
        return urlparse(candidate)
    except ValueError:
        return None


def is_local_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False

    normalized: str = hostname.strip().lower()
    if normalized in _LOCAL_HOSTNAMES or normalized.endswith(".localhost"):
        return True

    try:
        host_ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return (
        host_ip.is_loopback
        or host_ip.is_unspecified
        or host_ip.is_private
        or host_ip.is_link_local
    )


def is_external_http_url(candidate: str) -> bool:
    parsed = safe_urlparse(candidate.strip())
    if parsed is None:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    return not is_local_hostname(parsed.hostname)


def classify_request_target(path: str, *, bare_token_is_local: bool) -> str:
    """Classify a request target as "local", "external", or "unknown".

    ``bare_token_is_local`` controls whether a relative candidate without a
    path separator still counts as a local target.
    """
    candidate = path.strip()
    if not candidate:
        return "unknown"

    # Parse before the leading-slash shortcut so bracket-broken literals
    # (XPath selectors like //iframe[@id=...]) land in unknown, not local.
    parsed = safe_urlparse(candidate)
    if parsed is None:
        return "unknown"

    if candidate.startswith("/"):
        return "local"

    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        if is_external_http_url(candidate):
            return "external"
        if is_local_hostname(parsed.hostname):
            return "local"
        return "unknown"

    if parsed.netloc or scheme:
        return "unknown"

    if bare_token_is_local or "/" in candidate:
        return "local"
    return "unknown"


def extract_query_param_names(path: str) -> set[str]:
    parsed = safe_urlparse(path)
    if parsed is None or not parsed.query:
        return set()
    return set(parse_qs(parsed.query, keep_blank_values=True).keys())
