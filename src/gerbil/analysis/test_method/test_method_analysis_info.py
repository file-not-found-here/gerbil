from __future__ import annotations

from dataclasses import dataclass

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport

from gerbil.analysis.shared import CommonAnalysis, Reachability
from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.runtime.call_sites import (
    CallSiteNode,
    LoadCallSites,
    MethodRef,
    ResolveHelper,
    build_expanded_call_site_grouping,
    iter_expanded_evaluation_order,
    iter_resolved_helpers,
)
from gerbil.analysis.shared.annotations import (
    annotation_short_name as _annotation_short_name,
)
from gerbil.analysis.shared.constants import (
    SETUP_ANNOTATION_PRIORITY,
    TEARDOWN_ANNOTATION_PRIORITY,
)
from gerbil.analysis.schema import (
    AssertionAnalysis,
    ControllerHandlerTarget,
    DependencyAnalysis,
    ExpandedMetrics,
    FixtureAmbiguityNote,
    FixtureAnalysis,
    HttpAnalysis,
    HttpInteraction,
    HttpInteractionKind,
    HttpRequestRole,
    HttpRequestInteraction,
    HttpVerificationInteraction,
    LifecyclePhase,
    MethodIdentity,
    MethodMetrics,
    ParameterizationSummary,
    SourceSpan,
    StateAnalysis,
    TestMethodAnalysis,
    TestingFramework,
)
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.properties import (
    EndpointHandlerIndex,
    analyze_preconditions,
    analyze_request_dispatch,
    analyze_state_observations,
    db_state_assertion_observations,
    build_api_call_sequence,
    build_endpoint_handler_index,
    detect_controller_unit_test_targets,
    build_assertion_summary,
    build_http_interaction_views,
    build_http_sequence_summary,
    build_http_test_sequences,
    build_http_verification_interaction_for_event,
    build_status_code_counts,
    build_status_code_distribution,
    classify_auth_handling,
    classify_dependency_strategy,
    classify_failure_scenarios,
    classify_oracle_type,
    detect_resource_interaction_sequences,
    extract_parameterization_analysis,
)
from gerbil.analysis.http.classification import (
    build_http_request_interaction_for_event,
    build_output_http_mocked_interactions,
    classify_http_on_runtime_view,
)
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.runtime import (
    FixtureMethod,
    PhaseEntry,
    RuntimeEvent,
    TestRuntimeView,
)

_FIXTURE_RELATION_SUPER: str = "super"
_FIXTURE_RELATION_INTERFACE: str = "interface"
_FIXTURE_RELATION_CLASS: str = "class"
_FIXTURE_RELATION_UNRESOLVED: str = "unresolved"

_SETUP_RELATION_RANK_BY_PROFILE: dict[str, dict[str, int]] = {
    "default": {
        _FIXTURE_RELATION_SUPER: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_CLASS: 2,
    },
    "junit": {
        _FIXTURE_RELATION_SUPER: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_CLASS: 2,
    },
    "testng": {
        _FIXTURE_RELATION_SUPER: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_CLASS: 2,
    },
}

_TEARDOWN_RELATION_RANK_BY_PROFILE: dict[str, dict[str, int]] = {
    "default": {
        _FIXTURE_RELATION_CLASS: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_SUPER: 2,
    },
    "junit": {
        _FIXTURE_RELATION_CLASS: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_SUPER: 2,
    },
    "testng": {
        _FIXTURE_RELATION_CLASS: 0,
        _FIXTURE_RELATION_INTERFACE: 1,
        _FIXTURE_RELATION_SUPER: 2,
    },
}

_JUNIT_FRAMEWORKS: set[TestingFramework] = {
    TestingFramework.JUNIT3,
    TestingFramework.JUNIT4,
    TestingFramework.JUNIT5,
}


@dataclass(frozen=True)
class _FixtureOrderContext:
    class_depth_by_name: dict[str, int]
    relation_by_class_name: dict[str, str]


@dataclass(frozen=True)
class _FixtureSortEntry:
    priority: int
    unresolved_rank: int
    relation_rank: int
    depth_rank: int
    owner_class_name: str
    method_signature: str
    entry: PhaseEntry


