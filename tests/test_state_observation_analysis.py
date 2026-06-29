from __future__ import annotations

from gerbil.analysis.properties.state_observation_analysis import (
    analyze_state_observations,
    db_state_assertion_observations,
    detect_db_state_assertion_annotations,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    CallSiteNode,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    AssertionRole,
    LifecyclePhase,
    StateObservationMedium,
    StateObservationTier,
)

from tests.cldk_factories import (
    annotate_node_assertion,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_resolved_annotation,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


_TEST_CLASS = "example.TestClass"


def _build_runtime_view(
    *,
    phase: LifecyclePhase = LifecyclePhase.TEST,
    call_sites,
    variable_declarations=None,
    method_signature: str = "testMethod()",
) -> tuple[TestRuntimeView, CallSiteGrouping]:
    method = make_callable(
        signature=method_signature,
        call_sites=list(call_sites),
        variable_declarations=list(variable_declarations or []),
    )
    grouping = build_call_site_grouping(list(method.call_sites))
    entry = PhaseEntry(
        phase=phase,
        method_ref=MethodRef(
            defining_class_name=_TEST_CLASS,
            method_signature=method_signature,
        ),
        context_class_name=_TEST_CLASS,
        grouping=grouping,
        method_details=method,
    )
    view = TestRuntimeView(entries=[entry])
    return view, grouping


def _node_for(call_site, grouping: CallSiteGrouping) -> CallSiteNode:
    node = grouping.node_for_call_site(call_site)
    if node is None:
        raise AssertionError(f"node not found for call site {call_site.method_name}")
    return node


def _analyze(view: TestRuntimeView, *, analysis=None):
    resolver = build_runtime_receiver_resolver_for_testing(view, analysis=analysis)
    return analyze_state_observations(
        runtime_view=view,
        analysis=analysis,
        receiver_resolver=resolver,
    )


# ---------------------------------------------------------------------------
# Tier 1 — ancestor-walk (nested)
# ---------------------------------------------------------------------------


def test_findbyid_nested_in_assertthat_emits_db_nested() -> None:
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.DB
    assert obs.tier == StateObservationTier.NESTED
    assert obs.method_name == "findById"
    assert obs.evidence == "org.springframework.data.findById"
    assert obs.start_line == 5


def test_kafka_poll_nested_in_assertequals_emits_mq_nested() -> None:
    assert_call = make_call_site(
        method_name="assertEquals",
        receiver_type="org.junit.jupiter.api.Assertions",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
        is_static_call=True,
    )
    poll_call = make_call_site(
        method_name="poll",
        receiver_type="org.apache.kafka.clients.consumer.KafkaConsumer",
        receiver_expr="consumer",
        start_line=10,
        start_column=20,
        end_line=10,
        end_column=45,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, poll_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.MQ
    assert obs.tier == StateObservationTier.NESTED
    assert obs.method_name == "poll"


def test_files_readstring_nested_in_assertj_emits_fs_nested() -> None:
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=8,
        start_column=1,
        end_line=8,
        end_column=70,
        is_static_call=True,
    )
    read_call = make_call_site(
        method_name="readString",
        receiver_type="java.nio.file.Files",
        start_line=8,
        start_column=12,
        end_line=8,
        end_column=40,
        is_static_call=True,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, read_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.FS
    assert obs.tier == StateObservationTier.NESTED


def test_jparepository_subinterface_resolves_via_hierarchy() -> None:
    """User-defined repository extending a generic Spring Data interface —
    receiver resolution walks the library supertype chain."""

    repo_type = "example.UserRepository"
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=[
                    "org.springframework.data.jpa.repository.JpaRepository<example.User, java.lang.Long>"
                ],
            ),
        },
    )
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=7,
        start_column=1,
        end_line=7,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type=repo_type,
        receiver_expr="userRepo",
        start_line=7,
        start_column=12,
        end_line=7,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view, analysis=analysis)

    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.DB
    assert obs.tier == StateObservationTier.NESTED
    assert obs.receiver_type == repo_type


