# EvoMaster settings

The suites in `runs/` come from EvoMaster 6.2.0 in white-box mode, with a one-hour search budget per service, seed 1234 and the search capped at 6 CPUs. EvoMaster kept its default settings apart from the options below, as each run's log records.

## Services

| Project | Service EvoMaster ran | Heap |
| --- | --- | --- |
| DSpace | REST webapp | 8 GB |
| dolphinscheduler | Standalone server | 16 GB |
| hertzbeat | Manager | 8 GB |
| incubator-kie-kogito-runtimes | Spring Boot processes integration-test app | 16 GB |
| kylin | REST server | 8 GB |
| openvsx | Server | 8 GB |
| spinnaker | Clouddriver | 16 GB |
| spring-cloud-dataflow | Data Flow server | 16 GB |
| uaa | UAA server | 16 GB |

Kylin, DSpace, HertzBeat and OpenVSX ran with an 8 GB heap, the setting of EMB's white-box experiments. DolphinScheduler's first attempt ran out of memory at 8 GB, so the heap was raised to 16 GB from UAA on, including DolphinScheduler's rerun. The 8 GB runs show no sign of being limited by it. Their longest pause between tests was 7.4 seconds, against 200 seconds in DolphinScheduler's failed attempt.

## Options for every service

| Option | Value | Purpose |
| --- | --- | --- |
| `--blackBox` | `false` | White-box mode, which EvoMaster 6 no longer assumes by default |
| `--maxTime` | `1h` | Search budget |
| `--seed` | `1234` | Reproducible search |
| `--overrideOpenAPIUrl` | Scoped schema | Schema to search, described below |
| `--outputFolder`, `--outputFilePrefix` | Per service | Where the suite is written and what its classes are called |
| `--testSuiteSplitType` | `NONE` | One suite per service, not separate files by outcome |
| `--maxTestsPerTestSuite` | `500` | Splits the suite into classes small enough to compile |
| `--nameWithQueryParameters` | `false` | Test names that compile |
| `--maxResponseByteSize` | `32768` | Response bodies small enough to assert in one method |
| `--addPreDefinedTests` | `false` | Avoids a crash after the search |
| `--writeStatistics`, `--statisticsFile` | `true`, per service | Coverage statistics for the final suite |
| `--snapshotInterval`, `--snapshotStatisticsFile` | `5`, per service | Coverage every 5% of the budget |
| `--showProgress` | `false` | Plain logs |
| `--createConfigPathIfMissing` | `false` | No config file written into the service's tree |

**Schema.** EvoMaster reads a schema that Gerbil fetched from the running service, not the live one. Where a service serves several documents (Kylin), they are merged into one. Operations with Ant-style wildcard paths, which are not valid OpenAPI paths, are dropped, along with the per-service exclusions below.

**Class size.** A Java class holds at most 65,535 constants. HertzBeat's 5-minute suite of 1,327 tests used 21,002 in a single class, so a one-hour suite in one class would not compile.

**Test names.** EvoMaster copies query parameter names into test method names, and names such as `a-b` or `user[]` are not valid Java.

**Response size.** EvoMaster asserts every field of a stored response in one test method. HertzBeat's i18n bundle produced a method past the JVM's 64 KB method limit, which does not compile and is too deeply nested for CLDK's parser. EvoMaster does not keep response bodies past 32 KB, so its tests assert the status of those responses but not their content.

**Predefined test.** EvoMaster adds a test that fetches the schema. Several services serve their schema only while the search runs, and 6.2.0 crashes scoring this test after the search because the schema's path is not a resource its dependency tracking knows.

## Options for individual services

| Service | Option | Without it |
| --- | --- | --- |
| Data Flow | `--generateSqlDataWithSearch=false` | Crash |
| DSpace | `--generateSqlDataWithSearch=false` | Crash |
| DSpace | `--heuristicsForSQL=false` | Most tests cannot be scored |
| OpenVSX | `--heuristicsForSQL=false` | Most tests cannot be scored |
| OpenVSX | `--generateSqlDataWithSearch=false` | Needs the SQL heuristics |
| Kylin | `--expandRestIndividuals=false` | Crash |
| Kylin | `--generateSqlDataWithSearch=false` | Invalid rows |
| Kylin | 6 upload operations removed from the schema | Tests that do not compile |
| HertzBeat | 2 event-stream operations removed from the schema | Budget spent waiting |
| UAA | 5 operations on `/error429` removed from the schema | Search stalls |

Clouddriver, DolphinScheduler and Kogito needed none.

**Row generation (Data Flow, DSpace).** To insert a row, EvoMaster generates a value for every column, bounded by the column's declared maximum length. Both services run on H2, an in-process Java database, and have CLOB columns, which hold long text. H2 declares their maximum at about 1 GB, and EvoMaster fails building a value of that size, whatever the heap.

**SQL heuristics (DSpace, OpenVSX).** EvoMaster measures how close each query came to matching rows and uses that distance to guide the search. 6.2.0 fails computing it for the parenthesized joins Hibernate writes for DSpace's object inheritance and the `LATERAL` joins jOOQ writes for most of OpenVSX's catalogue queries. Most requests run such queries, and a test that touches one cannot be scored, so the service is restarted. Row generation depends on these distances, so it is off for OpenVSX too.

**Request bodies (Kylin).** EvoMaster expands request bodies using the classes it sees the service parse with Jackson. Kylin parses every JSON body this way, and 6.2.0 crashes doing so because Kylin's schema is Swagger 2 and has no OpenAPI 3 components.

**Rows for Kylin.** Kylin's job rows hold serialized Java objects, which generated rows cannot form.

**Uploads (Kylin).** These operations take multipart bodies. EvoMaster's multipart support is experimental and off by default, and the bodies an earlier version wrote did not compile. Exclusion is per path, so the DELETE on the jar path is removed with its upload. The six operations are `POST` and `DELETE /kylin/api/custom/jar`, `POST /kylin/api/metastore/import/models`, `POST /kylin/api/metastore/models`, `POST /kylin/api/metastore/validation/models` and `PUT /kylin/api/projects/{project}/project_kerberos_info`.

**Event streams (HertzBeat).** `GET /api/alert/sse/subscribe` and `GET /api/manager/sse/subscribe` never finish, so each call waits out EvoMaster's TCP timeout, and a few calls use up the budget.

**Rate-limit page (UAA).** `/error429` answers every call with status 429. EvoMaster 6 waits out a 429 before calling again, and here that wait never ends.

The SQL options turn off EvoMaster's use of the database, not the service's. Each service still reads and writes its database normally, and EvoMaster still records the queries each test runs.

## cbioportal

EvoMaster produced no one-hour suite for cbioportal, so it is not among the services above. All three attempts ran out of memory, at 8 GB, at 16 GB, and at 16 GB with `--taintAnalysisForMapsAndArrays=false`. The heap filled with EvoMaster's model of cbioportal's large request bodies, over 40 million gene objects in use.

## Outside EvoMaster's options

Some drivers change how the service runs under EvoMaster, without changing EvoMaster's settings.

- The OpenVSX and DSpace drivers leave third-party packages uninstrumented where EvoMaster's method replacements break them at startup (Ehcache, and DSpace's Lucene and Solr).
- Kylin's background threads record coverage while EvoMaster resets its target ids, which leaves ids EvoMaster cannot map. During the search only, Kylin's driver drops them before each test.
- EvoMaster reads a statement's SQL from its `toString`. UAA's driver turns off its Tomcat pool's statement facade and HertzBeat's driver turns off OpenTelemetry's JDBC wrapper, since both hide that text.
