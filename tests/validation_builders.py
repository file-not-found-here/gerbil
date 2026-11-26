"""A small Java project and its matching model, for the validation tests."""

from __future__ import annotations

from pathlib import Path

from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    BuilderCorrelationSource,
    CallSiteOriginKind,
    FixtureAnalysis,
    HttpAnalysis,
    HttpCallSite,
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    HttpTestSequence,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    ProjectAnalysis,
    ProjectMetadata,
    ProjectSummary,
    SequenceStepKind,
    SourceSpan,
    TestClassAnalysis,
    TestMethodAnalysis,
)

TEST_CLASS = "demo.WidgetTest"
BASE_CLASS = "demo.BaseTest"

WIDGET_TEST = """\
package demo;

import org.junit.jupiter.api.Test;

class WidgetTest extends BaseTest {
    @Test
    void createsWidget() throws Exception {
        mockMvc.perform(post("/widgets").header("X-Tenant", "a"))
            .andExpect(status().isCreated());
        fetch("/widgets/1");
        cleanup();
    }

    private void fetch(String path) throws Exception {
        mockMvc.perform(get(path)).andExpect(status().isOk());
    }
}
"""

BASE_TEST = """\
package demo;

import org.junit.jupiter.api.BeforeEach;

class BaseTest {
    @BeforeEach
    void seed() {
        repository.save(new Widget());
    }

    protected void cleanup() {
        repository.deleteAll();
    }
}
"""


def write_repo(root: Path) -> Path:
    package = root / "src" / "test" / "java" / "demo"
    package.mkdir(parents=True)
    (package / "WidgetTest.java").write_text(WIDGET_TEST, encoding="utf-8")
    (package / "BaseTest.java").write_text(BASE_TEST, encoding="utf-8")
    return root


def span_ending(source: str, needle: str) -> SourceSpan:
    """The span of a call whose text ends where `needle` first ends in the source."""
    end = source.index(needle) + len(needle)
    line = source.count("\n", 0, end) + 1
    column = end - (source.rfind("\n", 0, end) + 1)
    return SourceSpan(start_line=line, start_column=1, end_line=line, end_column=column)


def _origin(kind: CallSiteOriginKind, signature: str) -> OriginContext:
    return OriginContext(
        phase=LifecyclePhase.TEST,
        kind=kind,
        defining_class_name=TEST_CLASS,
        method_signature=signature,
    )


_TEST_ORIGIN = _origin(CallSiteOriginKind.TEST_METHOD, "createsWidget()")
_HELPER_ORIGIN = _origin(CallSiteOriginKind.TEST_HELPER, "fetch(java.lang.String)")


def _step(
    order: int,
    kind: SequenceStepKind,
    method_name: str,
    needle: str,
    origin: OriginContext,
    **fields: object,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=kind,
        phase=LifecyclePhase.TEST,
        origin=origin,
        method_name=method_name,
        source_span=span_ending(WIDGET_TEST, needle),
        **fields,  # type: ignore[arg-type]
    )


def _call(
    step: ApiSequenceStep,
    role: HttpRequestRole,
    *,
    header_names: list[str] | None = None,
    sources: list[BuilderCorrelationSource] | None = None,
) -> HttpRequestInteraction:
    return HttpRequestInteraction(
        origin=step.origin,
        http_call=HttpCallSite(
            http_method=step.http_method or "UNKNOWN",
            path=step.http_path or "",
            framework=HttpDispatchFramework.MOCKMVC,
            request_role=role,
            method_name=step.method_name,
            start_line=step.source_span.start_line,
            header_names=header_names or [],
            correlated_builder_sources=sources or [],
        ),
    )


def _source(step: ApiSequenceStep, properties: list[str]) -> BuilderCorrelationSource:
    return BuilderCorrelationSource(
        method_name=step.method_name,
        start_line=step.source_span.start_line,
        contributed_properties=properties,
    )


