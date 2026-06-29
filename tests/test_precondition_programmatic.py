from __future__ import annotations

from gerbil.analysis.properties.precondition_analysis import (
    analyze_preconditions,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    LifecyclePhase,
    Precondition,
    PreconditionSource,
    PreconditionType,
)

from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_import_declarations,
    make_resolved_annotation,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


_SETUP_CLASS = "example.TestClass"


def _runtime_view_with_phase(
    *,
    phase: LifecyclePhase,
    call_sites,
    method_signature: str = "beforeEach()",
) -> TestRuntimeView:
    method = make_callable(signature=method_signature, call_sites=call_sites)
    entry = PhaseEntry(
        phase=phase,
        method_ref=MethodRef(
            defining_class_name=_SETUP_CLASS,
            method_signature=method_signature,
        ),
        context_class_name=_SETUP_CLASS,
        grouping=build_call_site_grouping(list(method.call_sites)),
        method_details=method,
    )
    return TestRuntimeView(entries=[entry])


def _analyze(
    runtime_view: TestRuntimeView,
    *,
    class_annotations=None,
    method_annotations=None,
    class_annotation_imports_by_class=None,
    method_imports=None,
    analysis=None,
):
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        analysis=analysis,
    )
    return analyze_preconditions(
        class_annotations=class_annotations or [],
        method_annotations=method_annotations or [],
        class_annotation_imports_by_class=class_annotation_imports_by_class or {},
        method_imports=method_imports or [],
        runtime_view=runtime_view,
        analysis=analysis,
        receiver_resolver=resolver,
    )


def _assert_single(
    summary,
    *,
    type_: PreconditionType,
    source: PreconditionSource,
    evidence: str,
) -> None:
    assert summary.preconditions == [
        Precondition(type=type_, source=source, evidence=evidence)
    ]


# ---------------------------------------------------------------------------
# Happy paths — one per precondition type
# ---------------------------------------------------------------------------


def test_jdbctemplate_update_in_setup_is_db_seeding() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
            )
        ],
    )
    summary = _analyze(runtime)
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.springframework.jdbc.update",
    )


def test_entitymanager_persist_in_setup_is_db_seeding() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="persist",
                receiver_type="jakarta.persistence.EntityManager",
            )
        ],
    )
    summary = _analyze(runtime)
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="jakarta.persistence.EntityManager.persist",
    )


def _java_file_path(qualified_class_name: str) -> str:
    return f"src/main/java/{qualified_class_name.replace('.', '/')}.java"


def test_jparepository_subinterface_save_resolves_via_hierarchy() -> None:
    """A user-defined interface that extends a generic Spring Data repository
    supertype matches the programmatic map via the resolved receiver hierarchy."""

    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=[
                    "org.springframework.data.jpa.repository.JpaRepository<example.User, java.lang.Long>"
                ],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={java_file: []},
    )
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[make_call_site(method_name="save", receiver_type=repo_type)],
    )
    summary = _analyze(runtime, analysis=analysis)
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.springframework.data.save",
    )


def test_jparepository_subinterface_save_resolves_via_import() -> None:
    """A bare Spring Data supertype reference is qualified through its import."""

    repo_type = "example.UserRepositoryBare"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=["JpaRepository<example.User, java.lang.Long>"],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={
            java_file: ["org.springframework.data.jpa.repository.JpaRepository"]
        },
    )
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[make_call_site(method_name="save", receiver_type=repo_type)],
    )
    summary = _analyze(runtime, analysis=analysis)
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.springframework.data.save",
    )


def test_jparepository_subinterface_implements_save_resolves_via_hierarchy() -> None:
    """A class that implements a generic Spring Data repository interface still
    exposes the repository supertype in its hierarchy."""

    repo_type = "example.UserRepositoryImpl"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                implements_list=[
                    "org.springframework.data.jpa.repository.JpaRepository<example.User, java.lang.Long>"
                ],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={java_file: []},
    )
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[make_call_site(method_name="save", receiver_type=repo_type)],
    )
    summary = _analyze(runtime, analysis=analysis)
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.springframework.data.save",
    )


def test_custom_postgresqlcontainer_start_is_container_bootstrap() -> None:
    """A custom Testcontainers subclass exposes the library supertype so that
    start() is recognized as container bootstrap."""

    container_type = "example.MyPg"
    java_file = _java_file_path(container_type)
    analysis = FakeJavaAnalysis(
        classes={
            container_type: make_type(
                extends_list=[
                    "org.testcontainers.containers.PostgreSQLContainer<example.MyPg>"
                ],
            ),
        },
        java_files={container_type: java_file},
        import_declarations_by_file={java_file: []},
    )
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type=container_type,
            )
        ],
    )
    summary = _analyze(runtime, analysis=analysis)
    _assert_single(
        summary,
        type_=PreconditionType.CONTAINER_BOOTSTRAP,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.testcontainers.start",
    )


def test_generic_container_start_in_setup_is_container_bootstrap() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="org.testcontainers.containers.PostgreSQLContainer",
            )
        ],
    )
    summary = _analyze(runtime)
    _assert_single(
        summary,
        type_=PreconditionType.CONTAINER_BOOTSTRAP,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.testcontainers.start",
    )


