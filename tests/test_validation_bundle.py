from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gerbil.analysis.schema import (
    HttpCallSite,
    HttpDispatchFramework,
    HttpRequestRole,
    HttpTestSequence,
)
from gerbil.cli import app
from gerbil.validation.bundle import (
    BUNDLE_FILE,
    BundleError,
    _builder_parts,
    build_bundle,
    build_test_entry,
    read_bundle,
    write_bundle,
)
from gerbil.validation.java_source import JavaRepo
from gerbil.validation.page import PAGE_FILE, render_page
from gerbil.validation.sampling import (
    SAMPLE_FILE,
    TestCandidate,
    build_sample_payload,
    draw_sample,
    project_tests,
)
from tests.validation_builders import (
    BASE_CLASS,
    api_test,
    project,
    widget_test,
    write_model,
    write_repo,
)


def _entry(repo_root: Path) -> dict[str, Any]:
    analysis = project("demo", [widget_test()])
    ((candidate, _),) = project_tests(analysis)
    return build_test_entry(candidate, widget_test(), JavaRepo(repo_root))


def _item(entry: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(item for item in entry["items"] if item["id"] == item_id)


def test_units_are_the_test_its_fixtures_and_the_helpers_it_reaches(
    tmp_path: Path,
) -> None:
    entry = _entry(write_repo(tmp_path))

    assert [
        (unit["role"], unit["signature"], unit["has_items"], unit["start_line"])
        for unit in entry["units"]
    ] == [
        ("test", "createsWidget()", True, 6),
        ("setup", "seed()", False, 6),
        ("helper", "fetch(java.lang.String)", True, 14),
        # Reached from the test body and declared in the superclass; the model
        # extracted nothing from it.
        ("helper", "cleanup()", False, 11),
    ]
    assert (
        entry["units"][0]["lines"][1] == "    void createsWidget() throws Exception {"
    )
    assert entry["units"][3]["file"] == "src/test/java/demo/BaseTest.java"
    assert entry["missing_units"] == []


def test_items_carry_their_step_their_claims_and_their_token(tmp_path: Path) -> None:
    entry = _entry(write_repo(tmp_path))

    assert [(item["kind"], item["step"], item["unit"]) for item in entry["items"]] == [
        ("builder", 1, "u0"),
        ("builder", 1, "u0"),
        ("dispatch", 1, "u0"),
        ("check", 1, "u0"),
        ("builder", 2, "u2"),
        ("dispatch", 2, "u2"),
        ("check", 2, "u2"),
    ]
    assert [_item(entry, name)["parts"] for name in ("i1", "i2", "i5")] == [
        ["method", "path"],
        ["header/auth"],
        ["method"],
    ]
    dispatch = _item(entry, "i3")
    assert (dispatch["http_method"], dispatch["path"]) == ("POST", "/widgets")
    assert set(dispatch) >= {"http_method", "path", "path_truncated"}
    assert not set(dispatch) & {"header_names", "has_body_payload"}
    # The helper's request has no path the model could resolve.
    assert _item(entry, "i6")["path"] is None
    check = _item(entry, "i4")
    assert (check["target"], check["status_code"]) == ("status", 201)

    for item in entry["items"]:
        unit = entry["units"][int(item["unit"][1:])]
        token = item["token"]
        line = unit["lines"][token["line"] - unit["start_line"]]
        assert line[token["start"] : token["end"]] == item["method_name"]


def test_a_call_the_model_kept_out_of_every_step_has_no_step(tmp_path: Path) -> None:
    test = widget_test()
    test.http.test_sequences.pop()
    analysis = project("demo", [test])
    ((candidate, _),) = project_tests(analysis)

    entry = build_test_entry(candidate, test, JavaRepo(write_repo(tmp_path)))

    assert [item["step"] for item in entry["items"]] == [1, 1, 1, 1, None, None, None]
    assert _item(entry, "i5")["parts"] == ["method"]


def test_a_builder_sets_the_parts_its_own_record_carries() -> None:
    def builder(**fields: Any) -> HttpCallSite:
        record: dict[str, Any] = {
            "http_method": "UNKNOWN",
            "path": "",
            "framework": HttpDispatchFramework.MOCKMVC,
            "request_role": HttpRequestRole.BUILDER,
            "method_name": "param",
        }
        return HttpCallSite(**{**record, **fields})

    assert _builder_parts(builder()) == []
    assert _builder_parts(builder(query_param_names=["page"])) == ["parameter"]
    assert _builder_parts(builder(form_param_names=["name"])) == ["parameter"]
    assert _builder_parts(builder(has_body_payload=True)) == ["body"]
    assert _builder_parts(
        builder(header_names=["authorization"], auth_hints=["Authorization"])
    ) == ["header/auth"]
    # Credentials sent another way are the same part, as the paper counts them.
    assert _builder_parts(builder(auth_hints=["basic"])) == ["header/auth"]
    assert _builder_parts(builder(http_method="GET", path="/x")) == ["method", "path"]


def test_same_named_builders_of_one_chain_each_keep_their_own_parts(
    tmp_path: Path,
) -> None:
    # Two `.header(..)` calls of one chain: only the second sets a resolved name.
    # The dispatch's correlation sources could not tell them apart, so the first,
    # whose own record is empty, takes the part the dispatch credits to the name.
    test = widget_test()
    post, header, perform = test.http.call_sequence[:3]
    second = header.model_copy(update={"order": 3})
    perform = perform.model_copy(update={"order": 4})
    check = test.http.call_sequence[3].model_copy(update={"order": 5})
    test.http.call_sequence = [post, header, second, perform, check]
    test.http.test_sequences = [
        HttpTestSequence(
            order=1, steps=[post, header, second, perform, check], fingerprint="a"
        )
    ]
    calls = test.http.request_interactions
    unnamed = calls[1].model_copy(deep=True)
    assert unnamed.http_call is not None
    unnamed.http_call.header_names = []
    test.http.request_interactions = [calls[0], unnamed, calls[1], calls[2]]
    ((candidate, _),) = project_tests(project("demo", [test]))

    entry = build_test_entry(candidate, test, JavaRepo(write_repo(tmp_path)))

    assert [
        (item["id"], item["parts"])
        for item in entry["items"]
        if item["kind"] == "builder"
    ] == [
        ("i1", ["method", "path"]),
        ("i2", ["header/auth"]),
        ("i3", ["header/auth"]),
    ]


def test_a_builder_that_sets_nothing_is_left_off_the_page(tmp_path: Path) -> None:
    # Chain calls such as `given()` or `when()` set no part of the request, and the
    # request-construction statistics never count them.
    test = widget_test()
    calls = test.http.request_interactions
    bare = calls[1].model_copy(deep=True)
    assert bare.http_call is not None
    bare.http_call.header_names = []
    bare.http_call.headers = []
    perform = calls[2].model_copy(deep=True)
    assert perform.http_call is not None
    perform.http_call.correlated_builder_sources = (
        perform.http_call.correlated_builder_sources[:1]
    )
    test.http.request_interactions = [calls[0], bare, perform, *calls[3:]]
    ((candidate, _),) = project_tests(project("demo", [test]))

    repo = JavaRepo(write_repo(tmp_path))
    entry = build_test_entry(candidate, test, repo)

    assert [item["id"] for item in entry["items"]] == [
        "i1",
        "i3",
        "i4",
        "i5",
        "i6",
        "i7",
    ]
    # The code shown does not change with what is asked about it.
    ((full, _),) = project_tests(project("demo", [widget_test()]))
    assert entry["units"] == build_test_entry(full, widget_test(), repo)["units"]


def test_a_builder_credited_only_on_its_dispatch_shows_that_part(
    tmp_path: Path,
) -> None:
    # REST Assured `param(..)` keeps its parameters on the dispatch alone until the
    # verb decides query or form; its own record is empty.
    test = widget_test()
    calls = test.http.request_interactions
    param = calls[1].model_copy(deep=True)
    assert param.http_call is not None
    param.http_call.header_names = []
    perform = calls[2].model_copy(deep=True)
    assert perform.http_call is not None
    perform.http_call.correlated_builder_sources[1].contributed_properties = [
        "rest_assured_ambiguous_param_names"
    ]
    test.http.request_interactions = [calls[0], param, perform, *calls[3:]]
    ((candidate, _),) = project_tests(project("demo", [test]))

    entry = build_test_entry(candidate, test, JavaRepo(write_repo(tmp_path)))

    assert _item(entry, "i2")["parts"] == ["parameter"]


def test_helpers_are_followed_two_calls_deep_and_capped(tmp_path: Path) -> None:
    repo_root = write_repo(tmp_path)
    source = repo_root / "src/test/java/demo/WidgetTest.java"
    original = source.read_text()

    def rewrite(calls: str, methods: str) -> list[dict[str, Any]]:
        # Calls go on an existing line and methods after the last one, so no
        # line the model points at moves.
        text = original.replace("cleanup();", f"cleanup(); {calls}", 1)
        source.write_text(text[: text.rindex("}")] + methods + "}\n")
        units: list[dict[str, Any]] = _entry(repo_root)["units"]
        return units

    chained = rewrite(
        "a();", "    void a() { b(); }\n    void b() { c(); }\n    void c() { }\n"
    )

    reached = [unit["signature"] for unit in chained]
    assert reached[-3:] == ["cleanup()", "a()", "b()"] and "c()" not in reached

    many = rewrite(
        " ".join(f"h{n}();" for n in range(9)),
        "".join(f"    void h{n}() {{ }}\n" for n in range(9)),
    )

    # seed() is a fixture; of the ten helpers reached, eight are shown.
    assert [unit["signature"] for unit in many if not unit["has_items"]] == [
        "seed()",
        "cleanup()",
        *[f"h{n}()" for n in range(7)],
    ]


def test_a_helper_without_source_is_reported_and_its_items_stay(tmp_path: Path) -> None:
    repo_root = write_repo(tmp_path)
    (repo_root / "src/test/java/demo/BaseTest.java").unlink()

    entry = _entry(repo_root)

    assert entry["missing_units"] == [
        {"role": "setup", "class": BASE_CLASS, "signature": "seed()"}
    ]
    assert len(entry["items"]) == 7


def test_a_test_without_source_fails_the_bundle(tmp_path: Path) -> None:
    repo_root = write_repo(tmp_path)
    (repo_root / "src/test/java/demo/WidgetTest.java").unlink()

    with pytest.raises(BundleError, match="no source for demo.WidgetTest"):
        _entry(repo_root)


def _sample(
    checkout: str = "demo",
) -> tuple[dict[str, Any], list[TestCandidate], dict[str, Any]]:
    analysis = project(
        "demo", [widget_test()], project_path=f"/data/someone/repos/{checkout}"
    )
    tests = project_tests(analysis)
    candidates = [candidate for candidate, _ in tests]
    sample = draw_sample(candidates, size=2, per_project_cap=5, seed="seed")
    payload = build_sample_payload(
        candidates, sample, size=2, per_project_cap=5, seed="seed", excluded_datasets=[]
    )
    return payload, sample, {candidate.test_id: test for candidate, test in tests}


def test_bundle_reads_each_checkout_under_the_repos_root(tmp_path: Path) -> None:
    write_repo(tmp_path / "checkouts" / "demo")
    payload, sample, models = _sample()

    bundle = build_bundle(payload, sample, models, tmp_path / "checkouts")

    assert bundle["sample_digest"] == payload["sample"]["digest"]
    assert [
        (test["id"], test["step_count"], test["has_helper_or_fixture_http"])
        for test in bundle["tests"]
    ] == [("demo::demo.WidgetTest::createsWidget()", 2, True)]

    payload, sample, models = _sample(checkout="elsewhere")
    with pytest.raises(BundleError, match="no checkout named elsewhere"):
        build_bundle(payload, sample, models, tmp_path / "checkouts")


def test_bundle_file_round_trips_and_names_no_file_system(tmp_path: Path) -> None:
    write_repo(tmp_path / "checkouts" / "demo")
    payload, sample, models = _sample()
    bundle = build_bundle(payload, sample, models, tmp_path / "checkouts")

    write_bundle(bundle, tmp_path / "validation")

    assert read_bundle(tmp_path / "validation") == bundle
    written = (tmp_path / "validation" / BUNDLE_FILE).read_text()
    assert str(tmp_path) not in written and "/data/someone" not in written
    # Source files are named inside their repository only.
    assert (
        bundle["tests"][0]["units"][0]["file"] == "src/test/java/demo/WidgetTest.java"
    )


def test_page_embeds_the_bundle_without_ending_its_script_early(
    tmp_path: Path,
) -> None:
    write_repo(tmp_path / "demo")
    payload, sample, models = _sample()
    bundle = build_bundle(payload, sample, models, tmp_path)
    bundle["tests"][0]["units"][0]["lines"].append('String html = "</script>";')

    page = render_page(bundle)

    assert "__GERBIL_BUNDLE_JSON__" not in page
    embedded = page.split('<script id="bundle" type="application/json">', 1)[1]
    data, rest = embedded.split("</script>", 1)
    assert json.loads(data) == bundle
    assert "Annotation guide" in page and "render(false);" in rest


def test_page_embed_survives_markup_that_would_derail_a_script_element(
    tmp_path: Path,
) -> None:
    write_repo(tmp_path / "demo")
    payload, sample, models = _sample()
    bundle = build_bundle(payload, sample, models, tmp_path)
    bundle["tests"][0]["units"][0]["lines"].append('html = "<!-- <script>";')

    embedded = render_page(bundle).split('type="application/json">', 1)[1]
    data = embedded.split("</script>", 1)[0]

    assert "<" not in data
    assert json.loads(data) == bundle


def test_page_exports_no_name_timestamp_or_free_text_identity() -> None:
    page = render_page({"tests": []})

    assert '<select id="annotator">' in page and '"author1"' in page
    assert "Date" not in page and "exported_at" not in page


def test_cli_draws_a_sample_and_builds_its_page(tmp_path: Path) -> None:
    write_repo(tmp_path / "checkouts" / "demo")
    input_root = tmp_path / "models"
    write_model(
        input_root,
        project(
            "demo",
            [widget_test(), api_test("probe()", 1)],
            project_path="/data/someone/repos/demo",
        ),
    )
    validation_dir = tmp_path / "validation"
    runner = CliRunner()

    # Only the widget test is drawn: the cap of one leaves the probe out.
    drawn = runner.invoke(
        app,
        [
            "validation",
            "sample",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(validation_dir),
            "--size",
            "2",
            "--per-project-cap",
            "1",
            "--seed",
            "seed-2",
            "--jobs",
            "1",
        ],
    )
    sampled = json.loads((validation_dir / SAMPLE_FILE).read_text())

    # A sample short of its size is written and reported as an error.
    assert drawn.exit_code == 1 and "drew 1 of 2 tests" in drawn.output
    assert [test["method_signature"] for test in sampled["tests"]] == [
        "createsWidget()"
    ]

    # The page is built from the sample directory alone: the models are gone.
    (input_root / "demo" / "gerbil.json").unlink()
    built = runner.invoke(
        app,
        [
            "validation",
            "page",
            "--sample-dir",
            str(validation_dir),
            "--repos-root",
            str(tmp_path / "checkouts"),
        ],
    )

    assert built.exit_code == 0, built.output
    assert '"items": 7' in built.output
    # What the commands print can end up in a log or a paper's artifact too.
    for printed in (drawn.output, built.output):
        assert str(tmp_path) not in printed
    assert json.loads(built.output)["files"] == [PAGE_FILE, BUNDLE_FILE]
    assert "createsWidget" in (validation_dir / PAGE_FILE).read_text()
    assert read_bundle(validation_dir)["tests"][0]["method"] == "createsWidget()"
    for written in validation_dir.iterdir():
        text = written.read_text()
        assert str(tmp_path) not in text and "/data/someone" not in text


def test_cli_rejects_an_excluded_dataset_that_has_no_api_tests(tmp_path: Path) -> None:
    input_root = tmp_path / "models"
    write_model(input_root, project("demo", [api_test("probe()", 1)]))

    result = CliRunner().invoke(
        app,
        [
            "validation",
            "sample",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "validation"),
            "--exclude-dataset",
            "typo",
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code != 0 and "typo" in result.output
