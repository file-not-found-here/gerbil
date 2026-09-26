from __future__ import annotations

import re
from collections.abc import Callable

from gerbil.analysis.shared.url_utils import is_local_hostname, safe_urlparse

_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_24_RE = re.compile(r"^[0-9a-fA-F]{24}$")
_HEX_32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PRODUCTION_TEMPLATE_SEGMENT_RE = re.compile(r"^\{(\*?)([^{}:]+)(?::.+)?\}$")
_MULTI_SLASH_RE = re.compile(r"/{2,}")
_JAVA_FORMAT_SPECIFIER_RE = re.compile(r"^%(?:\d+\$)?\d*[sdxX]$")
# Sentinel placeholders for an unresolvable path. Only angle-bracketed forms
# qualify: they cannot occur in a real URL, so rejecting any path containing one
# is always safe. Bare words ("dynamic", "unknown", "unresolved") are excluded
# because they are legitimate literal route segments (e.g.
# /api/apps/{monitorId}/define/dynamic); a genuinely unresolved path arrives as
# the empty string, which is rejected separately.
_UNRESOLVED_PATH_VALUES: set[str] = {
    "<dynamic>",
    "<unknown>",
    "<unresolved>",
}

_ID_PLACEHOLDER = "{id}"
_ID_SUFFIX = f"/{_ID_PLACEHOLDER}"


def _is_unresolved_segment(segment: str) -> bool:
    normalized_segment = segment.strip().lower()
    return not normalized_segment or normalized_segment in _UNRESOLVED_PATH_VALUES


def _is_id_segment(segment: str) -> bool:
    normalized_segment = segment.strip()
    if normalized_segment.isdigit():
        return True
    if _UUID_SEGMENT_RE.fullmatch(normalized_segment):
        return True
    if _HEX_24_RE.fullmatch(normalized_segment):
        return True
    if _HEX_32_RE.fullmatch(normalized_segment):
        return True
    return bool(_ISO_DATE_RE.fullmatch(normalized_segment))


def _is_single_balanced_template_group(segment: str) -> bool:
    """True when the entire segment is one ``{…}`` group.

    Constraints may contain nested braces (e.g. ``{id:\\d{4}}``), but the
    outer group must span the whole segment: ``{a}{b}`` or ``{a:\\d+}{b}``
    must not be treated as a single variable segment.
    """
    if len(segment) < 2 or segment[0] != "{" or segment[-1] != "}":
        return False
    depth = 0
    for index, char in enumerate(segment):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
            if depth == 0 and index != len(segment) - 1:
                return False
    return depth == 0


def _normalize_segments(
    path: str | None,
    segment_normalizer: Callable[[str], str | None],
) -> str | None:
    if path is None:
        return None

    candidate = path.strip()
    if not candidate:
        return None

    parsed = safe_urlparse(candidate)
    if parsed is None:
        # Unparseable literals are not request paths; drop them rather than
        # minting a garbage resource key (normalize_path keeps them opaque
        # instead because its str contract has no rejection channel).
        return None
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        if not is_local_hostname(parsed.hostname):
            return None
        normalized_path = parsed.path or "/"
    elif candidate.startswith("//") and parsed.netloc:
        # A scheme-less leading "//" is a concat artifact, not an authority.
        # Collapse the slashes before parsing so the first segment stays in the
        # path (mirrors normalize_path in http_mapping_annotations).
        collapsed = f"/{candidate.lstrip('/')}"
        parsed = safe_urlparse(collapsed)
        if parsed is None:
            return None
        normalized_path = parsed.path or collapsed
    else:
        normalized_path = parsed.path or candidate

    normalized_path = _MULTI_SLASH_RE.sub("/", normalized_path.strip())
    if not normalized_path:
        return None

    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    if len(normalized_path) > 1:
        normalized_path = normalized_path.rstrip("/")

    normalized_segments: list[str] = []
    for segment in normalized_path.split("/"):
        if not segment:
            continue
        normalized_segment = segment_normalizer(segment)
        if normalized_segment is None:
            return None
        normalized_segments.append(normalized_segment)

    if not normalized_segments:
        return "/"

    return "/" + "/".join(normalized_segments)


def _normalize_template_segment(segment: str) -> str | None:
    normalized_segment = segment.strip()
    if _is_unresolved_segment(normalized_segment):
        return None

    if _is_single_balanced_template_group(normalized_segment):
        template_match = _PRODUCTION_TEMPLATE_SEGMENT_RE.fullmatch(normalized_segment)
        if template_match is not None:
            if template_match.group(1):
                return None
            return _ID_PLACEHOLDER

    if _JAVA_FORMAT_SPECIFIER_RE.fullmatch(normalized_segment):
        return _ID_PLACEHOLDER

    if "*" in normalized_segment or "%" in normalized_segment:
        return None

    if normalized_segment.startswith(":") and len(normalized_segment) > 1:
        return _ID_PLACEHOLDER
    if normalized_segment.startswith("${"):
        return None
    return _ID_PLACEHOLDER if _is_id_segment(normalized_segment) else normalized_segment


def normalize_request_path(path: str | None) -> str | None:
    return _normalize_segments(path, _normalize_template_segment)


def normalize_production_resource_key(path_template: str | None) -> str | None:
    normalized_path = _normalize_segments(path_template, _normalize_template_segment)
    if normalized_path is None:
        return None
    return resource_key(normalized_path)


def resource_key(normalized_path: str) -> str:
    """Derive the collection-level resource key from a normalized path.

    Strips a single trailing ``/{id}`` segment so that instance paths
    (``/users/{id}``) group with their collection path (``/users``).
    """
    if normalized_path.endswith(_ID_SUFFIX):
        return normalized_path[: -len(_ID_SUFFIX)]
    return normalized_path


def strip_application_path_prefix(normalized_path: str, prefix: str) -> str | None:
    """Strip a discovered @ApplicationPath prefix segment-wise off a normalized path.

    The prefix runs through the same request-path normalization as observed
    paths so segments compare like-for-like, and must lead the path as whole
    segments (``/api`` must not strip ``/apiserver``). Prefixes with ID-like
    segments (e.g. a numeric @ApplicationPath) are rejected: normalization
    abstracts them to ``{id}``, which would strip any ID-led path rather than
    only paths under that mount. Returns the remainder (``"/"`` when nothing
    is left) or ``None`` when the prefix does not apply.
    """
    normalized_prefix = normalize_request_path(prefix)
    if normalized_prefix is None or normalized_prefix == "/":
        return None
    prefix_segments = normalized_prefix.strip("/").split("/")
    if _ID_PLACEHOLDER in prefix_segments:
        return None
    path_segments = [segment for segment in normalized_path.split("/") if segment]
    if path_segments[: len(prefix_segments)] != prefix_segments:
        return None
    remainder_segments = path_segments[len(prefix_segments) :]
    return "/" + "/".join(remainder_segments) if remainder_segments else "/"
