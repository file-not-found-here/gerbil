# Coverage and mutation of API tests

`gerbil execution` runs API tests under JaCoCo and PIT, in the `gerbil-execution` Docker
image (`docker/execution/`).
- **Suites:** developer, Claude Code, Codex and EvoMaster.
- **Projects:** the nine measured comparison projects (`MEASURED_PROJECTS` in
  `src/gerbil/execution/projects.py`). cbioportal has no EvoMaster suite.

## Scope

- **Tests.**
  - A developer or agent test is in scope when every request Gerbil found is dispatched
    in-process or to localhost.
  - Every EvoMaster test is in scope.
  - Generated tests run offline, with the target's JVM flags.
- **Code.** Coverage and mutation count the production classes of the service EvoMaster
  runs, for every suite.
  - These classes come from the target module and from every module of its build that
    its production code compiles against or runs with. In Maven that means the compile,
    provided and runtime scopes; in Gradle, the main runtime classpath.
  - **Monorepos:**
    - Spinnaker: Clouddriver's own modules.
    - Kogito: the process application and the Kogito runtime it runs on.
    - Data Flow: the Data Flow server, but not Skipper's server.
  - Classes the build generates count too.
- **Trees.**
  - The developer suite runs on the pinned commit.
  - The other suites run on that commit with the agents' test clearing and the suite's
    committed patch applied.

## Inputs

- **Source repositories:** `resources/`.
- **Committed patches:**
  `outputs/{claude_code,codex,evomaster}/runs/<project>/test_patches/git_diff.patch`.
- **`outputs/execution-bundle/`**, which `gerbil execution bundle` writes:
  - For each project, the test clearing (the files deleted from the pinned commit) and
    each suite's API tests.
  - `maven-seed/`: artifacts that no remote serves any more, which are copied into
    Maven's cache before each build. These are Kogito's purged Apache snapshots and
    Kylin's Kyligence artifacts. The seed is not in git; `gerbil execution seed`
    exports it from the `gerbil-m2` cache.
  - `manifest.json`: the SHA-256 of every file above, of the seed and of each committed
    patch.

## Measurement

1. **Coverage.** `coverage` compiles each suite's test modules and runs the selected
   tests from compiled classes, in one JVM per module. It measures JaCoCo coverage of
   the service by the tests that pass.
2. **Mutant sample.** `mutation` lists every mutant that PIT's default mutators make of
   the service. It then draws a uniform sample, ranked by the SHA-256 of the seed and
   each mutant's id (`--sample-size`, `--sample-seed`). Every suite gets the same sample.
   The paper's results use 600 mutants per project (`outputs/execution/.run/sample`).
3. **PIT runs.** PIT runs the full mutation matrix on the sampled mutants that each
   suite covers. Kills are credited only to selected tests.
4. **Settings.** Tests and PIT workers run with a 16 GiB heap. PIT's `--timeout` is
   added to each test's baseline time; the paper's run used 180 s.

Reading the mutation results:
- `TIMED_OUT` and `MEMORY_ERROR` count as killed.
- **`RUN_ERROR`.** Some mutants are reached only by unselected tests or by a test
  class's set-up; for EvoMaster, that set-up starts the service. PIT gives such a mutant
  `RUN_ERROR` without running a test, and it counts as neither covered nor killed.
- **Tests that need Docker.** Containers get no Docker, so tests that start a service
  through Testcontainers fail their baseline. For example, 46 of Spinnaker's 75
  in-scope developer tests fail this way.
- **Coverage gaps.** `summary.json` reports three counts for each suite:
  - `line_covered`: the sampled mutants on lines that its passing tests ran.
  - `untested_line_covered`: how many of those PIT challenged with no selected test.
  - `unreached`: covered mutants that PIT could not mutate for the suite. They count as
    not covered.

## Results

Each project's results are in `<output-dir>/<project>/`:

| File | Contents |
| --- | --- |
| `summary.json` | For each suite: coverage counters, the mutation score over the sampled mutants (less non-viable ones), test strength over the covered ones, and kills by suite set |
| `mutants.json` | The service's mutants and the sample |
| `<suite>/tests.json` | Each API test's baseline status, or why it did not run. A test whose source is outside the build's test sources is `not-in-build` |
| `<suite>/coverage.json` | JaCoCo coverage and the heap it ran with |
| `<suite>/mutation.json` | For each mutant, its status and its killing and surviving tests; for each PIT run, its duration and peak memory |

