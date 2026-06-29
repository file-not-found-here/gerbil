# Gerbil

Gerbil statically analyzes Java test suites to characterize HTTP API tests for research questions about isolation, oracle behavior, failure coverage, endpoint coverage, framework usage, auth handling, and test setup patterns.

## Quick start

```bash
uv sync
uv run gerbil analysis \
  --project-path /path/to/java/project \
  --analysis-path /tmp/analysis-cache \
  --output-path /tmp/gerbil-output \
  --expanded-helper-depth 1
```

Or use the provided shortcuts:

```bash
make sync
make test
make lint
make type-check
make format-check
make cli-help
```

The command writes `<output-path>/<dataset-name>/gerbil.json`.

### Troubleshooting

If `uv run gerbil` fails with `ModuleNotFoundError`, rebuild the venv:

```bash
make clean        # removes .venv and runs uv sync
```

Alternatively, use the module entrypoint directly:

```bash
uv run python -m gerbil analysis ...
```

## Outputs

The `outputs/` directory contains the artifacts from our evaluation:

- `outputs/claude_code/` — the Claude Code user prompt used for evaluation (`prompt/user_prompt.jinja2`), along with the agent trajectory and outputs for each run on each project (`runs/<project>/`).
- `outputs/dataset/` — the hamster and gerbil project datasets.
- `outputs/sample/` — the sample projects used during the comparison between Claude Code and developer-written tests.
- `outputs/stats/` — the statistics used from Claude Code and the developer-written tests on the gerbil dataset.

## Recent improvements

- Resource interaction analysis groups HTTP events by normalized resource path into ordered sequences and classifies tests with side-effect verification (mutation + read on the same resource).
- Performed a structural cleanup by splitting `common_analysis.py` into focused modules (`framework_inference`, `class_categorization`, `fixture_discovery`, `metrics_helpers`) and reorganizing constants into domain-grouped modules.
- Consolidated duplicated utilities, removed the dead `dataset_name` field, renamed `TestMethodAnalysisInfo` to `MethodAnalysisInfo`, and cleaned assertion analysis exports.
- Hardened classification behavior with 10 fixes, including external URL exclusion from internal coverage, framework-context-gated dependency realization, boundary-safe receiver prefix matching, relative URI endpoint handling, improved body/header/auth detection, sequence identity hardening, and new companion runtime/coverage labels.
- Added configurable test directory patterns via `--test-dirs`.
- Expanded unit test coverage from 351 to 497 tests with direct assertion-submodule tests, broader framework registry and CLI coverage, and smoke tests for new common modules.

## Notes

- Analysis uses CLDK's Java symbol table backend.
- Architecture boundaries for analysis layers are documented in `docs/analysis-architecture-boundaries.md`.
- Resource interaction analysis captures HTTP interaction sequences per resource, enabling side-effect verification classification.
- Results include all detected test methods and API-specific labels/signals.
- Method-level labels are runtime-based and incorporate setup, test, and teardown fixture evidence.
- `request_dispatch` classifies how HTTP requests are dispatched: `in-process`, `local-network`, `remote-network`, or `unknown`. REST Assured module receiver types (`mockmvc`, `webtestclient`) are detected as `in-process`; core REST Assured dispatches as real HTTP.
- Request-dispatch labels use a framework-first algorithm reading HTTP framework from EVENT nodes.
- Expanded helper depth is configurable with `--expanded-helper-depth` (default `1`).
- Test directory patterns are configurable with `--test-dirs`.
- `http.request_interactions` provides origin-tagged HTTP request call and endpoint evidence across setup/test/teardown contexts.
- Each HTTP request interaction includes an `HttpCallSite` (with `request_role`: `event` or `builder`) and/or an `EndpointCandidate`.
- `http.verification_interactions` provides origin-tagged HTTP response verification events for status/body/header checks.
- `http.http_interactions` provides a rich in-order stream of request and verification interactions.
- Labeling inputs use `request-event` calls unless a label explicitly needs builder hints (for example auth token setup).
- `http.call_sequence` and `http.resource_interaction_sequences` expose ordered request/check and resource-level interaction takeaways.
- `assertions.summary` provides assertion role counts across setup/test/teardown contexts.
- `http.request_dispatch.signals` includes the event frameworks and path-classification signals behind dispatch labels.
- `local_metrics` (`ncloc`, `cyclomatic_complexity`) are test-body-only metrics.
- `expanded_metrics` (`ncloc`, `cyclomatic_complexity`, `helper_method_count`, `helper_method_ncloc`) represent reachability-expanded metrics across setup, test, and teardown contexts.
- Post-processing is intentionally left to downstream research scripts.