def test_kafkatemplate_send_in_setup_is_mq_seeding() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="send",
                receiver_type="org.springframework.kafka.core.KafkaTemplate",
            )
        ],
    )
    summary = _analyze(runtime)
    _assert_single(
        summary,
        type_=PreconditionType.MQ_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="org.springframework.kafka.core.KafkaTemplate.send",
    )


def test_files_writestring_in_setup_is_fs_seeding() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="writeString",
                receiver_type="java.nio.file.Files",
            )
        ],
    )
    summary = _analyze(runtime)
    _assert_single(
        summary,
        type_=PreconditionType.FS_SEEDING,
        source=PreconditionSource.PROGRAMMATIC,
        evidence="java.nio.file.Files.writeString",
    )


# ---------------------------------------------------------------------------
# False-positive guards
# ---------------------------------------------------------------------------


def test_jdbctemplate_update_in_test_phase_is_not_precondition() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.TEST,
        call_sites=[
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
            )
        ],
        method_signature="testBody()",
    )
    summary = _analyze(runtime)
    assert summary.preconditions == []


def test_jdbctemplate_update_in_teardown_phase_is_not_precondition() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.TEARDOWN,
        call_sites=[
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
            )
        ],
        method_signature="afterEach()",
    )
    summary = _analyze(runtime)
    assert summary.preconditions == []


def test_unknown_receiver_with_seeding_method_does_not_match() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="save",
                receiver_type="com.example.NotARepository",
            )
        ],
    )
    summary = _analyze(runtime)
    assert summary.preconditions == []


def test_entitymanager_find_is_observation_not_seeding() -> None:
    """EntityManager.find is a read, not a write — must not be tagged."""

    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="find",
                receiver_type="jakarta.persistence.EntityManager",
            )
        ],
    )
    summary = _analyze(runtime)
    assert summary.preconditions == []


# ---------------------------------------------------------------------------
# Annotation tier — seeding vs post-test assertion annotations
# ---------------------------------------------------------------------------


def test_dataset_annotation_is_db_seeding() -> None:
    runtime = _runtime_view_with_phase(phase=LifecyclePhase.SETUP, call_sites=[])
    summary = _analyze(
        runtime,
        method_annotations=['@DataSet("users.yml")'],
    )
    _assert_single(
        summary,
        type_=PreconditionType.DB_SEEDING,
        source=PreconditionSource.ANNOTATION,
        evidence="@DataSet",
    )


def test_expected_dataset_annotation_is_not_db_seeding() -> None:
    """@ExpectedDataSet (database-rider) asserts post-test database state; it
    is an oracle, not a precondition."""

    runtime = _runtime_view_with_phase(phase=LifecyclePhase.SETUP, call_sites=[])
    summary = _analyze(
        runtime,
        class_annotations=[
            make_resolved_annotation(
                annotation='@ExpectedDataSet("expected.yml")',
                declaring_class_name=_SETUP_CLASS,
            )
        ],
        method_annotations=['@ExpectedDataSet("expected.yml")'],
    )
    assert summary.preconditions == []


def test_expected_database_annotation_is_not_db_seeding() -> None:
    """@ExpectedDatabase (spring-test-dbunit) verifies database contents after
    the test completes; @DatabaseSetup remains the seeding annotation."""

    runtime = _runtime_view_with_phase(phase=LifecyclePhase.SETUP, call_sites=[])
    summary = _analyze(
        runtime,
        method_annotations=['@ExpectedDatabase("expected.xml")'],
    )
    assert summary.preconditions == []


# ---------------------------------------------------------------------------
# Dedupe + tier combination
# ---------------------------------------------------------------------------


def test_duplicate_setup_call_sites_dedupe_to_single_entry() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
                start_line=5,
            ),
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
                start_line=10,
            ),
        ],
    )
    summary = _analyze(runtime)
    assert len(summary.preconditions) == 1
    assert summary.preconditions[0].source == PreconditionSource.PROGRAMMATIC


def test_annotation_and_programmatic_tiers_coexist() -> None:
    runtime = _runtime_view_with_phase(
        phase=LifecyclePhase.SETUP,
        call_sites=[
            make_call_site(
                method_name="update",
                receiver_type="org.springframework.jdbc.core.JdbcTemplate",
            )
        ],
    )
    summary = _analyze(
        runtime,
        class_annotations=[
            make_resolved_annotation(
                annotation="@Sql",
                declaring_class_name=_SETUP_CLASS,
            )
        ],
        class_annotation_imports_by_class={
            _SETUP_CLASS: make_import_declarations(
                "org.springframework.test.context.jdbc.Sql"
            )
        },
    )

    sources = sorted((p.source.value, p.evidence) for p in summary.preconditions)
    assert sources == [
        ("annotation", "@Sql"),
        ("programmatic", "org.springframework.jdbc.update"),
    ]
    assert all(p.type == PreconditionType.DB_SEEDING for p in summary.preconditions)
