from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from cldk.models.java.models import JCallSite

if TYPE_CHECKING:
    from gerbil.analysis.schema import (
        AssertionClassification,
        EndpointCandidate,
        HttpClassification,
    )


@dataclass(frozen=True, order=True)
class Pos:
    """One-indexed source position."""

    line: int
    col: int


@dataclass(frozen=True)
class Span:
    """Inclusive source span for a call site."""

    start: Pos
    end: Pos

    def contains(self, other: Span) -> bool:
        return (
            self.start <= other.start
            and other.end <= self.end
            and (self.start < other.start or other.end < self.end)
        )


@dataclass(frozen=True)
class MethodRef:
    """Identifies a Java method by its defining class and signature."""

    defining_class_name: str
    method_signature: str


@dataclass
class HelperExpansion:
    """Cross-method expansion attached to a helper call-site node."""

    callee: MethodRef
    grouping: CallSiteGrouping


@dataclass(frozen=True)
class PathRecovery:
    """Records a helper-argument path adoption for expansion-based upgrade."""

    helper_node: CallSiteNode
    adopted_base: str


@dataclass
class CallSiteNode:
    """A call-site node in the containment tree."""

    call_site: JCallSite
    span: Span
    parent: CallSiteNode | None = field(default=None, repr=False)
    children: list[CallSiteNode] = field(default_factory=list, repr=False)
    resolved_helper: MethodRef | None = field(default=None, repr=False)
    helper_expansion: HelperExpansion | None = field(default=None, repr=False)
    http_classification: HttpClassification | None = field(default=None, repr=False)
    endpoint_candidate: EndpointCandidate | None = field(default=None, repr=False)
    assertion_classification: AssertionClassification | None = field(
        default=None, repr=False
    )
    path_recovery: PathRecovery | None = field(default=None, repr=False)

    def receiver_children(self) -> list[CallSiteNode]:
        """Children on the same receiver backbone (same start position)."""

        return [child for child in self.children if child.span.start == self.span.start]

    def argument_children(self) -> list[CallSiteNode]:
        """Children nested in arguments (different start position)."""

        return [child for child in self.children if child.span.start != self.span.start]

    def all_descendants(self) -> list[CallSiteNode]:
        """Depth-first traversal of descendants."""

        descendants: list[CallSiteNode] = []
        stack: list[CallSiteNode] = list(reversed(self.children))
        while stack:
            node = stack.pop()
            descendants.append(node)
            stack.extend(reversed(node.children))
        return descendants


def _end_pos_key(node: CallSiteNode) -> tuple[int, int]:
    return (node.span.end.line, node.span.end.col)


@dataclass
class CallSiteGrouping:
    """Containment tree over method call sites."""

    roots: list[CallSiteNode]
    nodes: list[CallSiteNode]

    def receiver_chain_for(self, line: int, col: int) -> list[CallSiteNode]:
        """Return nodes sharing the given start position, sorted by end position."""
        pos = Pos(line=line, col=col)
        matching = [node for node in self.nodes if node.span.start == pos]
        return sorted(matching, key=_end_pos_key)

    def receiver_chains(self) -> list[list[CallSiteNode]]:
        """Return all receiver chains, sorted by start position.

        Each chain is a list of nodes sharing the same start position,
        sorted internally by end position (innermost to outermost).
        """
        grouped: dict[Pos, list[CallSiteNode]] = {}
        for node in self.nodes:
            grouped.setdefault(node.span.start, []).append(node)
        return [sorted(group, key=_end_pos_key) for _, group in sorted(grouped.items())]

    def node_for_call_site(self, call_site: JCallSite) -> CallSiteNode | None:
        for node in self.nodes:
            if node.call_site is call_site:
                return node
        return None


@dataclass(frozen=True)
class ExpandedCallSiteEvent:
    """A single node yielded during expanded evaluation-order traversal."""

    owner: MethodRef
    node: CallSiteNode
    depth: int


def iter_expanded_evaluation_order(
    grouping: CallSiteGrouping,
    *,
    owner: MethodRef,
    depth: int = 0,
) -> Iterator[ExpandedCallSiteEvent]:
    """Yield call-site nodes in source order, inlining helper expansions."""

    sorted_roots = sorted(
        grouping.roots,
        key=lambda node: (node.span.start.line, node.span.start.col),
    )

    for root in sorted_roots:
        yield from _walk_node_expanded(root, owner=owner, depth=depth)


def _walk_node_expanded(
    node: CallSiteNode,
    *,
    owner: MethodRef,
    depth: int,
) -> Iterator[ExpandedCallSiteEvent]:
    """Walk a node and descendants in source evaluation order."""

    for child in sorted(node.receiver_children(), key=_end_pos_key):
        yield from _walk_node_expanded(child, owner=owner, depth=depth)

    for child in sorted(
        node.argument_children(),
        key=lambda arg: (arg.span.start.line, arg.span.start.col),
    ):
        yield from _walk_node_expanded(child, owner=owner, depth=depth)

    yield ExpandedCallSiteEvent(owner=owner, node=node, depth=depth)

    if node.helper_expansion is not None:
        yield from iter_expanded_evaluation_order(
            node.helper_expansion.grouping,
            owner=node.helper_expansion.callee,
            depth=depth + 1,
        )


