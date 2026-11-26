# Gerbil

Gerbil characterizes Java HTTP API tests through static analysis. On top of
[CLDK](https://github.com/codellm-devkit/python-sdk)'s Java symbol table, it finds each
test's API invocation steps (request builders, dispatch and response checks) and the
application endpoints they reach. It then labels every test along several axes:
testing and HTTP framework, request dispatch and construction, assertions,
authentication, state and fixtures. Finally it aggregates the labels into statistics
over a corpus.

The repository also holds the paper's comparison of developer-written API tests with the
tests that Claude Code, Codex and EvoMaster generated for ten open-source projects,
including the coverage and mutation scores of those tests.

## Setup

```bash
uv sync
uv run gerbil --help
```

- Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).
- For analysis: a JDK and Maven 3.9. CLDK's codeanalyzer fails to download some projects'
  dependencies under Maven 3.10.
- For coverage, mutation and EvoMaster: Docker.
- The analyzed projects are git submodules under `resources/`, pinned to the commits that
  were analyzed: `git submodule update --init resources/<project>`.
- Tests: `uv run pytest`. The integration tests run CLDK and Docker:
  `uv run pytest --run-integration`.

## Repository layout

```
src/gerbil/
  analysis/      static analysis of a project's tests, written as <project>/gerbil.json
  statistics/    statistics over analyses, suite comparison, subset selection and sampling
  figures/       figures from statistics directories
  validation/    sample, annotation page and scoring for the manual validation
  execution/     coverage (JaCoCo) and mutation (PIT) of API tests, in Docker
  evomaster/     EvoMaster runs, staging and export on the comparison subset
docker/
  execution/     execution image, test launcher and PIT mutant filter
  evomaster/     EvoMaster image and each target's harness (targets/<target>/)
scripts/         dataset, agent-run and SAINT extraction helpers; run_execution.sh
resources/       analyzed projects (git submodules at pinned commits)
tests/           unit tests; tests/integration needs --run-integration
docs/            details: statistics, validation, execution, EvoMaster, analysis output
outputs/         evaluation data (see Outputs)
```

## Comparison subset

The ten projects are taken from the corpus analysis (2,527 projects) by
`gerbil sample-projects`:

1. **Candidates.** A candidate project has at least one application endpoint and at least
   one API test whose request resolves to a method and path endpoint. Sixteen curated
   exclusions are left out: libraries and frameworks, and projects whose endpoint
   attribution is unreliable. They are listed with reasons in `EXCLUDED_DATASETS`
   (`src/gerbil/statistics/sampling.py`). That leaves 183 candidates.
2. **Ranking.** Candidates are ranked by API test count. Ties go to the higher total, and
   then higher maximum, HTTP sequence count.
3. **Selection.** The top ten are taken and written to
   `outputs/sample/interesting_projects.json`.

```bash
uv run gerbil sample-projects --input-root <corpus-analyses> --output-dir outputs/sample --count 10
```

| Rank | Project | Repository | API tests | Endpoints | EvoMaster service |
| --- | --- | --- | --- | --- | --- |
| 1 | DSpace | DSpace/DSpace | 3,134 | 178 | REST webapp |
| 2 | uaa | cloudfoundry/uaa | 1,389 | 167 | UAA server |
| 3 | kylin | apache/kylin | 567 | 553 | REST server |
| 4 | spring-cloud-dataflow | spring-attic/spring-cloud-dataflow | 385 | 121 | Data Flow server |
| 5 | incubator-kie-kogito-runtimes | apache/incubator-kie-kogito-runtimes | 334 | 72 | Spring Boot processes integration-test app |
| 6 | spinnaker | spinnaker/spinnaker | 239 | 558 | Clouddriver |
| 7 | openvsx | eclipse/openvsx | 226 | 67 | Server |
| 8 | cbioportal | cBioPortal/cbioportal | 218 | 217 | none |
| 9 | dolphinscheduler | apache/dolphinscheduler | 204 | 298 | Standalone server |
| 10 | hertzbeat | apache/hertzbeat | 184 | 179 | Manager |

- The projects' build settings are in `src/gerbil/execution/projects.py`.
- EvoMaster has no 1-hour cbioportal suite, because the search ran out of memory. The
  EvoMaster comparison and the coverage and mutation measurements therefore cover the
  other nine projects.
- `gerbil subset-selection` checks the ten against the 144 other corpus projects with at
  least 10 API tests and 1 endpoint, row by row on the comparison table. It uses a
  two-sided Mann-Whitney U test with Cliff's delta, Holm-adjusted, and writes
  `outputs/stats/subset_selection/subset_selection.json`. See
  [docs/statistics.md](docs/statistics.md).

## Agent baselines

Each agent started from the project's pinned commit with its tests cleared, and ran with
its own system prompt.