def test_spring_data_derived_query_emits_db_observation() -> None:
    # Derived query reads (findByEmail / existsByUsername / countByStatus) are
    # the common Spring Data oracle and must be recognized alongside findById.
    for method_name in (
        "findByEmail",
        "existsByUsername",
        "countByStatus",
        "searchByName",
    ):
        assert_call = make_call_site(
            method_name="assertThat",
            receiver_type="org.assertj.core.api.Assertions",
            start_line=5,
            start_column=1,
            end_line=5,
            end_column=80,
            is_static_call=True,
        )
        find_call = make_call_site(
            method_name=method_name,
            receiver_type="org.springframework.data.jpa.repository.JpaRepository",
            receiver_expr="repo",
            start_line=5,
            start_column=12,
            end_line=5,
            end_column=40,
        )
        view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
        annotate_node_assertion(
            _node_for(assert_call, grouping), role=AssertionRole.BODY
        )

        summary = _analyze(view)

        assert len(summary.observations) == 1, method_name
        obs = summary.observations[0]
        assert obs.medium == StateObservationMedium.DB
        assert obs.method_name == method_name
        assert obs.evidence == f"org.springframework.data.{method_name}"


def test_non_repository_derived_query_name_is_not_an_observation() -> None:
    # The derived-query pattern is gated to Spring Data receivers, so a same-named
    # method on an arbitrary service must not be mistaken for a DB read.
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findByEmail",
        receiver_type="com.example.UserService",
        receiver_expr="service",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert summary.observations == []


# ---------------------------------------------------------------------------
# Tier 2 — variable-binding
# ---------------------------------------------------------------------------


def test_binding_to_local_var_then_assert_on_getter_emits_db_binding() -> None:
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=20,
        end_line=5,
        end_column=55,
    )
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["row.getStatus()"],
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=50,
        is_static_call=True,
    )
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id).orElseThrow()",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=60,
    )
    view, grouping = _build_runtime_view(
        call_sites=[find_call, assert_call],
        variable_declarations=[row_decl],
    )
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.DB
    assert obs.tier == StateObservationTier.BINDING
    assert obs.method_name == "findById"


def test_binding_used_in_assertion_argument_emits_mq_binding() -> None:
    poll_call = make_call_site(
        method_name="poll",
        receiver_type="org.apache.kafka.clients.consumer.KafkaConsumer",
        receiver_expr="consumer",
        start_line=3,
        start_column=25,
        end_line=3,
        end_column=50,
    )
    assert_call = make_call_site(
        method_name="assertEquals",
        receiver_type="org.junit.jupiter.api.Assertions",
        argument_expr=['"x"', "rec.value()"],
        start_line=4,
        start_column=1,
        end_line=4,
        end_column=40,
        is_static_call=True,
    )
    rec_decl = make_variable_declaration(
        name="rec",
        type_name="org.apache.kafka.clients.consumer.ConsumerRecord",
        initializer="consumer.poll(Duration.ofSeconds(1))",
        start_line=3,
        start_column=1,
        end_line=3,
        end_column=55,
    )
    view, grouping = _build_runtime_view(
        call_sites=[poll_call, assert_call],
        variable_declarations=[rec_decl],
    )
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    assert summary.observations[0].tier == StateObservationTier.BINDING
    assert summary.observations[0].medium == StateObservationMedium.MQ


def test_binding_matched_in_assertj_extracting_descendant() -> None:
    """The variable name can appear in a descendant of the assertion root
    (e.g. an .extracting(...) call in a fluent chain)."""

    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=25,
        end_line=5,
        end_column=55,
    )
    assert_root = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["list"],
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=80,
        is_static_call=True,
    )
    extracting_call = make_call_site(
        method_name="extracting",
        receiver_type="org.assertj.core.api.ListAssert",
        argument_expr=["row::getName"],
        start_line=6,
        start_column=20,
        end_line=6,
        end_column=50,
    )
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id).orElseThrow()",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=60,
    )
    view, grouping = _build_runtime_view(
        call_sites=[find_call, assert_root, extracting_call],
        variable_declarations=[row_decl],
    )
    annotate_node_assertion(_node_for(assert_root, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)

    assert len(summary.observations) == 1
    assert summary.observations[0].tier == StateObservationTier.BINDING


# ---------------------------------------------------------------------------
# Negative paths — must NOT emit (conservative posture checks)
# ---------------------------------------------------------------------------


def test_setup_phase_read_does_not_emit() -> None:
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(
        phase=LifecyclePhase.SETUP, call_sites=[assert_call, find_call]
    )
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert summary.observations == []


def test_teardown_phase_read_emits_observation() -> None:
    """TEARDOWN is in the observation reach set; mirror of the SETUP negative
    test — locks in that cleanup reads are tracked alongside test-body reads."""

    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(
        phase=LifecyclePhase.TEARDOWN, call_sites=[assert_call, find_call]
    )
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert len(summary.observations) == 1
    obs = summary.observations[0]
    assert obs.medium == StateObservationMedium.DB
    assert obs.tier == StateObservationTier.NESTED


def test_read_without_assertion_reach_does_not_emit() -> None:
    """A DB read with no assertion anywhere → no emission (no UNKNOWN bucket)."""

    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=30,
    )
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id)",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=40,
    )
    view, _ = _build_runtime_view(
        call_sites=[find_call], variable_declarations=[row_decl]
    )

    summary = _analyze(view)
    assert summary.observations == []


