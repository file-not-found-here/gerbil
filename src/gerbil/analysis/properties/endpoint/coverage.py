from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache

from gerbil.analysis.schema import (
    ApplicationEndpoint,
    AssertionRole,
    EndpointAssertedStatusOutcomes,
    EndpointCandidate,
    EndpointCoverageEntry,
    EndpointCoverageSummary,
    HttpRequestRole,
    SequenceStepKind,
    TestClassAnalysis,
    TestMethodAnalysis,
    TestMethodReference,
)
from gerbil.analysis.properties.endpoint.extraction import (
    _REQUEST_TARGET_EXTERNAL,
    normalize_observed_path_with_context,
    normalize_path,
)

_TEMPLATE_VARIABLE_SEGMENT_RE: re.Pattern[str] = re.compile(
    r"^\{(\*?)([^{}:]+)(?::(.+))?\}$"
)
_PATH_TEMPLATE_SEGMENT_CACHE_SIZE: int = 4096


def _http_methods_match(
    endpoint: ApplicationEndpoint,
    observed_method: str,
) -> bool:
    normalized_production_method = (endpoint.http_method or "UNKNOWN").upper()
    normalized_observed_method = (observed_method or "UNKNOWN").upper()

    if normalized_observed_method == "UNKNOWN":
        return False
    if normalized_production_method == "UNKNOWN" and endpoint.is_method_wildcard:
        return True
    if normalized_production_method == "UNKNOWN":
        return False
    return normalized_production_method == normalized_observed_method


def _compile_literal_or_glob_segment_regex(segment: str) -> re.Pattern[str]:
    if "**" in segment:
        return re.compile(rf"^{re.escape(segment)}$")
    if "*" not in segment:
        return re.compile(rf"^{re.escape(segment)}$")

    escaped_parts = [re.escape(part) for part in segment.split("*")]
    return re.compile(rf"^{r'[^/]*'.join(escaped_parts)}$")


def _template_variable_constraint_regex(constraint: str) -> str:
    # Annotation literals arrive as raw Java source where a regex `\d` is
    # written `\\d`; the JAX-RS template grammar also ignores whitespace
    # around the constraint.
    unescaped_constraint = constraint.strip().replace("\\\\", "\\")
    return f"(?:{unescaped_constraint})"


def _scan_embedded_template_variables(
    segment: str,
) -> list[tuple[bool, str]] | None:
    """Split a segment into (is_variable, text) tokens around balanced ``{…}``.

    Returns None when the segment holds no balanced variable group, so callers
    fall back to literal/glob compilation.
    """
    tokens: list[tuple[bool, str]] = []
    literal_start = 0
    index = 0
    found_variable = False
    while index < len(segment):
        if segment[index] != "{":
            index += 1
            continue
        depth = 0
        close_index = -1
        for scan_index in range(index, len(segment)):
            if segment[scan_index] == "{":
                depth += 1
            elif segment[scan_index] == "}":
                depth -= 1
                if depth == 0:
                    close_index = scan_index
                    break
        if close_index < 0:
            return None
        if index > literal_start:
            tokens.append((False, segment[literal_start:index]))
        tokens.append((True, segment[index + 1 : close_index]))
        found_variable = True
        literal_start = close_index + 1
        index = close_index + 1
    if not found_variable:
        return None
    if literal_start < len(segment):
        tokens.append((False, segment[literal_start:]))
    return tokens


def _compile_embedded_variable_segment_regex(segment: str) -> re.Pattern[str]:
    """Compile a segment with ``{var}`` groups embedded in literal text.

    Spring and JAX-RS both match e.g. ``file-{name}.txt`` at runtime, so the
    literal remainder is escaped (with single-star globs honored) and each
    variable group becomes its constraint or ``[^/]+``.
    """
    embedded_tokens = _scan_embedded_template_variables(segment)
    if embedded_tokens is None:
        return _compile_literal_or_glob_segment_regex(segment)

    pattern_parts: list[str] = []
    for is_variable, text in embedded_tokens:
        if not is_variable:
            pattern_parts.append(
                r"[^/]*".join(re.escape(part) for part in text.split("*"))
            )
            continue
        name, separator, constraint = text.partition(":")
        if separator:
            pattern_parts.append(_template_variable_constraint_regex(constraint))
        else:
            pattern_parts.append(r"[^/]+")
    try:
        return re.compile(rf"^{''.join(pattern_parts)}$")
    except re.error:
        return _compile_literal_or_glob_segment_regex(segment)


