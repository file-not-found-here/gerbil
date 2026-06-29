from __future__ import annotations

from collections import OrderedDict
from collections.abc import MutableMapping
from typing import TypeVar

from cldk.analysis.java import JavaAnalysis

from gerbil.analysis.shared.reachability import Reachability

# ---------------------------------------------------------------------------
# bounded_cache
# ---------------------------------------------------------------------------

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")


def cache_get(cache: OrderedDict[_KeyT, _ValueT], key: _KeyT) -> _ValueT | None:
    cached_value = cache.get(key)
    if cached_value is None:
        return None
    cache.move_to_end(key)
    return cached_value


def cache_put(
    cache: OrderedDict[_KeyT, _ValueT],
    key: _KeyT,
    value: _ValueT,
    max_entries: int,
) -> None:
    if key in cache:
        cache.move_to_end(key)
    cache[key] = value
    if len(cache) > max_entries:
        cache.popitem(last=False)


def cache_put_bounded(
    cache: MutableMapping[_KeyT, _ValueT],
    key: _KeyT,
    value: _ValueT,
    max_entries: int,
) -> None:
    if key in cache:
        cache.pop(key)
    cache[key] = value
    if len(cache) <= max_entries:
        return
    oldest_key = next(iter(cache))
    cache.pop(oldest_key)


# ---------------------------------------------------------------------------
# receiver_hierarchy_cache
# ---------------------------------------------------------------------------

CLASS_RESOLUTION_CACHE_MAX_ENTRIES: int = 4_096
CLASS_RESOLUTION_CACHE: OrderedDict[tuple[JavaAnalysis, str], tuple[str, ...]] = (
    OrderedDict()
)


def reset_class_resolution_cache() -> None:
    """Clear the class hierarchy resolution cache.

    This resets cached class hierarchy resolution entries used by multiple
    property analyzers.
    """

    CLASS_RESOLUTION_CACHE.clear()


def get_receiver_hierarchy(
    receiver_type: str,
    analysis: JavaAnalysis,
) -> tuple[str, ...]:
    """Resolve and cache class hierarchy candidates for a receiver type.

    Args:
        receiver_type: Fully-qualified receiver type.
        analysis: Project-level Java analysis instance.

    Returns:
        Ordered candidate types beginning with ``receiver_type`` followed by
        resolved supertypes/interfaces when available.
    """

    cache_key = (analysis, receiver_type)
    cached_hierarchy = cache_get(CLASS_RESOLUTION_CACHE, cache_key)
    if cached_hierarchy is not None:
        return cached_hierarchy

    receiver_hierarchy: list[str] = [receiver_type]
    if analysis.get_class(receiver_type):
        reachability = Reachability(analysis)
        for resolved_type in reachability.get_class_resolution_order(receiver_type):
            if resolved_type not in receiver_hierarchy:
                receiver_hierarchy.append(resolved_type)

    resolved_hierarchy = tuple(receiver_hierarchy)
    cache_put(
        cache=CLASS_RESOLUTION_CACHE,
        key=cache_key,
        value=resolved_hierarchy,
        max_entries=CLASS_RESOLUTION_CACHE_MAX_ENTRIES,
    )
    return resolved_hierarchy


__all__ = [
    "CLASS_RESOLUTION_CACHE",
    "CLASS_RESOLUTION_CACHE_MAX_ENTRIES",
    "cache_get",
    "cache_put",
    "cache_put_bounded",
    "get_receiver_hierarchy",
    "reset_class_resolution_cache",
]