def test_write_method_in_test_phase_does_not_emit() -> None:
    """``save`` is not in the OBSERVATION_MEDIUM_* maps; it's a write. Even
    nested inside an assertion it must be ignored."""

    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    save_call = make_call_site(
        method_name="save",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, save_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert summary.observations == []


def test_unknown_receiver_type_does_not_emit() -> None:
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="",
        receiver_expr="something",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert summary.observations == []


def test_variable_name_substring_match_does_not_emit() -> None:
    """Declaration name ``row``; assertion argument ``narrow`` — word-boundary
    regex must NOT match."""

    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=20,
        end_line=5,
        end_column=50,
    )
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["narrow.getStatus()"],
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=50,
        is_static_call=True,
    )
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id)",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=55,
    )
    view, _ = _build_runtime_view(
        call_sites=[find_call, assert_call],
        variable_declarations=[row_decl],
    )

    summary = _analyze(view)
    assert summary.observations == []


def test_assertion_before_read_does_not_emit_binding() -> None:
    """Assertion line < read line — positional gate rejects Tier 2."""

    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["row.getStatus()"],
        start_line=4,
        start_column=1,
        end_line=4,
        end_column=50,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=20,
        end_line=5,
        end_column=50,
    )
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id)",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=55,
    )
    view, _ = _build_runtime_view(
        call_sites=[assert_call, find_call],
        variable_declarations=[row_decl],
    )

    summary = _analyze(view)
    assert summary.observations == []


def test_binding_initializer_does_not_mention_method_does_not_emit() -> None:
    """Declaration spans the read's line but initializer does not reference the
    observation method — no binding."""

    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=20,
        end_line=5,
        end_column=50,
    )
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["row.getStatus()"],
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=50,
        is_static_call=True,
    )
    unrelated_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="someOtherFactory.build()",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=55,
    )
    view, _ = _build_runtime_view(
        call_sites=[find_call, assert_call],
        variable_declarations=[unrelated_decl],
    )

    summary = _analyze(view)
    assert summary.observations == []


# ---------------------------------------------------------------------------
# Precedence / dedupe
# ---------------------------------------------------------------------------


def test_read_qualifying_for_both_tiers_emits_once_as_nested() -> None:
    """A read that is both lexically nested in an assertion AND has a matching
    variable binding should emit a single NESTED entry (Tier 1 precedence)."""

    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        argument_expr=["row.getStatus()"],
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    # A decl that spans line 5 and mentions findById — would satisfy Tier 2 too
    # if Tier 1 didn't take precedence. (Shape is contrived for the precedence
    # check.)
    row_decl = make_variable_declaration(
        name="row",
        type_name="example.Order",
        initializer="repo.findById(id).orElseThrow()",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=60,
    )
    view, grouping = _build_runtime_view(
        call_sites=[assert_call, find_call],
        variable_declarations=[row_decl],
    )
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert len(summary.observations) == 1
    assert summary.observations[0].tier == StateObservationTier.NESTED


# ---------------------------------------------------------------------------
# Sorting / stability
# ---------------------------------------------------------------------------