def _compile_path_segment_matcher(segment: str) -> re.Pattern[str] | None:
    if segment == "**":
        return None

    template_variable_match = _TEMPLATE_VARIABLE_SEGMENT_RE.fullmatch(segment)
    if template_variable_match is None:
        if "{" in segment:
            return _compile_embedded_variable_segment_regex(segment)
        return _compile_literal_or_glob_segment_regex(segment)

    is_catch_all = bool(template_variable_match.group(1))
    regex_constraint = template_variable_match.group(3)
    if is_catch_all:
        # Spring-style {*rest} has the same semantics as **.
        if regex_constraint:
            return _compile_literal_or_glob_segment_regex(segment)
        return None

    if not regex_constraint:
        return re.compile(r"^[^/]+$")

    try:
        return re.compile(rf"^{_template_variable_constraint_regex(regex_constraint)}$")
    except re.error:
        return _compile_literal_or_glob_segment_regex(segment)


@lru_cache(maxsize=_PATH_TEMPLATE_SEGMENT_CACHE_SIZE)
def _compile_path_template_segment_matchers(
    normalized_template: str,
) -> tuple[re.Pattern[str] | None, ...]:
    if normalized_template == "/":
        return tuple()

    raw_segments = _path_segments(normalized_template)
    segment_matchers: list[re.Pattern[str] | None] = []
    for segment in raw_segments:
        segment_matcher = _compile_path_segment_matcher(segment)
        if (
            segment_matcher is None
            and segment_matchers
            and segment_matchers[-1] is None
        ):
            continue
        segment_matchers.append(segment_matcher)

    return tuple(segment_matchers)


def _template_segment_matchers_match(
    template_segment_matchers: tuple[re.Pattern[str] | None, ...],
    observed_segments: tuple[str, ...],
) -> bool:
    observed_count = len(observed_segments)
    # Bottom-up DP over one reused row: matches_from[i] is True when
    # observed_segments[i:] matches the template suffix processed so far.
    # Matchers are folded in right-to-left; a ``**`` (None) matcher absorbs
    # any number of segments, which collapses to a suffix-OR sweep.
    matches_from = [False] * observed_count + [True]
    for template_segment_matcher in reversed(template_segment_matchers):
        if template_segment_matcher is None:
            tail_reachable = False
            for observed_index in range(observed_count, -1, -1):
                tail_reachable = tail_reachable or matches_from[observed_index]
                matches_from[observed_index] = tail_reachable
        else:
            # Ascending order leaves matches_from[i + 1] holding the previous
            # row's value when slot i is rewritten.
            for observed_index in range(observed_count):
                matches_from[observed_index] = (
                    matches_from[observed_index + 1]
                    and template_segment_matcher.fullmatch(
                        observed_segments[observed_index]
                    )
                    is not None
                )
            matches_from[observed_count] = False
    return matches_from[0]


def _template_has_plain_variable_tail(normalized_template: str) -> bool:
    """True when the template ends in a plain ``{var}`` segment.

    Catch-all (``{*rest}``) and regex-constrained tails are excluded: a
    truncated observed path carries no value to bound or validate against
    them, and constraints exist to discriminate sibling endpoints.
    """
    segments = _path_segments(normalized_template)
    if not segments:
        return False
    variable_match = _TEMPLATE_VARIABLE_SEGMENT_RE.fullmatch(segments[-1])
    if variable_match is None:
        return False
    return not variable_match.group(1) and variable_match.group(3) is None


