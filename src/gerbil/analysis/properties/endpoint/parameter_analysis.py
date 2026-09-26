from __future__ import annotations

from typing import Final

from gerbil.analysis.properties.endpoint.coverage import (
    _is_coverage_eligible_endpoint,
    _matched_endpoint_indices_for_candidate,
)
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    EndpointCandidate,
    EndpointParameter,
    EndpointParameterCoverageEntry,
    EndpointParameterCoverageSummary,
    EndpointParameterSource,
    HttpCallSite,
    HttpRequestRole,
    ObservedOptionalParameterSet,
    ParameterCoverageEntry,
    ParameterExerciseEvidence,
    TestClassAnalysis,
    TestMethodReference,
)
from gerbil.analysis.shared.url_utils import extract_query_param_names

_OBSERVED_OPTIONAL_PARAMETER_SET_LIMIT: Final[int] = 256

# Sources where we resolve individual named parameters and optionality is
# meaningful; path is always required and body/unknown are not name-enumerated,
# so per-source optional metrics are restricted to these.
_OPTIONAL_ANALYSIS_SOURCES: Final[tuple[EndpointParameterSource, ...]] = (
    EndpointParameterSource.QUERY,
    EndpointParameterSource.HEADER,
    EndpointParameterSource.FORM,
)


def _normalized_names(names: list[str]) -> set[str]:
    return {name.lower() for name in names if name}


def _parameter_key(parameter: EndpointParameter) -> str:
    return f"{parameter.source.value}:{parameter.name.lower()}"


def _exercise_rate(exercised_count: int, total_count: int) -> float | None:
    # None means "no denominator" (nothing of this kind to exercise), kept
    # distinct from a genuine 0.0 (a real denominator, none exercised) so
    # downstream distribution analysis does not conflate the two.
    if total_count <= 0:
        return None
    return exercised_count / total_count


def _exercise_rate_by_source(
    parameter_entries: list[ParameterCoverageEntry],
) -> dict[EndpointParameterSource, float]:
    rates: dict[EndpointParameterSource, float] = {}
    for source in EndpointParameterSource:
        source_entries = [
            entry for entry in parameter_entries if entry.parameter.source == source
        ]
        if not source_entries:
            continue
        # A keyed source always has at least one parameter, so the rate over all
        # of its parameters is never N/A: a present source's 0.0 is always genuine.
        exercised_count = sum(1 for entry in source_entries if entry.is_exercised)
        rates[source] = exercised_count / len(source_entries)
    return rates