| Agent | Model | Prompt template | Prompt per project |
| --- | --- | --- | --- |
| Claude Code | `claude-sonnet-4-6` | `outputs/claude_code/prompt/user_prompt.jinja2` | `outputs/claude_code/runs/<project>/prompt/user_prompt.md` |
| Codex | `gpt-6-luna` | `outputs/codex/prompt/user_prompt.jinja2` | `outputs/codex/runs/<project>/prompt/user_prompt.md` |

- Each run directory also holds the trajectory (`trajectories/messages.jsonl`) and the
  tests the agent wrote (`test_patches/git_diff.patch`, against the cleared commit).
- On the three monorepos (Spinnaker, Kogito, Data Flow), both prompts add "You must at
  least test the …" for the service EvoMaster runs.

## EvoMaster

- **Configuration:** `outputs/evomaster/settings.md`. It gives the version, the budget,
  the seed, every option and why it is set, and the per-service options and schema
  exclusions.
- **Harness per service:** `docker/evomaster/targets/<target>/`.
  - `files/` holds the `EmbeddedEvoMasterController` driver.
  - `build.patch` adds the test dependencies.
- **Target settings:** `src/gerbil/evomaster/targets.py` (module, schema exclusions,
  search arguments).
- **Image and container scripts:** `docker/evomaster/`.
- **Generated suites:** each project's `outputs/evomaster/runs/<project>/` holds four files.
  - `test_patches/git_diff.patch` turns the agents' starting point into the analyzed tree.
  - `run.json` is Gerbil's record of the run.
  - `statistics.csv` is EvoMaster's own statistics of the final suite.
  - `snapshot.csv` is the same statistics, taken every 5% of the search budget.
- **Running, staging and exporting:** [docs/evomaster.md](docs/evomaster.md).

## Coverage and mutation

The developer, Claude Code, Codex and EvoMaster suites of the nine measured projects run
in Docker under JaCoCo and PIT. Results are measured over the classes of the service
EvoMaster runs.

- Each project is scored on a uniform sample of 600 of the service's mutants, the same
  sample for all four suites. The settings the run pinned are in `outputs/execution/.run/`.
- Inputs beyond a checkout are in `outputs/execution-bundle/`, with digests pinned in
  `manifest.json`.
- The method, kill rules and commands are in [docs/execution.md](docs/execution.md).

## Outputs

| Path | Contents |
| --- | --- |
| `outputs/dataset/` | `hamster.json`, the 2,527 corpus projects; `gerbil.json`, the 350 with at least one API test |
| `outputs/sample/` | `interesting_projects.json`, the comparison subset's selection |
| `outputs/claude_code/`, `outputs/codex/` | Prompt templates and each project's run: prompt, trajectory, test patch |
| `outputs/evomaster/` | `settings.md`, and each project's suite (`runs/<project>/`) |
| `outputs/stats/` | Statistics, one directory per suite (below), plus `suite_comparison/`, `subset_selection/` and `test_structure/` |
| `outputs/validation/` | Validation sample, Gerbil's models, the annotation page and its bundle |
| `outputs/execution-bundle/` | Each project's test clearing, each suite's API tests and `manifest.json` |
| `outputs/execution/` | Coverage and mutation results: `<project>/summary.json` and `mutants.json`, and per suite `tests.json`, `coverage.json` and `mutation.json` |

| Statistics directory | Tests |
| --- | --- |
| `outputs/stats/dev_full/` | Developer-written, full corpus |
| `outputs/stats/dev_subset/` | Developer-written, comparison subset |
| `outputs/stats/claude_code/` | Claude Code |
| `outputs/stats/codex/` | Codex |
| `outputs/stats/evomaster/` | EvoMaster, nine services |
| `outputs/stats/saint/` | SAINT, with the `--saint-comparison` views |

## Commands

```bash
# Analyze one project, or every project under a directory
uv run gerbil analysis --project-path <project> --analysis-path <cldk-cache> --output-path <out>
uv run gerbil batch-analysis --input-root <projects> --analysis-root <cldk-caches> --output-root <out>

# Statistics for one suite, and the comparisons
uv run gerbil statistics --input-root <analyses> --output-dir outputs/stats/dev_subset
uv run gerbil suite-comparison --baseline developer=<dev-analyses> \
  --suite claude_code=<claude-code-analyses> --suite codex=<codex-analyses> \
  --suite evomaster=<evomaster-analyses> --suite saint=<saint-analyses> \
  --output-dir outputs/stats/suite_comparison
uv run gerbil subset-selection --input-root <corpus-analyses> --output-dir outputs/stats/subset_selection

# Validation, coverage and mutation, EvoMaster: see docs/
uv run gerbil validation --help
uv run gerbil execution --help
uv run gerbil evomaster --help
```

If `uv run gerbil` fails with `ModuleNotFoundError`, run `make clean`, which rebuilds the
virtual environment.