def _template_matches(
    path_template: str,
    observed_path: str,
    *,
    observed_has_truncated_tail: bool = False,
) -> bool:
    normalized_template = normalize_path(path_template)
    normalized_observed = normalize_path(observed_path)

    template_segment_matchers = _compile_path_template_segment_matchers(
        normalized_template
    )
    observed_segments = tuple(
        segment for segment in normalized_observed.strip("/").split("/") if segment
    )
    if _template_segment_matchers_match(template_segment_matchers, observed_segments):
        return True

    # A concatenation-truncated path is one segment short of its endpoint
    # template; retry with the template's final plain {var} segment dropped so
    # the cut-off value is absorbed.
    if not observed_has_truncated_tail:
        return False
    if not _template_has_plain_variable_tail(normalized_template):
        return False
    return _template_segment_matchers_match(
        template_segment_matchers[:-1], observed_segments
    )


def _path_segments(normalized_path: str) -> tuple[str, ...]:
    segments: list[str] = []
    current: list[str] = []
    brace_depth: int = 0
    for character in normalized_path.strip("/"):
        if character == "{":
            brace_depth += 1
        elif character == "}":
            if brace_depth > 0:
                brace_depth -= 1
        elif character == "/" and brace_depth == 0:
            if current:
                segments.append("".join(current))
                current = []
            continue
        current.append(character)
    if current:
        segments.append("".join(current))
    return tuple(segments)


@lru_cache(maxsize=_PATH_TEMPLATE_SEGMENT_CACHE_SIZE)
def _normalized_template_segments(path_template: str) -> tuple[str, ...]:
    return _path_segments(normalize_path(path_template))


def _strip_application_path_prefix(
    observed_segments: tuple[str, ...],
    prefix: str,
) -> str | None:
    """Strip a normalized @ApplicationPath prefix off observed path segments.

    The observed segments must start with the FULL prefix segment tuple (a
    segment-wise prefix, never a raw ``str.startswith`` — ``/api`` must not strip
    ``/apiserver/x``). Returns the remainder path (``"/"`` when nothing is left)
    or ``None`` when the prefix does not lead the observed segments.
    """
    prefix_segments = _path_segments(prefix)
    if not prefix_segments:
        return None
    if observed_segments[: len(prefix_segments)] != prefix_segments:
        return None
    remainder_segments = observed_segments[len(prefix_segments) :]
    return "/" + "/".join(remainder_segments) if remainder_segments else "/"


def _endpoint_matches_directly(
    endpoint: ApplicationEndpoint,
    observed_candidate: EndpointCandidate,
    observed_path: str,
) -> bool:
    return _http_methods_match(
        endpoint,
        observed_candidate.http_method,
    ) and _template_matches(
        endpoint.path_template,
        observed_path,
        observed_has_truncated_tail=observed_candidate.path_truncated,
    )


def _is_coverage_eligible_endpoint(endpoint: ApplicationEndpoint) -> bool:
    return not (
        endpoint.http_method.upper() == "UNKNOWN" and not endpoint.is_method_wildcard
    )


def _coverage_ratio(covered_endpoint_count: int, total_endpoint_count: int) -> float:
    if total_endpoint_count <= 0:
        return 0.0
    return covered_endpoint_count / total_endpoint_count


_MIN_SUFFIX_FALLBACK_OBSERVED_SEGMENTS: int = 2


def _segment_matcher_is_literal(
    segment: str,
    segment_matcher: re.Pattern[str] | None,
) -> bool:
    """True when the segment compiled to the exact-escaped-literal matcher form."""
    # Deriving literalness from the compiled matcher keeps it in lockstep with
    # _compile_path_segment_matcher: a malformed regex constraint like
    # {id:bad(} degrades to literal equality and therefore counts as an anchor.
    return (
        segment_matcher is not None
        and segment_matcher.pattern == f"^{re.escape(segment)}$"
    )


