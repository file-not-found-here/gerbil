"""Services of the comparison subset that EvoMaster fuzzes through white-box drivers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerbil.execution.projects import PROJECTS, ExecutionProject

DOCKER_DIRECTORY = Path(__file__).resolve().parents[3] / "docker" / "evomaster"


@dataclass(frozen=True)
class EvoMasterTarget:
    """One service and its harness under docker/evomaster/targets/<name>/.

    The harness's `files` are copied into the workspace and its `build.patch` adds
    EvoMaster's client to `module`, whose test classpath runs the service in-process
    from the embedded `driver` class. The generated suite joins that module's tests.
    `restores` are the test resources the driver loads, which staged trees take back
    from the pinned commit after the agents' test clearing removes them, and
    `search_arguments` are EvoMaster options the target needs beyond the defaults.
    """

    name: str
    project: ExecutionProject
    module: str
    driver: str
    schema_paths: tuple[str, ...] = ("/v3/api-docs",)
    excluded_paths: tuple[str, ...] = ()
    jvm_args: tuple[str, ...] = ()
    restores: tuple[str, ...] = ()
    search_arguments: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        return DOCKER_DIRECTORY / "targets" / self.name

    @property
    def files(self) -> Path:
        return self.directory / "files"

    @property
    def build_patch(self) -> Path:
        return self.directory / "build.patch"


TARGETS: dict[str, EvoMasterTarget] = {
    target.name: target
    for target in (
        EvoMasterTarget(
            "hertzbeat",
            PROJECTS["hertzbeat"],
            module="hertzbeat-manager",
            driver="em.embedded.hertzbeat.EmbeddedEvoMasterController",
            # Event streams never finish, so each call waits out EvoMaster's TCP timeout
            # and a few of them use up the search budget.
            excluded_paths=(r"/api/(alert|manager)/sse/subscribe",),
        ),
        EvoMasterTarget(
            "spring-cloud-dataflow",
            PROJECTS["spring-cloud-dataflow"],
            module="spring-cloud-dataflow-server",
            driver="em.embedded.dataflow.EmbeddedEvoMasterController",
            # EvoMaster (4.0.0 through 6.2.0) samples rows for CLOB columns at their full
            # 1 GB length, past the JVM's array size limit whatever the heap; queries are
            # still traced.
            search_arguments=("--generateSqlDataWithSearch=false",),
        ),
        EvoMasterTarget(
            "kogito-springboot",
            PROJECTS["incubator-kie-kogito-runtimes"],
            module=(
                "springboot/integration-tests/"
                "integration-tests-springboot-processes-it"
            ),
            driver="em.embedded.kogito.EmbeddedEvoMasterController",
        ),
        EvoMasterTarget(
            "dolphinscheduler",
            PROJECTS["dolphinscheduler"],
            # Its dependencies are the API, master, worker and alert servers.
            module="dolphinscheduler-standalone-server",
            driver="em.embedded.dolphinscheduler.EmbeddedEvoMasterController",
        ),
        EvoMasterTarget(
            "cbioportal",
            PROJECTS["cbioportal"],
            module=".",
            driver="em.embedded.cbioportal.EmbeddedEvoMasterController",
            restores=(
                "src/test/resources/cgds-h2.sql",
                "src/test/resources/seed_mini.sql",
            ),
            schema_paths=("/api/v3/api-docs/public", "/api/v3/api-docs/internal"),
        ),
        EvoMasterTarget(
            "openvsx",
            PROJECTS["openvsx"],
            module=".",
            driver="em.embedded.openvsx.EmbeddedEvoMasterController",
            restores=("server/src/test/resources/application.yml",),
            # As for DSpace: 6.2.0's SQL distances throw on the LATERAL joins jOOQ writes for
            # most catalogue queries, and generating rows needs them; queries are still traced.
            search_arguments=(
                "--heuristicsForSQL=false",
                "--generateSqlDataWithSearch=false",
            ),
        ),
        EvoMasterTarget(
            "clouddriver",
            PROJECTS["spinnaker"],
            # The service the developers' API tests exercise most.
            module="clouddriver/clouddriver-web",
            driver="em.embedded.clouddriver.EmbeddedEvoMasterController",
            restores=(
                "clouddriver/clouddriver-web/src/test/resources/application.properties",
            ),
        ),
        EvoMasterTarget(
            "kylin",
            PROJECTS["kylin"],
            module="src/server",
            driver="em.embedded.kylin.EmbeddedEvoMasterController",
            # Springfox's Swagger 2 groups (its OpenAPI 3 rendering fails), internal first:
            # a public operation on an internal one's method and path differs only in
            # media type, which one document cannot hold, so the internal one is kept.
            # Kylin's filters expect its /kylin context path, which springfox puts in
            # each documented path.
            schema_paths=(
                "/kylin/v2/api-docs?group=v4%20internal",
                "/kylin/v2/api-docs?group=v4%20public",
                "/kylin/v2/api-docs?group=v2%20public",
            ),
            # Uploads: EvoMaster's multipart support is experimental and off by default, and
            # 4.0.0 wrote the bodies as Java strings that did not compile. Exclusion is per
            # path, so the jar path's DELETE goes too; springfox lists the validation upload
            # in some fetches only.
            excluded_paths=(
                r"/kylin/api/custom/jar",
                r"/kylin/api/metastore/import/models",
                r"/kylin/api/metastore/models",
                r"/kylin/api/metastore/validation/models",
                r"/kylin/api/projects/\{project\}/project_kerberos_info",
            ),
            search_arguments=(
                # EvoMaster crashes building request bodies from the classes its agent sees
                # Kylin parse with Jackson, which reads every JSON body for its project
                # (4.0.0 on a gene it could not copy, 6.2.0 on the Swagger 2 schema having
                # no OpenAPI 3 components).
                "--expandRestIndividuals=false",
                # Job rows hold serialized executables, which generated rows cannot form.
                "--generateSqlDataWithSearch=false",
            ),
        ),
        EvoMasterTarget(
            "dspace",
            PROJECTS["DSpace"],
            module="dspace-server-webapp",
            driver="em.embedded.dspace.EmbeddedEvoMasterController",
            restores=(
                # The test installation, and the Spring Boot settings the tests add.
                "dspace-api/src/test/data",
                "dspace-server-webapp/src/test/data",
                "dspace-server-webapp/src/test/resources/application-test.properties",
                "dspace-server-webapp/src/test/resources/log4j2-test.xml",
                # What the installation's test doubles, which the harness carries, serve.
                "dspace-api/src/test/resources/org/dspace/app/sherpa",
                "dspace-api/src/test/resources/org/dspace/authority/orcid",
                "dspace-api/src/test/resources/org/dspace/external/openaire-no-projects.xml",
                "dspace-api/src/test/resources/org/dspace/external/openaire-project.xml",
                "dspace-api/src/test/resources/org/dspace/external/openaire-projects.xml",
                "dspace-server-webapp/src/test/resources/org/dspace/license/cc-license-rdf.xml",
            ),
            search_arguments=(
                # As for Data Flow: EvoMaster samples rows for its CLOB columns at full length.
                "--generateSqlDataWithSearch=false",
                # 6.2.0's SQL distances throw on the parenthesized joins Hibernate writes for
                # DSpace's object inheritance, which most requests run; queries are still traced.
                "--heuristicsForSQL=false",
            ),
        ),
        EvoMasterTarget(
            "uaa",
            PROJECTS["uaa"],
            # Only this module sees both the boot application and the server's tests.
            module="uaa",
            driver="em.embedded.uaa.EmbeddedEvoMasterController",
            restores=("uaa/src/test/resources/integration_test_properties.yml",),
            # The error page of rate-limited calls answers every call with 429, which
            # EvoMaster (since 6.0.0) waits out before calling again, indefinitely.
            excluded_paths=(r"/error429",),
        ),
    )
}


def target_for(project: ExecutionProject) -> EvoMasterTarget:
    """The target whose driver runs `project`'s service; every project has exactly one."""
    (target,) = (target for target in TARGETS.values() if target.project is project)
    return target
