from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Percentiles reported by every numeric distribution, in output order.
PERCENTILES: tuple[int, ...] = (25, 50, 75, 90)


@dataclass(frozen=True)
class Distribution:
    """Summary statistics over a numeric sample (None fields when the sample is empty)."""

    count: int
    min: float | None
    max: float | None
    mean: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p90: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "p25": self.p25,
            "p50": self.p50,
            "p75": self.p75,
            "p90": self.p90,
        }


@dataclass(frozen=True)
class Share:
    """Proportion of a boolean sample that is True (None proportion when empty)."""

    count: int
    total: int
    proportion: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "total": self.total,
            "proportion": self.proportion,
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (NumPy/R-7 method) over a sorted sample."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = (q / 100.0) * (n - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[low]
    fraction = rank - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def summarize(values: Iterable[float]) -> Distribution:
    """Min/max/mean and the 25/50/75/90 percentiles of a numeric sample."""
    data = sorted(float(value) for value in values)
    if not data:
        return Distribution(
            count=0,
            min=None,
            max=None,
            mean=None,
            p25=None,
            p50=None,
            p75=None,
            p90=None,
        )
    return Distribution(
        count=len(data),
        min=data[0],
        max=data[-1],
        mean=sum(data) / len(data),
        p25=_percentile(data, 25),
        p50=_percentile(data, 50),
        p75=_percentile(data, 75),
        p90=_percentile(data, 90),
    )


def share(flags: Iterable[bool]) -> Share:
    """Count and proportion of True values in a boolean sample."""
    total = 0
    true_count = 0
    for flag in flags:
        total += 1
        if flag:
            true_count += 1
    return Share(
        count=true_count,
        total=total,
        proportion=(true_count / total) if total else None,
    )


def count_share(count: int, total: int) -> Share:
    """Share of a precomputed count over a known total (None proportion when empty)."""
    return Share(
        count=count,
        total=total,
        proportion=(count / total) if total else None,
    )


def count_share_entries(
    counts: Mapping[str, int], keys: Sequence[str], total: int
) -> dict[str, dict[str, float | int | None]]:
    """Map each key to its count_share dict over the total; absent keys score 0."""
    return {key: count_share(counts.get(key, 0), total).to_dict() for key in keys}


def status_code_sort_key(status_code: str) -> tuple[int, int | str]:
    """Order numeric status-code keys numerically, then non-numeric ones lexically."""
    try:
        return (0, int(status_code))
    except ValueError:
        return (1, status_code)
