"""Regression tests verifying CLDK position-reporting for method invocations.

The ``call_site_chains`` module uses a position-based heuristic:

* **Same start position** → receiver chain (chained calls).
* **Different start position** → argument nesting.

These tests parse real Java source via CLDK and assert the exact position
properties our heuristic depends on, then feed the call sites through
``build_call_site_grouping`` to verify grouping correctness.
"""

from __future__ import annotations

from cldk import CLDK
from cldk.models.java.models import JCallSite

from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    build_call_site_grouping,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_call_sites(
    source: str,
    class_name: str,
    method_substr: str,
) -> list[JCallSite]:
    """Parse *source* with CLDK and return call sites for the matching method.

    Parameters
    ----------
    source:
        Full Java source text (must contain exactly one top-level class).
    class_name:
        Simple or qualified class name to look up.
    method_substr:
        Substring that must appear in the method signature (e.g. ``"test"``).
    """
    analysis = CLDK(language="java").analysis(source_code=source)
    methods = analysis.get_methods_in_class(class_name)
    for sig, callable_ in methods.items():
        if method_substr in sig and callable_.call_sites:
            return list(callable_.call_sites)
    raise AssertionError(
        f"No call sites found for method matching {method_substr!r} "
        f"in class {class_name!r}"
    )


def _grouping_from(
    source: str, class_name: str, method_substr: str
) -> CallSiteGrouping:
    return build_call_site_grouping(
        _parse_call_sites(source, class_name, method_substr)
    )