def test_output_sort_is_stable_across_mediums_and_lines() -> None:
    """Observations are sorted by (medium, start_line, method_name, evidence)."""

    assert_call_db = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=80,
        is_static_call=True,
    )
    find_call_db = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=40,
    )
    assert_call_fs = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    read_call_fs = make_call_site(
        method_name="readString",
        receiver_type="java.nio.file.Files",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
        is_static_call=True,
    )
    view, grouping = _build_runtime_view(
        call_sites=[assert_call_fs, read_call_fs, assert_call_db, find_call_db]
    )
    annotate_node_assertion(
        _node_for(assert_call_fs, grouping), role=AssertionRole.BODY
    )
    annotate_node_assertion(
        _node_for(assert_call_db, grouping), role=AssertionRole.BODY
    )

    summary = _analyze(view)
    assert len(summary.observations) == 2
    # Sort key: medium.value — "db" < "fs" alphabetically.
    assert summary.observations[0].medium == StateObservationMedium.DB
    assert summary.observations[1].medium == StateObservationMedium.FS


# ---------------------------------------------------------------------------
# Summary property helpers
# ---------------------------------------------------------------------------


def test_summary_per_medium_counts_and_flags() -> None:
    assert_call = make_call_site(
        method_name="assertThat",
        receiver_type="org.assertj.core.api.Assertions",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=80,
        is_static_call=True,
    )
    find_call = make_call_site(
        method_name="findById",
        receiver_type="org.springframework.data.repository.CrudRepository",
        receiver_expr="repo",
        start_line=5,
        start_column=12,
        end_line=5,
        end_column=40,
    )
    view, grouping = _build_runtime_view(call_sites=[assert_call, find_call])
    annotate_node_assertion(_node_for(assert_call, grouping), role=AssertionRole.BODY)

    summary = _analyze(view)
    assert summary.has_any is True
    assert summary.total_count == 1
    counts = summary.counts_by_medium
    assert counts[StateObservationMedium.DB] == 1
    assert counts[StateObservationMedium.MQ] == 0
    assert counts[StateObservationMedium.FS] == 0


def test_empty_summary_when_no_observation() -> None:
    view, _ = _build_runtime_view(call_sites=[])
    summary = _analyze(view)
    assert summary.has_any is False
    assert summary.total_count == 0


# ---------------------------------------------------------------------------
# Annotation-declared DB postcondition assertions
# ---------------------------------------------------------------------------


def test_expected_dataset_method_annotation_is_db_state_assertion() -> None:
    signals = detect_db_state_assertion_annotations(
        class_annotations=[],
        method_annotations=['@ExpectedDataSet("expected.yml")'],
        class_annotation_imports_by_class={},
        method_imports=[],
    )
    assert signals == ["@ExpectedDataSet"]


def test_expected_database_class_annotation_is_db_state_assertion() -> None:
    signals = detect_db_state_assertion_annotations(
        class_annotations=[
            make_resolved_annotation(
                annotation='@ExpectedDatabase("expected.xml")',
                declaring_class_name=_TEST_CLASS,
            )
        ],
        method_annotations=[],
        class_annotation_imports_by_class={},
        method_imports=[],
    )
    assert signals == ["@ExpectedDatabase"]


def test_class_and_method_db_state_assertions_dedupe_and_sort() -> None:
    signals = detect_db_state_assertion_annotations(
        class_annotations=[
            make_resolved_annotation(
                annotation='@ExpectedDatabase("expected.xml")',
                declaring_class_name=_TEST_CLASS,
            )
        ],
        method_annotations=[
            '@ExpectedDataSet("expected.yml")',
            "@ExpectedDataSet",
        ],
        class_annotation_imports_by_class={},
        method_imports=[],
    )
    assert signals == ["@ExpectedDataSet", "@ExpectedDatabase"]


def test_seeding_annotations_are_not_db_state_assertions() -> None:
    signals = detect_db_state_assertion_annotations(
        class_annotations=[
            make_resolved_annotation(
                annotation='@DataSet("users.yml")',
                declaring_class_name=_TEST_CLASS,
            )
        ],
        method_annotations=['@DatabaseSetup("setup.xml")', "@Sql"],
        class_annotation_imports_by_class={},
        method_imports=[],
    )
    assert signals == []


def test_db_state_assertion_annotations_become_db_observations() -> None:
    observations = db_state_assertion_observations(
        class_annotations=[],
        method_annotations=['@ExpectedDataSet("expected.yml")'],
        class_annotation_imports_by_class={},
        method_imports=[],
    )

    assert [(obs.medium, obs.tier, obs.evidence) for obs in observations] == [
        (
            StateObservationMedium.DB,
            StateObservationTier.ANNOTATION,
            "@ExpectedDataSet",
        )
    ]
