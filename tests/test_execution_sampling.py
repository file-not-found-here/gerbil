from __future__ import annotations

import math
from collections import Counter

from gerbil.execution.sampling import (
    DEFAULT_SAMPLE_SIZE,
    mutated_class,
    sample_digest,
    sample_mutants,
)

_MUTANTS = [
    f"org.demo.Class{index % 40}|method|()V|Mutator|{index}" for index in range(900)
]


def test_default_sample_size_bounds_the_error_at_two_points() -> None:
    assert DEFAULT_SAMPLE_SIZE == math.ceil(1.96**2 * 0.25 / 0.02**2)


def test_sample_is_the_same_for_any_order_and_duplicates_of_the_mutants() -> None:
    sample = sample_mutants(_MUTANTS, 100, "seed")

    assert len(sample) == 100 and sample == sorted(set(sample))
    assert set(sample) < set(_MUTANTS)
    assert sample_mutants([*reversed(_MUTANTS), *_MUTANTS], 100, "seed") == sample


def test_another_seed_draws_another_sample() -> None:
    assert sample_mutants(_MUTANTS, 100, "seed") != sample_mutants(
        _MUTANTS, 100, "other"
    )


def test_a_larger_sample_contains_a_smaller_one() -> None:
    assert set(sample_mutants(_MUTANTS, 100, "seed")) < set(
        sample_mutants(_MUTANTS, 300, "seed")
    )


def test_a_sample_no_smaller_than_the_population_is_every_mutant() -> None:
    assert sample_mutants(_MUTANTS, len(_MUTANTS), "seed") == sorted(_MUTANTS)
    assert sample_mutants(_MUTANTS, 5000, "seed") == sorted(_MUTANTS)


def test_sample_spreads_over_classes_in_proportion_to_their_mutants() -> None:
    by_class = Counter(
        mutated_class(mutant) for mutant in sample_mutants(_MUTANTS, 450, "seed")
    )

    # Half of each class's 22 or 23 mutants are expected; none is left out or favoured.
    assert len(by_class) == 40
    assert 4 <= min(by_class.values()) and max(by_class.values()) <= 19


def test_digest_identifies_the_sample_whatever_its_order() -> None:
    sample = sample_mutants(_MUTANTS, 100, "seed")

    assert sample_digest(sample) == sample_digest(reversed(sample))
    assert sample_digest(sample) != sample_digest(sample[1:])


def test_mutated_class_is_the_binary_class_name() -> None:
    assert (
        mutated_class("org.demo.Outer$Inner|run|(I)V|Mutator|4,9")
        == "org.demo.Outer$Inner"
    )