@lru_cache(maxsize=_PATH_TEMPLATE_SEGMENT_CACHE_SIZE)
def _anchored_suffix_matcher_tuples(
    path_template: str,
) -> tuple[tuple[re.Pattern[str] | None, ...], ...]:
    """Matcher tuples for every proper template suffix starting with a literal.

    Every dropped segment must be literal: the hidden-mount hypothesis (context
    root, test-side base URL) is only anchored to evidence when the unobserved
    prefix is a concrete string, while a dropped ``{var}`` would match any base.
    The kept suffix must also BEGIN with a literal segment: a variable-led
    suffix like ``{id}/status`` re-anchors on nothing concrete, so any observed
    path with a matching generic tail (``/orders/status``) would credit it.
    """
    template_segments = _normalized_template_segments(path_template)
    segment_is_literal = [
        _segment_matcher_is_literal(segment, _compile_path_segment_matcher(segment))
        for segment in template_segments
    ]
    suffix_matcher_tuples: list[tuple[re.Pattern[str] | None, ...]] = []
    for dropped in range(1, len(template_segments)):
        # Larger drops include this segment in the prefix, so neither condition
        # can recover once it fails.
        if not segment_is_literal[dropped - 1]:
            break
        if not segment_is_literal[dropped]:
            break
        suffix_matcher_tuples.append(
            _compile_path_template_segment_matchers(
                "/" + "/".join(template_segments[dropped:])
            )
        )
    return tuple(suffix_matcher_tuples)


def _template_suffix_matches(
    path_template: str,
    observed_segments: tuple[str, ...],
) -> bool:
    """True when the observed path matches the template with one or more leading
    template segments dropped."""
    return any(
        _template_segment_matchers_match(suffix_matchers, observed_segments)
        for suffix_matchers in _anchored_suffix_matcher_tuples(path_template)
    )


def _unique_suffix_fallback_indices(
    observed_candidate: EndpointCandidate,
    observed_segments: tuple[str, ...],
    coverage_endpoints: list[ApplicationEndpoint],
) -> set[int]:
    """Match an observed path missing leading template literals (an unrecovered
    test-side base URL duplicating an annotation-derived prefix) as a template
    suffix.

    Guards: never for truncated-tail candidates (combining both relaxations
    fabricates matches), at least two observed segments (one-segment paths
    like ``/search`` are too generic), a literal first segment in the matched
    suffix, and a UNIQUE route (http method, normalized template) across the
    universe —
    two distinct matching routes are ambiguous and return nothing, while
    duplicate extractions of the same route (interface plus implementation,
    consumes/produces variants) are all credited, mirroring direct matching.
    """
    if observed_candidate.path_truncated:
        return set()
    if len(observed_segments) < _MIN_SUFFIX_FALLBACK_OBSERVED_SEGMENTS:
        return set()
    matched_route_key: tuple[str, tuple[str, ...]] | None = None
    matched_indices: set[int] = set()
    for index, endpoint in enumerate(coverage_endpoints):
        if not _http_methods_match(endpoint, observed_candidate.http_method):
            continue
        if not _template_suffix_matches(endpoint.path_template, observed_segments):
            continue
        route_key = (
            (endpoint.http_method or "UNKNOWN").upper(),
            _normalized_template_segments(endpoint.path_template),
        )
        if matched_route_key is None:
            matched_route_key = route_key
        elif route_key != matched_route_key:
            return set()
        matched_indices.add(index)
    return matched_indices


def _matched_endpoint_indices_for_candidate(
    observed_candidate: EndpointCandidate,
    coverage_endpoints: list[ApplicationEndpoint],
    application_path_prefixes: tuple[str, ...],
) -> set[int]:
    """Return the indices of coverage endpoints an observed candidate exercises.

    Direct matches (against the un-stripped observed path) win unconditionally.
    Only when a candidate matches ZERO endpoints directly is each discovered
    @ApplicationPath prefix stripped off and the remainder retried, preventing a
    cross-application false positive where one app's path equals another's mount.
    When both passes match nothing, a guarded unique-suffix fallback absorbs
    template-leading literals the observed path failed to recover (e.g. an
    unrecovered test-side base URL duplicating a class-level mapping prefix).
    """
    observed_path, request_target_context = normalize_observed_path_with_context(
        observed_candidate.path
    )
    if observed_path is None or request_target_context == _REQUEST_TARGET_EXTERNAL:
        return set()

    direct_indices = {
        index
        for index, endpoint in enumerate(coverage_endpoints)
        if _endpoint_matches_directly(endpoint, observed_candidate, observed_path)
    }
    if direct_indices:
        return direct_indices

    observed_segments = _path_segments(observed_path)
    stripped_indices: set[int] = set()
    for prefix in application_path_prefixes:
        stripped_path = _strip_application_path_prefix(observed_segments, prefix)
        if stripped_path is None:
            continue
        for index, endpoint in enumerate(coverage_endpoints):
            if _template_matches(
                endpoint.path_template,
                stripped_path,
                observed_has_truncated_tail=observed_candidate.path_truncated,
            ) and _http_methods_match(endpoint, observed_candidate.http_method):
                stripped_indices.add(index)
    if stripped_indices:
        return stripped_indices

    return _unique_suffix_fallback_indices(
        observed_candidate, observed_segments, coverage_endpoints
    )