def _by_name(call_sites: list[JCallSite], name: str) -> JCallSite:
    """Return the first call site with the given method_name."""
    for cs in call_sites:
        if cs.method_name == name:
            return cs
    raise AssertionError(f"No call site named {name!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSingleLineChainPositions:
    """``sb.append("a").append("b").append("c");`` on one line."""

    SOURCE = """\
public class Chain1 {
    public void testChain() {
        StringBuilder sb = new StringBuilder();
        sb.append("a").append("b").append("c");
    }
}
"""

    def test_all_share_start_position(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Chain1", "testChain")
        appends = [cs for cs in call_sites if cs.method_name == "append"]
        assert len(appends) == 3

        starts = {(cs.start_line, cs.start_column) for cs in appends}
        assert len(starts) == 1, f"Expected identical starts, got {starts}"

    def test_end_positions_strictly_increase(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Chain1", "testChain")
        appends = [cs for cs in call_sites if cs.method_name == "append"]
        ends = sorted(
            [(cs.end_line, cs.end_column) for cs in appends],
        )
        assert ends[0] < ends[1] < ends[2]

    def test_grouping_single_receiver_chain(self) -> None:
        grouping = _grouping_from(self.SOURCE, "Chain1", "testChain")

        # All appends land in one receiver chain.
        append_nodes = [
            n for n in grouping.nodes if n.call_site.method_name == "append"
        ]
        starts = {n.span.start for n in append_nodes}
        assert len(starts) == 1

        start = next(iter(starts))
        chain_nodes = grouping.receiver_chain_for(start.line, start.col)
        assert chain_nodes
        chain_names = [n.call_site.method_name for n in chain_nodes]
        assert chain_names == ["append", "append", "append"]

        # Nodes are ordered by end position.
        ends = [n.span.end for n in chain_nodes]
        assert ends == sorted(ends)


class TestMultiLineChainPositions:
    """Multi-line chain still shares start position."""

    SOURCE = """\
public class Chain2 {
    public void testChain() {
        StringBuilder sb = new StringBuilder();
        sb.append("a")
          .append("b")
          .append("c");
    }
}
"""

    def test_all_share_start_position(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Chain2", "testChain")
        appends = [cs for cs in call_sites if cs.method_name == "append"]
        assert len(appends) == 3

        starts = {(cs.start_line, cs.start_column) for cs in appends}
        assert len(starts) == 1

    def test_end_lines_increase(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Chain2", "testChain")
        appends = [cs for cs in call_sites if cs.method_name == "append"]
        end_lines = sorted({cs.end_line for cs in appends})
        assert len(end_lines) == 3
        assert end_lines == sorted(end_lines)

    def test_grouping_single_receiver_chain(self) -> None:
        grouping = _grouping_from(self.SOURCE, "Chain2", "testChain")

        append_nodes = [
            n for n in grouping.nodes if n.call_site.method_name == "append"
        ]
        starts = {n.span.start for n in append_nodes}
        assert len(starts) == 1

        start = next(iter(starts))
        chain_nodes = grouping.receiver_chain_for(start.line, start.col)
        assert chain_nodes
        assert len(chain_nodes) == 3

        ends = [n.span.end for n in chain_nodes]
        assert ends == sorted(ends)


class TestNestedArgumentPositions:
    """``list.add(String.valueOf(42));`` — nested argument, different starts."""

    SOURCE = """\
public class Nested1 {
    public void testNested() {
        java.util.List<String> list = new java.util.ArrayList<>();
        list.add(String.valueOf(42));
    }
}
"""

    def test_different_start_positions(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Nested1", "testNested")
        add = _by_name(call_sites, "add")
        valueOf = _by_name(call_sites, "valueOf")

        assert (add.start_line, add.start_column) != (
            valueOf.start_line,
            valueOf.start_column,
        )

    def test_add_span_contains_valueOf_span(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Nested1", "testNested")
        add = _by_name(call_sites, "add")
        valueOf = _by_name(call_sites, "valueOf")

        assert add.start_line <= valueOf.start_line
        assert add.end_line >= valueOf.end_line
        assert (add.start_line, add.start_column) <= (
            valueOf.start_line,
            valueOf.start_column,
        )
        assert (add.end_line, add.end_column) >= (
            valueOf.end_line,
            valueOf.end_column,
        )

    def test_grouping_argument_child(self) -> None:
        grouping = _grouping_from(self.SOURCE, "Nested1", "testNested")

        add_node = next(n for n in grouping.nodes if n.call_site.method_name == "add")
        valueOf_node = next(
            n for n in grouping.nodes if n.call_site.method_name == "valueOf"
        )

        assert add_node in grouping.roots or add_node.parent is not None
        assert valueOf_node in add_node.argument_children()
        assert valueOf_node not in add_node.receiver_children()


class TestMixedChainAndNestedArgumentPositions:
    """``perform(get("/api")).andExpect(null);`` — chain + nested arg."""

    SOURCE = """\
public class Mixed1 {
    public Object perform(Object o) { return o; }
    public Object get(String s) { return s; }
    public Object andExpect(Object o) { return o; }

    public void testMixed() {
        perform(get("/api")).andExpect(null);
    }
}
"""

    def test_perform_and_andExpect_share_start(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Mixed1", "testMixed")
        perform = _by_name(call_sites, "perform")
        andExpect = _by_name(call_sites, "andExpect")

        assert (perform.start_line, perform.start_column) == (
            andExpect.start_line,
            andExpect.start_column,
        )

    def test_get_has_different_start(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Mixed1", "testMixed")
        perform = _by_name(call_sites, "perform")
        get = _by_name(call_sites, "get")

        assert (perform.start_line, perform.start_column) != (
            get.start_line,
            get.start_column,
        )

    def test_grouping_structure(self) -> None:
        grouping = _grouping_from(self.SOURCE, "Mixed1", "testMixed")

        andExpect_node = next(
            n for n in grouping.nodes if n.call_site.method_name == "andExpect"
        )
        perform_node = next(
            n for n in grouping.nodes if n.call_site.method_name == "perform"
        )
        get_node = next(n for n in grouping.nodes if n.call_site.method_name == "get")

        # andExpect is the outermost (root), perform is its receiver child.
        assert andExpect_node in grouping.roots
        assert perform_node in andExpect_node.receiver_children()

        # get is an argument child of perform.
        assert get_node in perform_node.argument_children()


class TestFluentApiMultiLineChainPositions:
    """Six-call fluent chain across multiple lines."""

    SOURCE = """\
public class Fluent1 {
    public Fluent1 header(String k, String v) { return this; }
    public Fluent1 when_() { return this; }
    public Fluent1 get(String s) { return this; }
    public Fluent1 then_() { return this; }
    public Fluent1 statusCode(int c) { return this; }
    public static Fluent1 given() { return new Fluent1(); }

    public void testFluent() {
        given()
            .header("k", "v")
            .when_()
            .get("/api")
            .then_()
            .statusCode(200);
    }
}
"""

    EXPECTED_ORDER = ["given", "header", "when_", "get", "then_", "statusCode"]

    def test_all_share_start_position(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Fluent1", "testFluent")
        fluent_names = set(self.EXPECTED_ORDER)
        fluent_cs = [cs for cs in call_sites if cs.method_name in fluent_names]
        assert len(fluent_cs) == 6

        starts = {(cs.start_line, cs.start_column) for cs in fluent_cs}
        assert len(starts) == 1, f"Expected identical starts, got {starts}"

    def test_end_positions_grow_monotonically(self) -> None:
        call_sites = _parse_call_sites(self.SOURCE, "Fluent1", "testFluent")
        fluent_names = set(self.EXPECTED_ORDER)
        fluent_cs = [cs for cs in call_sites if cs.method_name in fluent_names]

        ends = sorted([(cs.end_line, cs.end_column) for cs in fluent_cs])
        for i in range(len(ends) - 1):
            assert ends[i] < ends[i + 1], f"End positions not monotonic at index {i}"

    def test_grouping_single_chain_six_nodes(self) -> None:
        grouping = _grouping_from(self.SOURCE, "Fluent1", "testFluent")

        fluent_names = set(self.EXPECTED_ORDER)
        fluent_nodes = [
            n for n in grouping.nodes if n.call_site.method_name in fluent_names
        ]
        starts = {n.span.start for n in fluent_nodes}
        assert len(starts) == 1

        start = next(iter(starts))
        chain_nodes = grouping.receiver_chain_for(start.line, start.col)
        assert chain_nodes
        assert len(chain_nodes) == 6

        chain_names = [n.call_site.method_name for n in chain_nodes]
        assert chain_names == self.EXPECTED_ORDER

        ends = [n.span.end for n in chain_nodes]
        assert ends == sorted(ends)
