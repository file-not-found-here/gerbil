# resources/scripts

Reusable, data-driven tooling to build, launch, and health-gate the Java HTTP
services bundled under `resources/` for REST Assured / JAX-RS testing.

The consumer is the sibling `general-agent-eval` Docker runner: with
`--service <id>`, the harness brings the target service online inside the
container (via the `run-with-service.sh` shim) before the agent runs, so the
agent can exercise a live API. That integration is **implemented**; these
scripts are the reusable building block it calls.

## Layout

```
scripts/
├── services.json        # canonical registry (single source of truth)
├── setup-service.sh     # generic driver: build / run / up / wait / stop a service
├── run-with-service.sh  # container shim: `up` a service, then exec the agent command
├── lib/
│   ├── manifest.py       # reads services.json (CLI + importable load_services())
│   └── common.sh         # shared bash helpers (jdk select, artifact glob, readiness, health)
└── README.md
```

All per-service recipes live in `services.json`. The shell scripts contain no
hard-coded service knowledge — add or change a service by editing the manifest.

## Services

| Service | JDK | Style | Port | Base path | Health probe | Ext. deps |
|---|---|---|---|---|---|---|
| `features-service`  | 8  | JAX-RS              | 8080 | `/`          | `GET /products`                                  | – |
| `restcountries`     | 8  | JAX-RS              | 8080 | `/rest`      | `GET /rest/v2/all`                               | – |
| `languagetool`      | 8  | custom HTTP JSON    | 8081 | `/v2`        | `GET /v2/languages`                              | – |
| `sample.daytrader8` | 8  | JAX-RS (+JSF)       | 9080 | `/daytrader` | `GET /daytrader/jaxrs/sync/echoText?input=ok`    | – |
| `genome-nexus`      | 8  | Spring REST         | 8888 | `/`          | `GET /actuator/health`                           | **MongoDB** |
| `jpetstore-6`       | 17 | Servlet/JSP (HTML)  | 8080 | `/jpetstore` | `GET /jpetstore/`                                | – |
| `spring-petclinic`  | 17 | Spring MVC (HTML)   | 8080 | `/`          | `GET /actuator/health`                           | – |

`jpetstore-6` and `spring-petclinic` are mostly server-rendered web apps, not
JSON REST APIs (PetClinic's only JSON endpoint is `GET /vets`). They are still
HTTP-testable. Run `setup-service.sh info <service>` for the full per-service notes.

## Usage

```bash
./setup-service.sh list                       # list services + descriptions
./setup-service.sh info  features-service      # metadata, resolved base/health URLs
./setup-service.sh build features-service      # build only
./setup-service.sh up    features-service      # build + background run + health-gate; prints base URL
./setup-service.sh run   features-service      # foreground run (blocks; good as a container CMD)
./setup-service.sh wait  features-service      # poll health until ready
./setup-service.sh health features-service     # one-shot health check (exit 0/1)
./setup-service.sh stop  features-service      # stop a backgrounded `up`
```

`up` is the entry point the harness will use: it builds, starts the service in
its own process session (so cleanup also reaps JVMs forked by Cargo/Liberty),
waits until the health probe answers `2xx–4xx`, writes `pid`/`log`/`url` to the
state dir, and prints the base URL on success.

### Options

| Option | Env fallback | Meaning |
|---|---|---|
| `--repo PATH`     | `SERVICE_REPO`          | Service source dir. **Required for the container**, where the staged repo lives at `/workspace/input`. |
| `--port N`        | `PORT`                  | HTTP port override. |
| `--host HOST`     | `SERVICE_HOST`          | Host used to build health/base URLs (default `localhost`). |
| `--state-dir DIR` | `GERBIL_SERVICE_STATE_DIR` | Where `pid`/`log`/`url` are written (default `$TMPDIR/gerbil-services`). |
| `--timeout SEC`   | –                       | Health-gate timeout override. |
| `--no-build`      | –                       | `up`: skip build (assume already built). |
| `--no-wait`       | –                       | `up`: start in background but skip the health gate. |

### Where the source is resolved from

`--repo` → `SERVICE_REPO` → `resources/<subdir>` (only if it exists **and is
writable**) → `$PWD`. The writability check means a read-only `/app/resources`
mount is skipped, but the container integration should always pass `--repo`
explicitly (e.g. `--repo /workspace/input`).

## JDK selection (`JAVA_<n>_HOME` convention)

Each service declares the JDK it needs. `select_java` points `JAVA_HOME` at
`JAVA_<n>_HOME` when that env var is set (e.g. `JAVA_8_HOME`, `JAVA_17_HOME`),
otherwise it falls back to the active JDK and warns on a major-version mismatch.
The agent runtime image (`Dockerfile.agent-runtime`) provisions both Temurin 8
and Temurin 17 and exports `JAVA_8_HOME`/`JAVA_17_HOME` (default `JAVA_HOME`
stays 17), so each service builds and runs under the JDK it declares.

## pre_run dependencies

A service may declare a `pre_run` list in `services.json`: background steps
started in their own session (and TCP-readiness-gated) before the SUT launches.
`genome-nexus` uses this to start a bundled `mongod` on an empty database — the
same way EMB runs it (Testcontainers `mongo:3.6.2`, never seeded). The mongod is
reaped by `stop` along with the SUT (all `*.pid` files in the state dir).

## Requirements

- `bash`, `curl`, `python3` (used to read the manifest, poll readiness, and
  launch services in their own session portably) — all present in the runtime image.
- `mvn` / the project's `./mvnw` wrapper; `git`. No `jq` needed.
- Network access on first build for several services (Maven Central, Spring
  snapshots, plus Cargo's Tomcat download, Liberty's runtime, jitpack for
  genome-nexus). Pre-warming `~/.m2` is recommended.
- `genome-nexus` needs a reachable MongoDB; `setup-service.sh` provides it
  automatically via the bundled `mongod` pre_run step (empty DB). The rest are
  self-contained (embedded H2 / HSQLDB / Derby or classpath data). For populated
  genome-nexus data, point `MONGODB_URI` at a `genomenexus/gn-mongo` instance.
