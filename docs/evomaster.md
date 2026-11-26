# EvoMaster on the comparison subset

`gerbil evomaster` runs EvoMaster 6.2.0 in white-box mode on the services of the
comparison subset (`src/gerbil/evomaster/targets.py`). Each service gets an embedded
driver in the style of the EvoMaster Benchmark (EMB). The options and the reason for
each are in `outputs/evomaster/settings.md`.

## Harness

Each target's harness is in `docker/evomaster/targets/<target>/`.

- **`files/`** adds an `EmbeddedEvoMasterController` to the test sources of the module
  whose test classpath runs the service.
  - The driver starts the service in the test JVM, and declares its OpenAPI schema, its
    logins and how to reset its state.
  - Each driver's class comment states what it runs.
- **`build.patch`** adds dependencies to that module's tests: EvoMaster's client and
  REST Assured for every target, springdoc for UAA and DSpace, and WireMock for Data
  Flow.

The generated suite therefore runs from the module's test classpath, like the
developers' suite.

| Target | Module | Service as the driver runs it | Reset before each test | Logins |
| --- | --- | --- | --- | --- |
| cbioportal | `.` | Portal without authentication on H2 (MySQL mode), with the developers' H2 schema and full-app seed data | Clear all tables, reload the seed | none |
| clouddriver | `clouddriver/clouddriver-web` | Spinnaker's Clouddriver with Redis and SQL off, as its `MainSpec` runs it; no cloud provider | Clear the in-memory task history | none (Fiat off) |
| dolphinscheduler | `dolphinscheduler-standalone-server` | Standalone API, master, worker and alert servers on H2 with the JDBC registry | Clear Quartz's jobs, then all but the scheduler, registry and plugin tables, and reload the seed | `admin`, session cookie |
| dspace | `dspace-server-webapp` | REST webapp booted as its integration tests boot it: the test installation, H2, and the developers' embedded Solr and service doubles; background schedules off | Restore the tables and sequences changed since seeding, evict Hibernate's caches, empty the Solr cores | `admin`, `eperson`, JWT, with the CSRF token pair a client sends |
| hertzbeat | `hertzbeat-manager` | Manager with its embedded alerter, warehouse and collector, with the developers' `test` profile on H2 | Cancel collection jobs, restore the rows written at startup | `admin`, `tom`, `guest`, JWT |
| kogito-springboot | `springboot/integration-tests/integration-tests-springboot-processes-it` | Kogito's process integration-test application | Abort process instances, clear user tasks | none |
| kylin | `src/server` | Kylin 5's REST server in dev mode under its `/kylin` context path: file metadata from its example data, H2 job and query stores, local Spark, a local lock instead of ZooKeeper; schema merged from springfox's internal, v4 public and v2 public groups (Swagger 2) | Restore the H2 stores once no job runs, then the metadata changed since startup | `ADMIN`, HTTP Basic |
| openvsx | `server` (build root) | Registry with its test configuration on a throwaway PostgreSQL cluster, database search | Clear all but Flyway, ShedLock, migration and JobRunr tables, restore the seeded admin, its token and `test_user`, clear caches | `super_user` (admin) and `test_user` through a test-only header; tokens as query parameters |
| spring-cloud-dataflow | `spring-cloud-dataflow-server` | Data Flow server on H2, as its `DefaultLocalTests` run it; Skipper does not run | Clear all tables but Flyway's | none (security off) |
| uaa | `uaa` | Boot application with the developers' MockMvc test configuration under their `postgresql` profile, on a throwaway PostgreSQL cluster | Restore the rows and sequences the startup bootstraps wrote | `admin` client, `marissa`, OAuth tokens |

- **State reset.** Drivers restore the state a service holds after it starts: database
  rows and in-memory state such as scheduled jobs. EvoMaster's smart cleaning is off.
- **No network.** Generation, verification and `gerbil execution` run without network
  access.
- **openvsx login.** openvsx users log in only through GitHub and eclipse.org. Its
  driver therefore logs in a request carrying `X-EvoMaster-Login` as the seeded account
  it names. It is a test-only filter ahead of Spring Security.

## Stages

A run records its stages in `seed-<n>/run.json`.

1. **prepare** builds the tree that the suite is generated, staged and measured on: the
   pinned commit with the agents' test clearing, the cleared paths the build and driver
   need, and the harness.
2. **build** compiles the module's test classpath. It is the only stage with network
   access.
3. **schema** starts the driver offline, then fetches and merges the service's OpenAPI
   documents. It drops wildcard paths and the excluded operations.
4. **generate** runs the driver and EvoMaster's search offline, on that schema.
5. **verify** compiles the suite and runs every test twice, reordering each class's
   tests the second time.
   - Tests whose outcome differs between the runs are unstable; `verify/outcomes.json`
     lists them.
   - EvoMaster leaves its SSRF callback server out of some classes. verify adds it where
     it is missing, marked `// gerbil:`, and keeps the originals in
     `generated-original/`.

## Running

```bash
uv run gerbil evomaster targets
uv run gerbil evomaster run --target hertzbeat --max-time 1h --output-dir "$OUT"
uv run gerbil evomaster summary --output-dir "$OUT"
uv run gerbil evomaster stage --staged-root <staged-repos> --output-dir "$OUT"
uv run gerbil evomaster export --export-root outputs/evomaster/runs --output-dir "$OUT" --max-time 1h
JAVA_TOOL_OPTIONS=-Xmx16g uv run gerbil batch-analysis --input-root <staged-repos> \
  --analysis-root <cldk-caches> --output-root <evomaster-analyses> --jobs 1
```

- **Output directory.** `--output-dir` must be case-sensitive inside the containers; see
  [execution.md](execution.md#running). `summary`, `stage` and `export` default to
  `outputs/execution-cs/evomaster-1h`.
- **Run options.** `--resume-from <stage>` reruns a recorded run from that stage.
  `--cpus` caps the search's CPU time.
- **stage** rebuilds the prepared tree and adds the suite, ready for analysis.
- **export** saves that tree as a diff from the agents' starting point. It saves it
  beside `run.json` and EvoMaster's `statistics.csv` and `snapshot.csv`.

## Limitations

- **cbioportal** has no 1-hour suite. Three searches ran out of memory, with 8 GB and
  16 GB heaps.
- **Single seed.** Each suite comes from one seed of a wall-clock search, so a rerun
  differs.
- **Unstable assertions.** EvoMaster asserts on values that change between runs, such
  as tokens and timestamps, so some generated tests fail.
- **Shadowed operations.** Kylin tells 18 of its 514 documented operations apart only by
  media type, so they are shadowed.
- **Slow searches.** Kylin's search evaluated few tests after about 6 minutes, and
  HertzBeat's after about 12, because of slow calls.
- **DSpace** resources mostly share generic `/api/{apiCategory}/{model}` operations.
- **SQL.** EvoMaster cannot parse much of cbioportal's SQL. DSpace and openvsx run
  without SQL heuristics.
- **Data Flow** SSRF assertions on `metadata-uri` come before the request that would
  satisfy them, so they fail.
- **Time limits.** Minimization hit its time limit for DolphinScheduler, Data Flow,
  HertzBeat and UAA. The security phase hit its limit for DolphinScheduler and UAA.
- **Clouddriver** runs without a cloud provider, so most operations answer with errors.