def build_endpoint_candidate_matcher(
    application_endpoints: list[ApplicationEndpoint],
    application_path_prefixes: tuple[str, ...] = (),
) -> tuple[list[ApplicationEndpoint], Callable[[EndpointCandidate], set[int]]]:
    """Return the coverage-eligible endpoints and a memoized candidate matcher.

    The matcher maps an observed request candidate to the indices (into the
    returned endpoint list) of the production endpoints it exercises, applying
    the same direct/prefix-strip/suffix-fallback rules as coverage. Callers that
    need each request's production endpoint (e.g. resource-sequence regrouping)
    reuse this instead of re-implementing template matching.
    """
    coverage_endpoints = [
        endpoint
        for endpoint in application_endpoints
        if _is_coverage_eligible_endpoint(endpoint)
    ]
    matched_indices_cache: dict[tuple[str, str, bool], set[int]] = {}

    def matched_indices_for(candidate: EndpointCandidate) -> set[int]:
        key = (candidate.http_method, candidate.path, candidate.path_truncated)
        cached = matched_indices_cache.get(key)
        if cached is None:
            cached = _matched_endpoint_indices_for_candidate(
                candidate, coverage_endpoints, application_path_prefixes
            )
            matched_indices_cache[key] = cached
        return cached

    return coverage_endpoints, matched_indices_for


@dataclass
class _OutcomeAccumulator:
    """Per-endpoint tallies of requests and status assertions attributed to it."""

    attributed_request_count: int = 0
    status_asserted_request_count: int = 0
    asserting_test_methods: set[tuple[str, str]] = field(default_factory=set)
    status_range_counts: Counter[str] = field(default_factory=Counter)
    status_code_counts: Counter[str] = field(default_factory=Counter)

    def to_outcomes(self) -> EndpointAssertedStatusOutcomes:
        return EndpointAssertedStatusOutcomes(
            attributed_request_count=self.attributed_request_count,
            status_asserted_request_count=self.status_asserted_request_count,
            asserting_test_method_count=len(self.asserting_test_methods),
            status_range_counts=dict(self.status_range_counts),
            status_code_counts=dict(self.status_code_counts),
        )


def _accumulate_sequence_outcomes(
    test_method_analysis: TestMethodAnalysis,
    test_method_reference: tuple[str, str],
    matched_indices_for: Callable[[EndpointCandidate], set[int]],
    outcome_accumulators: dict[int, _OutcomeAccumulator],
) -> None:
    """Attribute each sequence's status checks to its single request's endpoints.

    Sequence segmentation already binds every response check to the nearest
    preceding request, so deferred batch assertions (capture several responses,
    assert afterwards) attach to the last request rather than their own.
    """
    for sequence in test_method_analysis.http.test_sequences:
        request_step = next(
            (
                step
                for step in sequence.steps
                if step.kind == SequenceStepKind.HTTP_REQUEST
            ),
            None,
        )
        if request_step is None or not request_step.http_path:
            continue
        observed_candidate = EndpointCandidate(
            http_method=(request_step.http_method or "UNKNOWN").upper(),
            path=request_step.http_path,
            source="sequence-step",
            start_line=request_step.source_span.start_line,
            path_truncated=request_step.path_truncated,
        )
        matched_indices = matched_indices_for(observed_candidate)
        if not matched_indices:
            continue
        status_checks = [
            step
            for step in sequence.steps
            if step.kind == SequenceStepKind.RESPONSE_CHECK
            and step.assertion_role == AssertionRole.STATUS
        ]
        for index in matched_indices:
            accumulator = outcome_accumulators[index]
            accumulator.attributed_request_count += 1
            if not status_checks:
                continue
            accumulator.status_asserted_request_count += 1
            accumulator.asserting_test_methods.add(test_method_reference)
            for check in status_checks:
                if check.status_range is not None:
                    accumulator.status_range_counts[check.status_range] += 1
                else:
                    # An unresolved expected status (e.g. a variable code) is
                    # still a status assertion; bucket it as "unknown",
                    # mirroring StatusCodeDistribution.
                    accumulator.status_range_counts["unknown"] += 1
                if check.status_code is not None:
                    accumulator.status_code_counts[str(check.status_code)] += 1