def widget_test() -> TestMethodAnalysis:
    """`createsWidget`: one step in the test body and one, with no path, in a helper."""
    build, request, check = (
        SequenceStepKind.REQUEST_BUILD,
        SequenceStepKind.HTTP_REQUEST,
        SequenceStepKind.RESPONSE_CHECK,
    )
    post = _step(
        1,
        build,
        "post",
        'post("/widgets")',
        _TEST_ORIGIN,
        http_method="POST",
        http_path="/widgets",
    )
    header = _step(2, build, "header", '.header("X-Tenant", "a")', _TEST_ORIGIN)
    perform = _step(
        3,
        request,
        "perform",
        '.header("X-Tenant", "a"))',
        _TEST_ORIGIN,
        http_method="POST",
        http_path="/widgets",
    )
    created = _step(
        4,
        check,
        "isCreated",
        "isCreated()",
        _TEST_ORIGIN,
        assertion_role=AssertionRole.STATUS,
        status_code=201,
        status_range="2xx",
    )
    get = _step(5, build, "get", "get(path)", _HELPER_ORIGIN, http_method="GET")
    helper_perform = _step(
        6, request, "perform", "perform(get(path))", _HELPER_ORIGIN, http_method="GET"
    )
    ok = _step(
        7,
        check,
        "isOk",
        "isOk()",
        _HELPER_ORIGIN,
        assertion_role=AssertionRole.STATUS,
        status_code=200,
        status_range="2xx",
    )
    first, second = [post, header, perform, created], [get, helper_perform, ok]
    return TestMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name=TEST_CLASS,
            method_signature="createsWidget()",
            method_declaration="void createsWidget()",
        ),
        is_api_test=True,
        fixtures=[
            FixtureAnalysis(
                phase=LifecyclePhase.SETUP,
                defining_class_name=BASE_CLASS,
                method_signature="seed()",
            )
        ],
        http=HttpAnalysis(
            call_sequence=[*first, *second],
            test_sequences=[
                HttpTestSequence(order=1, steps=first, fingerprint="a"),
                HttpTestSequence(order=2, steps=second, fingerprint="b"),
            ],
            request_interactions=[
                _call(post, HttpRequestRole.BUILDER),
                _call(header, HttpRequestRole.BUILDER, header_names=["X-Tenant"]),
                _call(
                    perform,
                    HttpRequestRole.EVENT,
                    header_names=["X-Tenant"],
                    sources=[
                        _source(post, ["http_method", "path"]),
                        _source(header, ["headers", "header_names"]),
                    ],
                ),
                _call(get, HttpRequestRole.BUILDER),
                _call(
                    helper_perform,
                    HttpRequestRole.EVENT,
                    sources=[_source(get, ["http_method"])],
                ),
            ],
        ),
    )


def api_test(
    signature: str, step_count: int, *, in_helper: bool = False
) -> TestMethodAnalysis:
    """An API test with `step_count` bare dispatches, for the sampler."""
    origin = OriginContext(
        phase=LifecyclePhase.TEST,
        kind=(
            CallSiteOriginKind.TEST_HELPER
            if in_helper
            else CallSiteOriginKind.TEST_METHOD
        ),
    )
    sequences = [
        HttpTestSequence(
            order=order,
            fingerprint=str(order),
            steps=[
                ApiSequenceStep(
                    order=order,
                    kind=SequenceStepKind.HTTP_REQUEST,
                    phase=LifecyclePhase.TEST,
                    origin=origin,
                    method_name="perform",
                    source_span=SourceSpan(
                        start_line=1, start_column=1, end_line=1, end_column=2
                    ),
                )
            ],
        )
        for order in range(1, step_count + 1)
    ]
    return TestMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name=TEST_CLASS,
            method_signature=signature,
            method_declaration=f"void {signature}",
        ),
        is_api_test=True,
        http=HttpAnalysis(test_sequences=sequences),
    )


def non_api_test(signature: str) -> TestMethodAnalysis:
    return TestMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name=TEST_CLASS,
            method_signature=signature,
            method_declaration=f"void {signature}",
        )
    )


def project(
    name: str,
    tests: list[TestMethodAnalysis],
    *,
    project_path: str = "/analyzed/demo",
    class_name: str = TEST_CLASS,
) -> ProjectAnalysis:
    return ProjectAnalysis(
        dataset_name=name,
        metadata=ProjectMetadata(project_path=project_path),
        summary=ProjectSummary(api_test_count=sum(t.is_api_test for t in tests)),
        test_class_analyses=[
            TestClassAnalysis(
                qualified_class_name=class_name, test_method_analyses=tests
            )
        ],
    )


def write_model(input_root: Path, analysis: ProjectAnalysis) -> Path:
    path = input_root / analysis.dataset_name / "gerbil.json"
    path.parent.mkdir(parents=True)
    path.write_text(analysis.model_dump_json(), encoding="utf-8")
    return path
