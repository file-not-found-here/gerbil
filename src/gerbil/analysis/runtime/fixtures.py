from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FixtureMethod:
    defining_class_name: str
    method_signature: str
    is_ambiguous: bool = False