def iter_resolved_helpers(grouping: CallSiteGrouping) -> Iterator[MethodRef]:
    """Yield unique helper MethodRefs from an expanded grouping.

    Includes helpers at the depth cutoff (resolved_helper set, no helper_expansion)
    and fully expanded helpers. Each MethodRef is yielded at most once.
    """

    seen: set[MethodRef] = set()
    stack: list[CallSiteGrouping] = [grouping]
    while stack:
        current = stack.pop()
        for node in current.nodes:
            if node.resolved_helper is not None and node.resolved_helper not in seen:
                seen.add(node.resolved_helper)
                yield node.resolved_helper
            if node.helper_expansion is not None:
                stack.append(node.helper_expansion.grouping)


ResolveHelper = Callable[[MethodRef, JCallSite], MethodRef | None]
LoadCallSites = Callable[[MethodRef], list[JCallSite] | None]


def build_expanded_call_site_grouping(
    call_sites: list[JCallSite],
    *,
    owner: MethodRef,
    resolve_helper: ResolveHelper,
    load_call_sites: LoadCallSites,
    max_helper_depth: int = 1,
) -> CallSiteGrouping:
    """Build a call-site grouping with recursive helper expansion."""

    if max_helper_depth < 0:
        raise ValueError("max_helper_depth must be non-negative")

    grouping = build_call_site_grouping(call_sites)

    call_stack: set[MethodRef] = {owner}
    _expand_nodes(
        nodes=grouping.nodes,
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=max_helper_depth,
        current_depth=0,
        call_stack=call_stack,
    )
    return grouping


def _expand_nodes(
    nodes: list[CallSiteNode],
    *,
    owner: MethodRef,
    resolve_helper: ResolveHelper,
    load_call_sites: LoadCallSites,
    max_helper_depth: int,
    current_depth: int,
    call_stack: set[MethodRef],
) -> None:
    """Attach helper expansions for nodes recognized as helper calls."""

    for node in nodes:
        callee_ref = resolve_helper(owner, node.call_site)
        if callee_ref is None:
            continue
        node.resolved_helper = callee_ref
        if current_depth >= max_helper_depth:
            continue
        if callee_ref in call_stack:
            continue

        callee_call_sites = load_call_sites(callee_ref)
        if not callee_call_sites:
            continue

        callee_grouping = build_call_site_grouping(callee_call_sites)

        call_stack.add(callee_ref)
        _expand_nodes(
            nodes=callee_grouping.nodes,
            owner=callee_ref,
            resolve_helper=resolve_helper,
            load_call_sites=load_call_sites,
            max_helper_depth=max_helper_depth,
            current_depth=current_depth + 1,
            call_stack=call_stack,
        )
        call_stack.discard(callee_ref)

        node.helper_expansion = HelperExpansion(
            callee=callee_ref, grouping=callee_grouping
        )


def _span_from_call_site(call_site: JCallSite) -> Span:
    start_line: int = int(call_site.start_line)
    start_col: int = int(call_site.start_column)

    end_line_raw = (
        int(call_site.end_line) if call_site.end_line is not None else start_line
    )
    end_col_raw = (
        int(call_site.end_column) if call_site.end_column is not None else start_col
    )
    end_line: int = end_line_raw if end_line_raw > 0 else start_line
    end_col: int = end_col_raw if end_col_raw > 0 else start_col

    if (end_line, end_col) < (start_line, start_col):
        end_line, end_col = start_line, start_col

    return Span(
        start=Pos(line=start_line, col=start_col), end=Pos(line=end_line, col=end_col)
    )


def build_call_site_grouping(call_sites: list[JCallSite]) -> CallSiteGrouping:
    """Build containment trees from call-site positional spans."""

    if not call_sites:
        return CallSiteGrouping(roots=[], nodes=[])

    nodes: list[CallSiteNode] = [
        CallSiteNode(call_site=call_site, span=_span_from_call_site(call_site))
        for call_site in call_sites
    ]

    nodes_sorted: list[CallSiteNode] = sorted(
        nodes,
        key=lambda node: (
            node.span.start.line,
            node.span.start.col,
            -node.span.end.line,
            -node.span.end.col,
        ),
    )

    stack: list[CallSiteNode] = []
    roots: list[CallSiteNode] = []
    for node in nodes_sorted:
        while stack and not stack[-1].span.contains(node.span):
            stack.pop()

        if stack:
            node.parent = stack[-1]
            stack[-1].children.append(node)
        else:
            roots.append(node)

        stack.append(node)

    return CallSiteGrouping(roots=roots, nodes=nodes)


# ---------------------------------------------------------------------------
# Call-site key utilities (merged from call_site_keys.py)
# ---------------------------------------------------------------------------

CallSiteKey = tuple[int, int, int, int, str]


def call_site_key(call_site: JCallSite) -> CallSiteKey:
    """Build a stable positional key for a call site."""
    method_name: str = call_site.method_name or ""
    end_line = int(call_site.end_line) if call_site.end_line is not None else -1
    end_column = int(call_site.end_column) if call_site.end_column is not None else -1
    return (
        int(call_site.start_line),
        int(call_site.start_column),
        end_line,
        end_column,
        method_name,
    )