A suite challenged with a different sample is not scored. A larger sample holds every
smaller one, so results can move between sample sizes:
- **Shrinking.** `gerbil execution resample --from-size <M> --sample-size <N>` rescores
  results on the first N mutants of a larger sample.
- **Growing.** `gerbil execution mutation --extend-from <N> --sample-size <M>` grows
  results on N mutants to M. PIT challenges the suite only with the rest of the sample.
  The earlier results stay as `mutation-<N>.json`. Nothing checks that both runs used
  the same PIT settings.

## Running

`--output-dir` (default `outputs/execution`) must be case-sensitive inside the
containers. On macOS, attach a case-sensitive image under the repository, since Colima
shares only the home folder:

```bash
hdiutil create -size 200g -type SPARSE -fs "Case-sensitive APFS" -volname gerbil-execution outputs/gerbil-execution.sparseimage
mkdir -p outputs/execution-cs
hdiutil attach outputs/gerbil-execution.sparseimage -mountpoint "$PWD/outputs/execution-cs"
OUT=outputs/execution-cs/execution
```

Give Docker a little over 18 GiB of memory for each JVM that runs at once. That is one
JVM in `coverage`, and `--workers` JVMs in `mutation`.

One project:

```bash
uv run gerbil execution check
uv run gerbil execution coverage --project hertzbeat --output-dir "$OUT"
uv run gerbil execution mutation --project hertzbeat --output-dir "$OUT" --sample-size 600 --timeout 180 --threads 4 --workers 8
```

Every project, in the background:

```bash
scripts/run_execution.sh check
SAMPLE=600 scripts/run_execution.sh start   # or: start coverage, start mutation, start mutation uaa kylin
scripts/run_execution.sh status
scripts/run_execution.sh stop
```

- **Resuming.** `start` picks up from the result files. It runs coverage for suites
  without `coverage.json`, and mutation for suites without `mutation.json`.
- **Re-measuring.** Re-running `coverage` discards that suite's mutation results.
- **Settings.** The script reads them from environment variables: `OUT`, `BUNDLE`,
  `WORKERS`, `THREADS`, `JOBS`, `SAMPLE`, `PIT_TIMEOUT`, `RETRIES`, `MIN_FREE_GB`,
  `PAUSE_BELOW_GB`, `COVERAGE_CAP` and `MUTATION_CAP`. The first run fixes `SAMPLE` and
  `PIT_TIMEOUT` for its `OUT`.
- **Moving between machines.** `scripts/run_execution.sh bundle` and `results` carry
  the inputs and the results between machines as zip parts.

## Test structure and mutants

`gerbil execution structure` splits a suite's passing tests by traits from the comparison
table:
- two or more steps, and five or more
- a body check, against a status-only check
- a step with two or more body checks
- a re-dispatched endpoint
- a read after a write
- a request from a fixture

For each trait, it compares the two groups' kill rates on the mutants that tests of both
groups run.

```bash
uv run gerbil execution structure --suite developer --analysis-root <dev-analyses> \
  --output-dir outputs/execution --statistics-dir outputs/stats/test_structure
```

- **Two comparisons.** `shared_mutants` uses the mutants that tests of both groups run.
  `same_test_class` uses the mutants that both groups' tests run within one test class.
- **Statistics.**
  - Intervals and p-values take the mutated class as their unit: a class bootstrap and a
    sign-flip test.
  - The p-values are Holm-adjusted over the comparisons of one set of tests.
  - Pooled entries also give the difference with each project left out
    (`mean_difference_without`).
- **Observational.** The comparison is observational, not causal.
  - A step is any request a test issues, including those its harness makes. For
    example, every DSpace request is preceded by a CSRF token fetch.
- **Left out:**
  - `unmatched_tests`: passing tests with no API test in the analysis, or that share a
    class and method name with another test.
  - `stopped_mutants`: mutants whose PIT worker was stopped.