def _observed_optional_parameter_sets(
    optional_set_tests: dict[tuple[str, ...], set[tuple[str, str]]],
) -> tuple[list[ObservedOptionalParameterSet], int, bool]:
    sorted_sets = sorted(
        optional_set_tests.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    distinct_count = len(sorted_sets)
    truncated = distinct_count > _OBSERVED_OPTIONAL_PARAMETER_SET_LIMIT
    observed_sets = [
        ObservedOptionalParameterSet(
            parameter_keys=list(parameter_keys),
            test_count=len(test_keys),
        )
        for parameter_keys, test_keys in sorted_sets[
            :_OBSERVED_OPTIONAL_PARAMETER_SET_LIMIT
        ]
    ]
    return observed_sets, distinct_count, truncated


def _project_present_sets(
    present_sets: set[frozenset[str]], source: EndpointParameterSource
) -> set[frozenset[str]]:
    prefix = f"{source.value}:"
    return {
        frozenset(key for key in observed if key.startswith(prefix))
        for observed in present_sets
    }


def _simple_1_way_covered_count(
    optional_keys: set[str], present_sets: set[frozenset[str]]
) -> int:
    """Count optional parameters observed both present and absent across requests."""
    covered = 0
    for key in optional_keys:
        present = False
        absent = False
        for observed in present_sets:
            if key in observed:
                present = True
            else:
                absent = True
            if present and absent:
                break
        if present and absent:
            covered += 1
    return covered


def _two_way_coverage(
    optional_keys: set[str], present_sets: set[frozenset[str]]
) -> tuple[int, int, int]:
    """Return (fully covered pairs, covered configurations, total pairs).

    A pair is fully covered once all four present/absent combinations have been
    observed across requests (NIST simple 2-way); covered configurations counts
    every observed combination across pairs (numerator of NIST total 2-way,
    whose denominator is 4 * total pairs)."""
    keys = sorted(optional_keys)
    pair_count = 0
    covered_pair_count = 0
    covered_config_count = 0
    for first_index in range(len(keys)):
        first = keys[first_index]
        for second in keys[first_index + 1 :]:
            pair_count += 1
            combinations: set[tuple[bool, bool]] = set()
            for observed in present_sets:
                combinations.add((first in observed, second in observed))
                if len(combinations) == 4:
                    break
            covered_config_count += len(combinations)
            if len(combinations) == 4:
                covered_pair_count += 1
    return covered_pair_count, covered_config_count, pair_count


def _determine_exercised_parameters(
    endpoint: ApplicationEndpoint,
    observed_path: str,
    http_call: HttpCallSite,
) -> list[EndpointParameter]:
    exercised: list[EndpointParameter] = []
    query_names_lower = _normalized_names(
        [*extract_query_param_names(observed_path), *http_call.query_param_names]
    )
    header_names = _normalized_names(http_call.header_names)
    form_names = _normalized_names(http_call.form_param_names)
    # Spring's ServletRequest.getParameter* merges query string and
    # application/x-www-form-urlencoded POST bodies, so @RequestParam is
    # satisfied by form parameters as well.
    spring_query_names_lower = (
        query_names_lower | form_names
        if endpoint.framework == "spring"
        else query_names_lower
    )

    for param in endpoint.parameters:
        if param.is_unscorable:
            continue
        if param.source == EndpointParameterSource.PATH:
            exercised.append(param)
        elif param.source == EndpointParameterSource.QUERY:
            # An open query surface (e.g. @RequestParam MultiValueMap) accepts
            # arbitrary keys, so any observed query parameter exercises it.
            if param.is_aggregate and spring_query_names_lower:
                exercised.append(param)
            elif param.name.lower() in spring_query_names_lower:
                exercised.append(param)
        elif param.source == EndpointParameterSource.HEADER:
            if param.name.lower() in header_names:
                exercised.append(param)
        elif param.source == EndpointParameterSource.BODY:
            if http_call.has_body_payload:
                exercised.append(param)
        elif param.source == EndpointParameterSource.FORM:
            if param.name.lower() in form_names:
                exercised.append(param)

    return exercised


def _endpoint_exercise_status(entry: EndpointParameterCoverageEntry) -> str:
    """Classify an endpoint as fully/partial/unexercised/unscorable.

    Eligible endpoints always have at least one parameter, so a zero scorable
    count means every parameter is unscorable: there is no measurable surface,
    so the endpoint is bucketed separately rather than counted as exercised.
    """
    if entry.total_parameter_count == 0:
        return "unscorable"
    if entry.exercised_parameter_count == entry.total_parameter_count:
        return "fully"
    if entry.exercised_parameter_count == 0:
        return "unexercised"
    return "partial"


def build_endpoint_parameter_coverage_summary(
    application_endpoints: list[ApplicationEndpoint],
    test_class_analyses: list[TestClassAnalysis],
    application_path_prefixes: tuple[str, ...] = (),
) -> EndpointParameterCoverageSummary:
    coverage_endpoints = [
        endpoint
        for endpoint in application_endpoints
        if _is_coverage_eligible_endpoint(endpoint)
    ]
    parameterized_indices = [
        index
        for index, endpoint in enumerate(coverage_endpoints)
        if endpoint.parameters
    ]

    if not parameterized_indices:
        return EndpointParameterCoverageSummary()

    # Candidate-to-endpoint links use route coverage's matcher over the same
    # endpoint universe, so the two summaries agree on @ApplicationPath
    # stripping and its direct-match guard.
    matched_interactions_by_endpoint_index: dict[
        int, list[tuple[tuple[str, str], EndpointCandidate, HttpCallSite]]
    ] = {index: [] for index in parameterized_indices}
    for test_class_analysis in test_class_analyses:
        for test_method_analysis in test_class_analysis.test_method_analyses:
            test_key = (
                test_method_analysis.identity.defining_class_name,
                test_method_analysis.identity.method_signature,
            )
            for interaction in test_method_analysis.http.request_interactions:
                endpoint_candidate = interaction.endpoint_candidate
                if (
                    endpoint_candidate is None
                    or not endpoint_candidate.path
                    or interaction.http_call is None
                ):
                    continue

                if interaction.http_call.request_role != HttpRequestRole.EVENT:
                    continue

                for index in _matched_endpoint_indices_for_candidate(
                    endpoint_candidate,
                    coverage_endpoints,
                    application_path_prefixes,
                ):
                    matched = matched_interactions_by_endpoint_index.get(index)
                    if matched is not None:
                        matched.append(
                            (test_key, endpoint_candidate, interaction.http_call)
                        )

    endpoint_entries: list[EndpointParameterCoverageEntry] = []

    for endpoint_index in parameterized_indices:
        endpoint = coverage_endpoints[endpoint_index]
        # Unscorable structured bindings (@ModelAttribute, unresolved @BeanParam)
        # are inventory-only: excluded from every coverage denominator below.
        scorable_parameters = [
            parameter
            for parameter in endpoint.parameters
            if not parameter.is_unscorable
        ]
        evidence_by_test: dict[tuple[str, str], list[EndpointParameter]] = {}
        route_covering_tests: set[tuple[str, str]] = set()
        optional_parameter_keys = {
            _parameter_key(parameter)
            for parameter in scorable_parameters
            if not parameter.required
        }
        optional_set_tests: dict[tuple[str, ...], set[tuple[str, str]]] = {}

        for (
            test_key,
            endpoint_candidate,
            http_call,
        ) in matched_interactions_by_endpoint_index[endpoint_index]:
            route_covering_tests.add(test_key)

            exercised = _determine_exercised_parameters(
                endpoint=endpoint,
                observed_path=endpoint_candidate.path,
                http_call=http_call,
            )

            if optional_parameter_keys:
                optional_keys = tuple(
                    sorted(
                        _parameter_key(param)
                        for param in exercised
                        if _parameter_key(param) in optional_parameter_keys
                    )
                )
                optional_set_tests.setdefault(optional_keys, set()).add(test_key)

            if not exercised:
                continue

            evidence_by_test.setdefault(test_key, []).extend(exercised)

        parameter_evidence: list[ParameterExerciseEvidence] = []
        for (class_name, method_sig), params in sorted(evidence_by_test.items()):
            seen_keys: set[str] = set()
            deduped: list[EndpointParameter] = []
            for p in params:
                parameter_key = _parameter_key(p)
                if parameter_key not in seen_keys:
                    seen_keys.add(parameter_key)
                    deduped.append(p)
            parameter_evidence.append(
                ParameterExerciseEvidence(
                    test_method=TestMethodReference(
                        qualified_class_name=class_name,
                        method_signature=method_sig,
                    ),
                    exercised_parameters=deduped,
                )
            )

        exercised_param_keys: set[str] = set()
        exercising_counts: dict[str, int] = {}
        for evidence in parameter_evidence:
            for param in evidence.exercised_parameters:
                parameter_key = _parameter_key(param)
                exercised_param_keys.add(parameter_key)
                exercising_counts[parameter_key] = (
                    exercising_counts.get(parameter_key, 0) + 1
                )

        parameter_entries: list[ParameterCoverageEntry] = []
        for param in scorable_parameters:
            parameter_key = _parameter_key(param)
            is_exercised = parameter_key in exercised_param_keys
            parameter_entries.append(
                ParameterCoverageEntry(
                    parameter=param,
                    is_exercised=is_exercised,
                    exercising_test_count=exercising_counts.get(parameter_key, 0),
                )
            )

        exercised_count = sum(1 for pe in parameter_entries if pe.is_exercised)
        total_count = len(scorable_parameters)
        required_entries = [
            entry for entry in parameter_entries if entry.parameter.required
        ]
        optional_entries = [
            entry for entry in parameter_entries if not entry.parameter.required
        ]
        required_exercised_count = sum(
            1 for entry in required_entries if entry.is_exercised
        )
        optional_exercised_count = sum(
            1 for entry in optional_entries if entry.is_exercised
        )
        (
            observed_optional_parameter_sets,
            distinct_observed_optional_parameter_set_count,
            observed_optional_parameter_sets_truncated,
        ) = _observed_optional_parameter_sets(optional_set_tests)

        present_sets = {frozenset(keys) for keys in optional_set_tests}

        required_count_by_source: dict[EndpointParameterSource, int] = {}
        required_exercised_by_source: dict[EndpointParameterSource, int] = {}
        required_rate_by_source: dict[EndpointParameterSource, float | None] = {}
        optional_count_by_source: dict[EndpointParameterSource, int] = {}
        optional_exercised_by_source: dict[EndpointParameterSource, int] = {}
        optional_rate_by_source: dict[EndpointParameterSource, float | None] = {}
        distinct_set_count_by_source: dict[EndpointParameterSource, int] = {}
        simple_1_way_covered_by_source: dict[EndpointParameterSource, int] = {}
        simple_1_way_coverage_by_source: dict[EndpointParameterSource, float | None] = (
            {}
        )
        pair_count_by_source: dict[EndpointParameterSource, int] = {}
        simple_2_way_covered_by_source: dict[EndpointParameterSource, int] = {}
        simple_2_way_coverage_by_source: dict[EndpointParameterSource, float | None] = (
            {}
        )
        total_2_way_covered_by_source: dict[EndpointParameterSource, int] = {}
        total_2_way_coverage_by_source: dict[EndpointParameterSource, float | None] = {}

        # Every source present on the endpoint (>=1 scorable parameter) is keyed in
        # all per-source dicts; counts are genuine (0 allowed) and a rate is None
        # only when its own denominator is 0, so count 0 <-> rate None stay aligned.
        for source in _OPTIONAL_ANALYSIS_SOURCES:
            source_entries = [
                entry for entry in parameter_entries if entry.parameter.source == source
            ]
            if not source_entries:
                continue
            source_required = [e for e in source_entries if e.parameter.required]
            source_optional = [e for e in source_entries if not e.parameter.required]

            source_required_exercised = sum(
                1 for e in source_required if e.is_exercised
            )
            required_count_by_source[source] = len(source_required)
            required_exercised_by_source[source] = source_required_exercised
            required_rate_by_source[source] = _exercise_rate(
                source_required_exercised, len(source_required)
            )

            source_optional_exercised = sum(
                1 for e in source_optional if e.is_exercised
            )
            optional_count_by_source[source] = len(source_optional)
            optional_exercised_by_source[source] = source_optional_exercised
            optional_rate_by_source[source] = _exercise_rate(
                source_optional_exercised, len(source_optional)
            )

            source_keys = {_parameter_key(e.parameter) for e in source_optional}
            source_present_sets = _project_present_sets(present_sets, source)
            # A source with no optional parameters has no optional dimension: its
            # projected sets are all the empty set, so report 0 distinct rather
            # than a degenerate 1 for the empty projection.
            distinct_set_count_by_source[source] = (
                len(source_present_sets) if source_keys else 0
            )

            source_simple_1_way = _simple_1_way_covered_count(
                source_keys, source_present_sets
            )
            simple_1_way_covered_by_source[source] = source_simple_1_way
            simple_1_way_coverage_by_source[source] = _exercise_rate(
                source_simple_1_way, len(source_keys)
            )

            (
                source_simple_2_way_covered,
                source_total_2_way_covered,
                source_pair_count,
            ) = _two_way_coverage(source_keys, source_present_sets)
            pair_count_by_source[source] = source_pair_count
            simple_2_way_covered_by_source[source] = source_simple_2_way_covered
            simple_2_way_coverage_by_source[source] = _exercise_rate(
                source_simple_2_way_covered, source_pair_count
            )
            total_2_way_covered_by_source[source] = source_total_2_way_covered
            total_2_way_coverage_by_source[source] = _exercise_rate(
                source_total_2_way_covered, 4 * source_pair_count
            )

        simple_1_way_covered = _simple_1_way_covered_count(
            optional_parameter_keys, present_sets
        )
        simple_2_way_covered, total_2_way_covered, pair_count = _two_way_coverage(
            optional_parameter_keys, present_sets
        )

        # Per endpoint
        endpoint_entries.append(
            EndpointParameterCoverageEntry(
                endpoint=endpoint,
                parameter_evidence=parameter_evidence,
                parameter_entries=parameter_entries,
                exercised_parameter_count=exercised_count,
                total_parameter_count=total_count,
                exercise_rate=_exercise_rate(exercised_count, total_count),
                exercise_rate_by_source=_exercise_rate_by_source(parameter_entries),
                required_parameter_count=len(required_entries),
                required_exercised_count=required_exercised_count,
                required_exercise_rate=_exercise_rate(
                    required_exercised_count,
                    len(required_entries),
                ),
                optional_parameter_count=len(optional_entries),
                optional_exercised_count=optional_exercised_count,
                optional_exercise_rate=_exercise_rate(
                    optional_exercised_count,
                    len(optional_entries),
                ),
                required_parameter_count_by_source=required_count_by_source,
                required_exercised_count_by_source=required_exercised_by_source,
                required_exercise_rate_by_source=required_rate_by_source,
                optional_parameter_count_by_source=optional_count_by_source,
                optional_exercised_count_by_source=optional_exercised_by_source,
                optional_exercise_rate_by_source=optional_rate_by_source,
                route_covering_test_count=len(route_covering_tests),
                observed_optional_parameter_set_limit=(
                    _OBSERVED_OPTIONAL_PARAMETER_SET_LIMIT
                ),
                observed_optional_parameter_sets_truncated=(
                    observed_optional_parameter_sets_truncated
                ),
                distinct_observed_optional_parameter_set_count=(
                    distinct_observed_optional_parameter_set_count
                ),
                distinct_observed_optional_set_count_by_source=(
                    distinct_set_count_by_source
                ),
                observed_optional_parameter_sets=observed_optional_parameter_sets,
                simple_1_way_optional_covered_count=simple_1_way_covered,
                simple_1_way_optional_coverage=_exercise_rate(
                    simple_1_way_covered, len(optional_parameter_keys)
                ),
                simple_1_way_optional_covered_count_by_source=(
                    simple_1_way_covered_by_source
                ),
                simple_1_way_optional_coverage_by_source=(
                    simple_1_way_coverage_by_source
                ),
                optional_pair_count=pair_count,
                simple_2_way_optional_covered_count=simple_2_way_covered,
                simple_2_way_optional_coverage=_exercise_rate(
                    simple_2_way_covered, pair_count
                ),
                total_2_way_optional_covered_config_count=total_2_way_covered,
                total_2_way_optional_coverage=_exercise_rate(
                    total_2_way_covered, 4 * pair_count
                ),
                optional_pair_count_by_source=pair_count_by_source,
                simple_2_way_optional_covered_count_by_source=(
                    simple_2_way_covered_by_source
                ),
                simple_2_way_optional_coverage_by_source=(
                    simple_2_way_coverage_by_source
                ),
                total_2_way_optional_covered_config_count_by_source=(
                    total_2_way_covered_by_source
                ),
                total_2_way_optional_coverage_by_source=total_2_way_coverage_by_source,
            )
        )

    status_counts = {"fully": 0, "partial": 0, "unexercised": 0, "unscorable": 0}
    for entry in endpoint_entries:
        status_counts[_endpoint_exercise_status(entry)] += 1

    # Full project-wide
    return EndpointParameterCoverageSummary(
        total_endpoints_with_parameters=len(endpoint_entries),
        fully_exercised_endpoint_count=status_counts["fully"],
        partially_exercised_endpoint_count=status_counts["partial"],
        unexercised_endpoint_count=status_counts["unexercised"],
        unscorable_endpoint_count=status_counts["unscorable"],
        endpoints=endpoint_entries,
    )
