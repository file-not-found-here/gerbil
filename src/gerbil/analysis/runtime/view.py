from __future__ import annotations

from dataclasses import dataclass, field

from cldk.models.java import JCallable
from cldk.models.java.models import JCallSite

from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    CallSiteNode,
    MethodRef,
    iter_expanded_evaluation_order,
)
from gerbil.analysis.schema import CallSiteOriginKind, LifecyclePhase, OriginContext


@dataclass
class PhaseEntry:
    """A single method in the test execution timeline with expanded call-site data."""

    phase: LifecyclePhase
    method_ref: MethodRef
    context_class_name: str
    grouping: CallSiteGrouping
    method_details: JCallable | None
    is_group_ambiguous: bool = False


@dataclass(frozen=True)
class RuntimeEvent:
    """A single call-site event in the materialized test execution timeline."""

    phase: LifecyclePhase
    owner: MethodRef
    node: CallSiteNode
    depth: int
    entry_method_ref: MethodRef | None = None
    is_group_ambiguous: bool = False

    @property
    def call_site(self) -> JCallSite:
        return self.node.call_site

    @property
    def origin_kind(self) -> CallSiteOriginKind:
        return call_site_origin_kind(phase=self.phase, depth=self.depth)

    @property
    def origin_context(self) -> OriginContext:
        entry_method_ref = self.entry_method_ref or self.owner
        return OriginContext(
            phase=self.phase,
            kind=self.origin_kind,
            defining_class_name=self.owner.defining_class_name,
            method_signature=self.owner.method_signature,
            entry_defining_class_name=entry_method_ref.defining_class_name,
            entry_method_signature=entry_method_ref.method_signature,
            depth=self.depth,
            is_group_ambiguous=self.is_group_ambiguous,
        )


def call_site_origin_kind(
    *,
    phase: LifecyclePhase,
    depth: int,
) -> CallSiteOriginKind:
    if phase == LifecyclePhase.TEST:
        return (
            CallSiteOriginKind.TEST_METHOD
            if depth == 0
            else CallSiteOriginKind.TEST_HELPER
        )
    return (
        CallSiteOriginKind.FIXTURE if depth == 0 else CallSiteOriginKind.FIXTURE_HELPER
    )


@dataclass
class TestRuntimeView:
    """Ordered representation of setup, test, and teardown execution context."""

    entries: list[PhaseEntry] = field(default_factory=list)
    _cached_events: list[RuntimeEvent] | None = field(default=None, repr=False)
    _cached_entries_signature: (
        tuple[tuple[int, LifecyclePhase, MethodRef, int], ...] | None
    ) = field(
        default=None,
        repr=False,
    )

    def test_entry(self) -> PhaseEntry | None:
        for entry in self.entries:
            if entry.phase == LifecyclePhase.TEST:
                return entry
        return None

    def phase_entries(self, phase: LifecyclePhase) -> list[PhaseEntry]:
        return [entry for entry in self.entries if entry.phase == phase]

    def _entries_signature(
        self,
        entries: tuple[PhaseEntry, ...] | None = None,
    ) -> tuple[tuple[int, LifecyclePhase, MethodRef, int], ...]:
        if entries is None:
            entries = tuple(self.entries)
        return tuple(
            (
                id(entry),
                entry.phase,
                entry.method_ref,
                id(entry.grouping),
            )
            for entry in entries
        )

    @staticmethod
    def _materialize_events(
        entries: tuple[PhaseEntry, ...],
    ) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for entry in entries:
            for event in iter_expanded_evaluation_order(
                entry.grouping,
                owner=entry.method_ref,
            ):
                events.append(
                    RuntimeEvent(
                        phase=entry.phase,
                        owner=event.owner,
                        node=event.node,
                        depth=event.depth,
                        entry_method_ref=entry.method_ref,
                        is_group_ambiguous=entry.is_group_ambiguous,
                    )
                )
        return events

    def iter_events(self) -> list[RuntimeEvent]:
        """Materialized list of all runtime events across all phases, lazily cached."""

        current_signature = self._entries_signature()
        if (
            self._cached_events is not None
            and self._cached_entries_signature == current_signature
        ):
            return self._cached_events

        while True:
            entries_snapshot = tuple(self.entries)
            snapshot_signature = self._entries_signature(entries_snapshot)
            events = self._materialize_events(entries_snapshot)

            # If entries changed during materialization, retry so cached values
            # always represent a coherent snapshot.
            if snapshot_signature != self._entries_signature():
                continue

            self._cached_events = events
            self._cached_entries_signature = snapshot_signature
            return events

    def test_events(self) -> list[RuntimeEvent]:
        """Runtime events for the TEST phase only."""

        return [
            event for event in self.iter_events() if event.phase == LifecyclePhase.TEST
        ]

    def phase_events(self, phase: LifecyclePhase) -> list[RuntimeEvent]:
        """Runtime events for a specific phase."""

        return [event for event in self.iter_events() if event.phase == phase]


# Prevent pytest from treating dataclasses prefixed with "Test" as test classes.
setattr(PhaseEntry, "__test__", False)
setattr(RuntimeEvent, "__test__", False)
setattr(TestRuntimeView, "__test__", False)