class MethodAnalysisInfo:
    def __init__(
        self,
        analysis: JavaAnalysis,
        application_classes: list[str],
        test_utility_classes: list[str] | None = None,
        expanded_helper_depth: int = 1,
        common_analysis: CommonAnalysis | None = None,
        reachability: Reachability | None = None,
        endpoint_handler_index: EndpointHandlerIndex | None = None,
    ) -> None:
        if expanded_helper_depth < 0:
            raise ValueError("expanded_helper_depth must be non-negative")

        self.analysis: JavaAnalysis = analysis
        self.application_classes: list[str] = application_classes
        self.test_utility_classes: list[str] = test_utility_classes or []
        self.expanded_helper_depth: int = expanded_helper_depth
        self.common_analysis: CommonAnalysis = common_analysis or CommonAnalysis(
            analysis
        )
        self.reachability: Reachability = (
            reachability or self.common_analysis.get_reachability()
        )
        self.endpoint_handler_index: EndpointHandlerIndex = (
            endpoint_handler_index
            if endpoint_handler_index is not None
            else build_endpoint_handler_index([])
        )

    def get_test_method_analysis_info(
        self,
        testing_frameworks: list[TestingFramework],
        qualified_class_name: str,
        method_signature: str,
        setup_methods: list[FixtureMethod],
        teardown_methods: list[FixtureMethod] | None = None,
    ) -> TestMethodAnalysis:
        """Build full analysis information for a single test method."""

        method_details = self.analysis.get_method(
            qualified_class_name, method_signature
        )
        if not method_details:
            return TestMethodAnalysis(
                identity=MethodIdentity(
                    defining_class_name=qualified_class_name,
                    method_signature=method_signature,
                    method_declaration="",
                )
            )

        common_analysis = self.common_analysis
        direct_class_imports: list[JImport] = common_analysis.get_class_imports(
            qualified_class_name
        )
        get_static_import_index_for_class = common_analysis.get_static_import_index
        method_annotations: list[str] = list(method_details.annotations or [])
        effective_teardown_methods: list[FixtureMethod] = teardown_methods or []

        reachability = self.reachability
        resolve_helper, load_call_sites_fn = reachability.build_helper_resolver(
            qualified_class_name=qualified_class_name,
            add_extended_class=True,
            test_utility_classes=self.test_utility_classes,
            get_static_import_index_for_class=get_static_import_index_for_class,
        )

        runtime_view = self._build_test_runtime_view(
            qualified_class_name=qualified_class_name,
            method_signature=method_signature,
            method_details=method_details,
            setup_methods=setup_methods,
            teardown_methods=effective_teardown_methods,
            resolve_helper=resolve_helper,
            load_call_sites=load_call_sites_fn,
            testing_frameworks=testing_frameworks,
        )

        ncloc: int = common_analysis.get_ncloc(
            method_details.declaration, method_details.code
        )
        cyclomatic_complexity: int = int(method_details.cyclomatic_complexity or 0)
        local_objects_created: int = common_analysis.count_objects_created(
            method_details
        )

        (
            expanded_ncloc,
            expanded_cyclomatic_complexity,
            expanded_helper_method_count,
            expanded_helper_method_ncloc,
            expanded_objects_created,
        ) = self._build_runtime_metrics(
            common_analysis=common_analysis,
            runtime_view=runtime_view,
        )

        # Distinct helpers reachable from the test body only, so this excludes
        # fixture helpers and never double-counts a helper invoked more than once.
        test_entry = runtime_view.test_entry()
        test_helper_method_count = (
            sum(1 for _ in iter_resolved_helpers(test_entry.grouping))
            if test_entry is not None
            else 0
        )

        receiver_resolver = RuntimeReceiverResolver(
            analysis=self.analysis,
            load_method_details=lambda method_ref: self.analysis.get_method(
                method_ref.defining_class_name,
                method_ref.method_signature,
            ),
            get_static_import_index_for_class=get_static_import_index_for_class,
            get_class_imports_for_class=common_analysis.get_class_imports,
            get_superclass_chain_for_class=common_analysis.get_superclass_chain,
            constant_resolver=common_analysis.get_constant_resolver(),
        )

        # Single mutation pass: annotate nodes with HTTP classification.
        classify_http_on_runtime_view(
            runtime_view=runtime_view,
            receiver_resolver=receiver_resolver,
        )

        # Single mutation pass: annotate nodes with assertion classification.
        classify_assertions_on_runtime_view(
            runtime_view=runtime_view,
            receiver_resolver=receiver_resolver,
        )

        # Count request-event classified nodes for api test detection.
        http_request_event_count = sum(
            1
            for event in runtime_view.iter_events()
            if event.node.http_classification is not None
            and event.node.http_classification.request_role == HttpRequestRole.EVENT
        )

        # Count assertions in root grouping of test entry (not recursing into helpers).
        assertion_count = 0
        for entry in runtime_view.entries:
            if entry.phase == LifecyclePhase.TEST:
                assertion_count = sum(
                    1
                    for node in entry.grouping.nodes
                    if node.assertion_classification is not None
                    and node.assertion_classification.is_countable
                )
                break

        is_api_test = http_request_event_count > 0
        parameterization: ParameterizationSummary | None = (
            extract_parameterization_analysis(
                method_annotations,
                class_imports=direct_class_imports,
            )
        )
        identity = MethodIdentity(
            defining_class_name=qualified_class_name,
            method_signature=method_signature,
            method_declaration=method_details.declaration,
            annotations=method_annotations,
            thrown_exceptions=list(method_details.thrown_exceptions or []),
            parameterization=parameterization,
        )
        local_metrics = MethodMetrics(
            ncloc=ncloc,
            cyclomatic_complexity=cyclomatic_complexity,
            number_of_objects_created=local_objects_created,
            assertion_count=assertion_count,
        )
        expanded_metrics = ExpandedMetrics(
            ncloc=expanded_ncloc,
            cyclomatic_complexity=expanded_cyclomatic_complexity,
            helper_method_count=expanded_helper_method_count,
            helper_method_ncloc=expanded_helper_method_ncloc,
            test_helper_method_count=test_helper_method_count,
            number_of_objects_created=expanded_objects_created,
        )

        # Controller unit tests bypass HTTP, so API tests take precedence: only
        # tests with no request EVENT are candidates for direct handler dispatch.
        controller_unit_test_targets: list[ControllerHandlerTarget] = []
        if not is_api_test:
            controller_unit_test_targets = detect_controller_unit_test_targets(
                runtime_view=runtime_view,
                handler_index=self.endpoint_handler_index,
                receiver_resolver=receiver_resolver,
                analysis=self.analysis,
            )

        if not is_api_test:
            return TestMethodAnalysis(
                identity=identity,
                is_api_test=False,
                is_controller_unit_test=bool(controller_unit_test_targets),
                controller_unit_test_targets=controller_unit_test_targets,
                local_metrics=local_metrics,
                expanded_metrics=expanded_metrics,
            )

        class_details = self.analysis.get_class(qualified_class_name)
        class_annotations: list[ResolvedAnnotation] = (
            common_analysis.resolve_effective_class_annotations(qualified_class_name)
        )
        class_annotation_imports_by_class: dict[str, list[JImport]] = {
            class_name: common_analysis.get_class_imports(class_name)
            for class_name in {
                resolved_annotation.declaring_class_name
                for resolved_annotation in class_annotations
            }
        }

        request_dispatch = analyze_request_dispatch(
            runtime_view=runtime_view,
        )

        dependency_strategy = classify_dependency_strategy(
            class_details=class_details,
            class_annotations=class_annotations,
            method_details=method_details,
            runtime_view=runtime_view,
            analysis=self.analysis,
            class_annotation_imports_by_class=class_annotation_imports_by_class,
            method_imports=direct_class_imports,
            declaring_class_imports=direct_class_imports,
            receiver_resolver=receiver_resolver,
        )

        assertion_summary = build_assertion_summary(
            runtime_view=runtime_view,
        )

        oracle_type = classify_oracle_type(
            runtime_view=runtime_view,
            method_details=method_details,
            class_imports=direct_class_imports,
            receiver_resolver=receiver_resolver,
            method_annotations=(
                method_details.annotations if method_details is not None else None
            ),
        )

        failure_scenarios = classify_failure_scenarios(
            runtime_view=runtime_view,
            method_annotations=(
                method_details.annotations if method_details is not None else None
            ),
            class_imports=direct_class_imports,
        )

        status_code_dist = build_status_code_distribution(
            runtime_view=runtime_view,
        )
        status_code_counts = build_status_code_counts(
            runtime_view=runtime_view,
        )

        state_observation = analyze_state_observations(
            runtime_view=runtime_view,
            analysis=self.analysis,
            receiver_resolver=receiver_resolver,
        )
        state_observation.observations.extend(
            db_state_assertion_observations(
                class_annotations=class_annotations,
                method_annotations=method_annotations,
                class_annotation_imports_by_class=class_annotation_imports_by_class,
                method_imports=direct_class_imports,
            )
        )

        auth_handling = classify_auth_handling(
            class_annotations=class_annotations,
            method_annotations=method_annotations,
            class_annotation_imports_by_class=class_annotation_imports_by_class,
            method_imports=direct_class_imports,
            runtime_view=runtime_view,
            receiver_resolver=receiver_resolver,
        )

        precondition_summary = analyze_preconditions(
            class_annotations=class_annotations,
            method_annotations=method_annotations,
            class_annotation_imports_by_class=class_annotation_imports_by_class,
            method_imports=direct_class_imports,
            runtime_view=runtime_view,
            analysis=self.analysis,
            receiver_resolver=receiver_resolver,
        )
        ambiguous_fixture_group_methods = self._build_ambiguous_fixture_notes(
            runtime_view
        )

        api_call_sequence = build_api_call_sequence(
            runtime_view=runtime_view,
        )
        http_test_sequences = build_http_test_sequences(api_call_sequence)
        http_sequence_summary = build_http_sequence_summary(http_test_sequences)
        resource_interaction_sequences = detect_resource_interaction_sequences(
            runtime_view=runtime_view,
        )

        # Build output-only HTTP interaction objects for serialization.
        (
            http_request_interactions,
            http_verification_interactions,
            http_response_extractions,
            http_interactions,
        ) = build_http_interaction_views(runtime_view=runtime_view)
        http_mocked_interactions = build_output_http_mocked_interactions(
            runtime_view=runtime_view,
        )
        fixtures = self._build_fixture_analyses(runtime_view, common_analysis)

        return TestMethodAnalysis(
            identity=identity,
            is_api_test=is_api_test,
            local_metrics=local_metrics,
            expanded_metrics=expanded_metrics,
            http=HttpAnalysis(
                request_interactions=http_request_interactions,
                mocked_interactions=http_mocked_interactions,
                response_extractions=http_response_extractions,
                verification_interactions=http_verification_interactions,
                http_interactions=http_interactions,
                call_sequence=api_call_sequence,
                test_sequences=http_test_sequences,
                sequence_summary=http_sequence_summary,
                resource_interaction_sequences=resource_interaction_sequences,
                request_dispatch=request_dispatch,
                auth_handling=auth_handling,
            ),
            assertions=AssertionAnalysis(
                summary=assertion_summary,
                oracle_type=oracle_type,
                failure_scenarios=failure_scenarios,
                status_code_distribution=status_code_dist,
                status_code_counts=status_code_counts,
            ),
            dependencies=DependencyAnalysis(
                strategy=dependency_strategy,
            ),
            state=StateAnalysis(
                preconditions=precondition_summary,
                observations=state_observation,
            ),
            fixtures=fixtures,
            ambiguous_fixture_group_methods=ambiguous_fixture_group_methods,
        )

    def _build_test_runtime_view(
        self,
        *,
        qualified_class_name: str,
        method_signature: str,
        method_details: JCallable | None,
        setup_methods: list[FixtureMethod],
        teardown_methods: list[FixtureMethod],
        resolve_helper: ResolveHelper,
        load_call_sites: LoadCallSites,
        testing_frameworks: list[TestingFramework] | None = None,
    ) -> TestRuntimeView:
        def fixture_priority(
            method_annotations: list[str], priority_map: dict[str, int]
        ) -> int:
            priorities: list[int] = [
                priority_map[annotation_prefix]
                for annotation in method_annotations
                if (annotation_prefix := _annotation_short_name(annotation))
                in priority_map
            ]
            return min(priorities) if priorities else len(priority_map)

        def build_fixture_entry(
            *,
            phase: LifecyclePhase,
            fixture: FixtureMethod,
        ) -> PhaseEntry:
            fixture_method = self.analysis.get_method(
                fixture.defining_class_name, fixture.method_signature
            )
            call_sites = list(fixture_method.call_sites) if fixture_method else []
            grouping_owner = MethodRef(
                defining_class_name=fixture.defining_class_name,
                method_signature=fixture.method_signature,
            )
            grouping = build_expanded_call_site_grouping(
                call_sites=call_sites,
                owner=grouping_owner,
                resolve_helper=resolve_helper,
                load_call_sites=load_call_sites,
                max_helper_depth=self.expanded_helper_depth,
            )
            return PhaseEntry(
                phase=phase,
                method_ref=MethodRef(
                    defining_class_name=fixture.defining_class_name,
                    method_signature=fixture.method_signature,
                ),
                context_class_name=qualified_class_name,
                grouping=grouping,
                method_details=fixture_method,
                is_group_ambiguous=fixture.is_ambiguous,
            )

        ordering_profile = self._resolve_fixture_order_profile(testing_frameworks)
        order_context = self._build_fixture_order_context(qualified_class_name)

        def build_sorted_fixture_entries(
            *,
            phase: LifecyclePhase,
            fixture_methods: list[FixtureMethod],
            priority_map: dict[str, int],
        ) -> list[PhaseEntry]:
            sortable_entries: list[_FixtureSortEntry] = []
            for fixture in fixture_methods:
                fixture_class_name = fixture.defining_class_name
                fixture_signature = fixture.method_signature
                relation_name = order_context.relation_by_class_name.get(
                    fixture_class_name, _FIXTURE_RELATION_UNRESOLVED
                )
                unresolved_rank: int = (
                    1 if relation_name == _FIXTURE_RELATION_UNRESOLVED else 0
                )
                relation_rank: int = self._get_fixture_relation_rank(
                    phase=phase,
                    profile=ordering_profile,
                    relation_name=relation_name,
                )
                depth: int = order_context.class_depth_by_name.get(
                    fixture_class_name, -1
                )
                depth_rank: int = self._get_fixture_depth_rank(phase=phase, depth=depth)
                fixture_entry = build_fixture_entry(
                    phase=phase,
                    fixture=fixture,
                )
                fixture_annotations = (
                    list(fixture_entry.method_details.annotations or [])
                    if fixture_entry.method_details
                    else []
                )
                sortable_entries.append(
                    _FixtureSortEntry(
                        priority=fixture_priority(
                            fixture_annotations,
                            priority_map,
                        ),
                        unresolved_rank=unresolved_rank,
                        relation_rank=relation_rank,
                        depth_rank=depth_rank,
                        owner_class_name=fixture_class_name,
                        method_signature=fixture_signature,
                        entry=fixture_entry,
                    )
                )

            sortable_entries.sort(
                key=lambda item: (
                    item.priority,
                    item.unresolved_rank,
                    item.relation_rank,
                    item.depth_rank,
                    item.owner_class_name,
                    item.method_signature,
                )
            )
            return [item.entry for item in sortable_entries]

        setup_entries = build_sorted_fixture_entries(
            phase=LifecyclePhase.SETUP,
            fixture_methods=setup_methods,
            priority_map=SETUP_ANNOTATION_PRIORITY,
        )

        test_grouping = build_expanded_call_site_grouping(
            call_sites=list(method_details.call_sites) if method_details else [],
            owner=MethodRef(
                defining_class_name=qualified_class_name,
                method_signature=method_signature,
            ),
            resolve_helper=resolve_helper,
            load_call_sites=load_call_sites,
            max_helper_depth=self.expanded_helper_depth,
        )
        test_entry = PhaseEntry(
            phase=LifecyclePhase.TEST,
            method_ref=MethodRef(
                defining_class_name=qualified_class_name,
                method_signature=method_signature,
            ),
            context_class_name=qualified_class_name,
            grouping=test_grouping,
            method_details=method_details,
            is_group_ambiguous=False,
        )

        teardown_entries = build_sorted_fixture_entries(
            phase=LifecyclePhase.TEARDOWN,
            fixture_methods=teardown_methods,
            priority_map=TEARDOWN_ANNOTATION_PRIORITY,
        )

        return TestRuntimeView(
            entries=[
                *setup_entries,
                test_entry,
                *teardown_entries,
            ]
        )

    def _resolve_fixture_order_profile(
        self, testing_frameworks: list[TestingFramework] | None
    ) -> str:
        frameworks = set(testing_frameworks or [])
        if TestingFramework.TESTNG in frameworks:
            return "testng"
        if frameworks.intersection(_JUNIT_FRAMEWORKS):
            return "junit"
        return "default"

    def _build_fixture_order_context(
        self, qualified_class_name: str
    ) -> _FixtureOrderContext:
        class_resolution_order = self.reachability.get_class_resolution_order(
            qualified_class_name
        )
        class_depth_by_name: dict[str, int] = {
            class_name: index for index, class_name in enumerate(class_resolution_order)
        }

        super_resolution_order = self.reachability.get_class_resolution_order(
            qualified_class_name,
            include_interfaces=False,
        )
        superclass_names: set[str] = (
            set(super_resolution_order[1:]) if super_resolution_order else set()
        )
        interface_names: set[str] = set(class_resolution_order) - set(
            super_resolution_order
        )

        relation_by_class_name: dict[str, str] = {
            class_name: _FIXTURE_RELATION_SUPER for class_name in superclass_names
        }
        relation_by_class_name.update(
            {
                interface_name: _FIXTURE_RELATION_INTERFACE
                for interface_name in interface_names
            }
        )
        relation_by_class_name[qualified_class_name] = _FIXTURE_RELATION_CLASS

        return _FixtureOrderContext(
            class_depth_by_name=class_depth_by_name,
            relation_by_class_name=relation_by_class_name,
        )

    def _get_fixture_relation_rank(
        self, *, phase: LifecyclePhase, profile: str, relation_name: str
    ) -> int:
        rank_map_by_profile = (
            _SETUP_RELATION_RANK_BY_PROFILE
            if phase == LifecyclePhase.SETUP
            else _TEARDOWN_RELATION_RANK_BY_PROFILE
        )
        rank_by_relation = rank_map_by_profile.get(
            profile, rank_map_by_profile["default"]
        )
        return rank_by_relation.get(relation_name, len(rank_by_relation))

    def _get_fixture_depth_rank(self, *, phase: LifecyclePhase, depth: int) -> int:
        return -depth if phase == LifecyclePhase.SETUP else depth

    def _build_runtime_metrics(
        self,
        common_analysis: CommonAnalysis,
        runtime_view: TestRuntimeView,
    ) -> tuple[int, int, int, int, int]:
        expanded_ncloc: int = 0
        expanded_cyclomatic_complexity: int = 0
        expanded_helper_method_count: int = 0
        expanded_helper_method_ncloc: int = 0
        number_of_objects_created: int = 0

        for entry in runtime_view.entries:
            if entry.method_details is None:
                continue

            method_ncloc = common_analysis.get_ncloc(
                entry.method_details.declaration,
                entry.method_details.code,
            )
            expanded_ncloc += method_ncloc
            expanded_cyclomatic_complexity += int(
                entry.method_details.cyclomatic_complexity or 0
            )
            number_of_objects_created += common_analysis.count_objects_created(
                entry.method_details
            )

        helper_metric_cache: dict[MethodRef, tuple[int, int, int] | None] = {}
        for entry in runtime_view.entries:
            for event in iter_expanded_evaluation_order(
                entry.grouping,
                owner=entry.method_ref,
            ):
                helper_ref = event.node.resolved_helper
                if helper_ref is None:
                    continue

                cached_metrics = helper_metric_cache.get(helper_ref)
                if cached_metrics is None and helper_ref not in helper_metric_cache:
                    helper_details = self.analysis.get_method(
                        helper_ref.defining_class_name,
                        helper_ref.method_signature,
                    )
                    if helper_details is None:
                        helper_metric_cache[helper_ref] = None
                        continue

                    helper_metric_cache[helper_ref] = (
                        common_analysis.get_ncloc(
                            helper_details.declaration,
                            helper_details.code,
                        ),
                        int(helper_details.cyclomatic_complexity or 0),
                        common_analysis.count_objects_created(helper_details),
                    )

                helper_metrics = helper_metric_cache.get(helper_ref)
                if helper_metrics is None:
                    continue

                helper_ncloc, helper_complexity, helper_object_count = helper_metrics
                expanded_ncloc += helper_ncloc
                expanded_cyclomatic_complexity += helper_complexity
                number_of_objects_created += helper_object_count
                expanded_helper_method_count += 1
                expanded_helper_method_ncloc += helper_ncloc

        return (
            expanded_ncloc,
            expanded_cyclomatic_complexity,
            expanded_helper_method_count,
            expanded_helper_method_ncloc,
            number_of_objects_created,
        )

    @staticmethod
    def _build_fixture_analyses(
        runtime_view: TestRuntimeView,
        common_analysis: CommonAnalysis,
    ) -> list[FixtureAnalysis]:
        fixture_analyses_by_key: dict[
            tuple[LifecyclePhase, str, str],
            tuple[
                PhaseEntry,
                list[HttpRequestInteraction],
                list[HttpVerificationInteraction],
                list[HttpInteraction],
            ],
        ] = {}
        fixture_entry_order: list[tuple[LifecyclePhase, str, str]] = []

        for entry in runtime_view.entries:
            if entry.phase == LifecyclePhase.TEST:
                continue

            fixture_key = (
                entry.phase,
                entry.method_ref.defining_class_name,
                entry.method_ref.method_signature,
            )
            fixture_entry_order.append(fixture_key)
            fixture_analyses_by_key[fixture_key] = (entry, [], [], [])

        for event in runtime_view.iter_events():
            if event.phase == LifecyclePhase.TEST:
                continue

            entry_ref = event.entry_method_ref or event.owner
            fixture_key = (
                event.phase,
                entry_ref.defining_class_name,
                entry_ref.method_signature,
            )
            fixture_bucket = fixture_analyses_by_key.get(fixture_key)
            if fixture_bucket is None:
                continue

            (
                _,
                entry_request_interactions,
                entry_verification_interactions,
                entry_http_interactions,
            ) = fixture_bucket
            request_interaction = build_http_request_interaction_for_event(event)
            if request_interaction is not None:
                entry_request_interactions.append(request_interaction)
            verification_interaction = build_http_verification_interaction_for_event(
                event
            )
            if verification_interaction is not None:
                entry_verification_interactions.append(verification_interaction)
            for http_interaction in MethodAnalysisInfo._build_http_interactions(
                event=event,
                request_interaction=request_interaction,
                verification_interaction=verification_interaction,
            ):
                entry_http_interactions.append(http_interaction)

        fixture_analyses: list[FixtureAnalysis] = []
        for ordered_fixture_key in fixture_entry_order:
            (
                entry,
                entry_request_interactions,
                entry_verification_interactions,
                entry_http_interactions,
            ) = fixture_analyses_by_key[ordered_fixture_key]

            entry_ncloc: int = 0
            if entry.method_details is not None:
                entry_ncloc = common_analysis.get_ncloc(
                    entry.method_details.declaration,
                    entry.method_details.code,
                )

            entry_annotations = (
                list(entry.method_details.annotations or [])
                if entry.method_details
                else []
            )
            fixture_analyses.append(
                FixtureAnalysis(
                    phase=entry.phase,
                    defining_class_name=entry.method_ref.defining_class_name,
                    method_signature=entry.method_ref.method_signature,
                    annotations=entry_annotations,
                    ncloc=entry_ncloc,
                    request_interaction_count=len(entry_request_interactions),
                    request_interactions=entry_request_interactions,
                    verification_interaction_count=len(entry_verification_interactions),
                    verification_interactions=entry_verification_interactions,
                    http_interaction_count=len(entry_http_interactions),
                    http_interactions=entry_http_interactions,
                )
            )

        return fixture_analyses

    @staticmethod
    def _build_source_span(node: CallSiteNode) -> SourceSpan:
        return SourceSpan(
            start_line=node.span.start.line,
            start_column=node.span.start.col,
            end_line=node.span.end.line,
            end_column=node.span.end.col,
        )

    @staticmethod
    def _build_http_interactions(
        *,
        event: RuntimeEvent,
        request_interaction: HttpRequestInteraction | None,
        verification_interaction: HttpVerificationInteraction | None,
    ) -> list[HttpInteraction]:
        interactions: list[HttpInteraction] = []
        if request_interaction is not None:
            interactions.append(
                HttpInteraction(
                    kind=HttpInteractionKind.REQUEST,
                    origin=request_interaction.origin,
                    source_span=MethodAnalysisInfo._build_source_span(event.node),
                    request_interaction=request_interaction,
                )
            )
        if verification_interaction is not None:
            interactions.append(
                HttpInteraction(
                    kind=HttpInteractionKind.VERIFICATION,
                    origin=verification_interaction.origin,
                    source_span=verification_interaction.source_span,
                    verification_interaction=verification_interaction,
                )
            )
        return interactions

    @staticmethod
    def _build_ambiguous_fixture_notes(
        runtime_view: TestRuntimeView,
    ) -> list[FixtureAmbiguityNote]:
        notes: list[FixtureAmbiguityNote] = []
        for entry in runtime_view.entries:
            if entry.phase == LifecyclePhase.TEST:
                continue
            if not entry.is_group_ambiguous:
                continue
            notes.append(
                FixtureAmbiguityNote(
                    phase=entry.phase,
                    defining_class_name=entry.method_ref.defining_class_name,
                    method_signature=entry.method_ref.method_signature,
                )
            )
        return notes
