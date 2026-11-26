"""Draw the seeded random sample of a service's mutants that every suite is scored on."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

# Cochran's n = z^2 p(1-p) / e^2 at 95% confidence (z = 1.96), the widest variance
# (p = 0.5) and e = 0.02: a mutation score over the sample is within 2 points of the
# score over every mutant, 19 times in 20.
DEFAULT_SAMPLE_SIZE = 2401
DEFAULT_SAMPLE_SEED = "gerbil"


def sample_mutants(mutant_ids: Iterable[str], size: int, seed: str) -> list[str]:
    """`size` of the mutants, each equally likely, or all of them if there are fewer.

    Mutants are ranked by a hash of the seed and their id, so the sample needs no
    random number generator to reproduce and a larger sample contains a smaller one.
    """
    ranked = sorted(set(mutant_ids), key=lambda mutant_id: _rank(seed, mutant_id))
    return sorted(ranked[:size])


def _rank(seed: str, mutant_id: str) -> tuple[bytes, str]:
    return hashlib.sha256(f"{seed}\0{mutant_id}".encode()).digest(), mutant_id


def sample_digest(sample: Iterable[str]) -> str:
    """Identifies a sample, so results of another one are not scored together."""
    return hashlib.sha256("\n".join(sorted(sample)).encode()).hexdigest()


def mutated_class(mutant_id: str) -> str:
    return mutant_id.split("|", 1)[0]