def build_endpoint_coverage_summary(
    application_endpoints: list[ApplicationEndpoint],
    test_class_analyses: list[TestClassAnalysis],
    application_path_prefixes: tuple[str, ...] = (),
) -> EndpointCoverageSummary:
    # Coverage is based exclusively on EVENT interactions. Builder properties
    # (HTTP method, path) are already merged into their correlated events by
    # the upstream builder-chain correlation pass, so events carry the
    # enriched information and builders need not be counted separately.
    # Matching is a pure function of (method, path, truncated) against the
    # fixed endpoint universe; the sequence-outcome and coverage passes derive
    # their candidates from the same classified call sites, so one memoized
    # matcher keeps them consistent and matches each distinct request once.
    coverage_endpoints, matched_indices_for = build_endpoint_candidate_matcher(
        application_endpoints, application_path_prefixes
    )

    matched_test_methods_by_endpoint_index: dict[int, set[tuple[str, str]]] = {
        index: set() for index in range(len(coverage_endpoints))
    }
    outcome_accumulators: dict[int, _OutcomeAccumulator] = {
        index: _OutcomeAccumulator() for index in range(len(coverage_endpoints))
    }

    for test_class_analysis in test_class_analyses:
        for test_method_analysis in test_class_analysis.test_method_analyses:
            test_method_reference = (
                test_method_analysis.identity.defining_class_name,
                test_method_analysis.identity.method_signature,
            )

            _accumulate_sequence_outcomes(
                test_method_analysis,
                test_method_reference,
                matched_indices_for,
                outcome_accumulators,
            )

            observed_candidates: list[EndpointCandidate] = []

            for interaction in test_method_analysis.http.request_interactions:
                endpoint_candidate = interaction.endpoint_candidate
                if (
                    endpoint_candidate is None
                    or not endpoint_candidate.path
                    or interaction.http_call is None
                ):
                    continue

                if interaction.http_call.request_role == HttpRequestRole.EVENT:
                    observed_candidates.append(endpoint_candidate)

            for observed_candidate in observed_candidates:
                for index in matched_indices_for(observed_candidate):
                    matched_test_methods_by_endpoint_index[index].add(
                        test_method_reference
                    )

    endpoint_entries: list[EndpointCoverageEntry] = []
    covered_endpoint_count: int = 0

    for index, endpoint in enumerate(coverage_endpoints):
        test_method_refs = sorted(
            matched_test_methods_by_endpoint_index[index],
            key=lambda reference: (reference[0], reference[1]),
        )
        covering_test_methods = [
            TestMethodReference(
                qualified_class_name=qualified_class_name,
                method_signature=method_signature,
            )
            for qualified_class_name, method_signature in test_method_refs
        ]
        covering_test_method_count = len(covering_test_methods)
        is_covered = covering_test_method_count > 0

        if is_covered:
            covered_endpoint_count += 1

        endpoint_entries.append(
            EndpointCoverageEntry(
                endpoint=endpoint,
                covering_test_methods=covering_test_methods,
                covering_test_method_count=covering_test_method_count,
                is_covered=is_covered,
                asserted_outcomes=outcome_accumulators[index].to_outcomes(),
            )
        )

    total_application_endpoints = len(coverage_endpoints)
    untested_endpoint_count = total_application_endpoints - covered_endpoint_count

    return EndpointCoverageSummary(
        total_application_endpoints=total_application_endpoints,
        covered_endpoint_count=covered_endpoint_count,
        untested_endpoint_count=untested_endpoint_count,
        coverage_ratio=_coverage_ratio(
            covered_endpoint_count,
            total_application_endpoints,
        ),
        endpoints=endpoint_entries,
        discovered_application_paths=sorted(application_path_prefixes),
    )
