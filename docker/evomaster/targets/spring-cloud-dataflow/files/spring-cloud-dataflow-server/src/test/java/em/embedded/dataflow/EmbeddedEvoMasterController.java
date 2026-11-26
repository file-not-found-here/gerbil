package em.embedded.dataflow;

import java.io.IOException;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.List;

import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbCleaner;
import org.evomaster.client.java.sql.DbSpecification;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.TypeExcludeFilter;
import org.springframework.cloud.dataflow.server.single.DataFlowServerApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.type.classreading.MetadataReader;
import org.springframework.core.type.classreading.MetadataReaderFactory;

/**
 * Runs the Data Flow server on in-memory H2, as its DefaultLocalTests do. Skipper does
 * not run, so stream deployments fail on the loopback while stream definitions work.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    private static final String DATABASE_URL = "jdbc:h2:mem:dataflow;DB_CLOSE_DELAY=-1";

    // Static, since each test class makes its own controller and one service serves the JVM.
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;
    private static Connection connection;

    public static void main(String[] args) {
        new InstrumentedSutStarter(new EmbeddedEvoMasterController()).start();
    }

    public EmbeddedEvoMasterController() {
        setControllerPort(40100);
    }

    @Override
    public String startSut() {
        if (isSutRunning()) {
            return baseUrl();
        }
        System.setProperty("spring.devtools.restart.enabled", "false");
        SpringApplication application = new SpringApplication(DataFlowServerApplication.class);
        application.addInitializers(context -> context.getBeanFactory()
                .registerSingleton("evoMasterTestClassFilter", new TestClassFilter()));
        context = application.run(
                "--server.port=" + port,
                "--spring.datasource.url=" + DATABASE_URL,
                "--spring.datasource.username=sa",
                "--spring.datasource.password=",
                "--spring.datasource.driver-class-name=org.h2.Driver",
                "--spring.cloud.config.enabled=false",
                // The test-scope REST client would call the server while it starts.
                "--spring.autoconfigure.exclude="
                        + "org.springframework.cloud.dataflow.rest.client.config.DataFlowClientAutoConfiguration",
                "--springdoc.api-docs.enabled=true",
                "--springdoc.api-docs.version=openapi_3_0");
        port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
        try {
            connection = DriverManager.getConnection(DATABASE_URL, "sa", "");
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        return baseUrl();
    }

    private String baseUrl() {
        return "http://localhost:" + context.getEnvironment().getProperty("local.server.port");
    }

    @Override
    public void stopSut() {
        // Generated tests stop the service after each class, and PIT runs each test on its
        // own; outside the search one service serves the JVM, as Spring's context cache does.
        if (!isInstrumentationActivated()) {
            return;
        }
        try {
            connection.close();
        } catch (SQLException ignored) {
            // The database is dropped with the JVM.
        }
        context.close();
    }

    @Override
    public boolean isSutRunning() {
        return context != null && context.isRunning();
    }

    @Override
    public void resetStateOfSUT() {
        DbCleaner.clearDatabase_H2(connection, List.of("flyway_schema_history_dataflow"), null);
    }

    // The server's own modules, and the Skipper client and security modules it runs with.
    @Override
    public String getPackagePrefixesToCover() {
        return "org.springframework.cloud.dataflow.,org.springframework.cloud.skipper.,"
                + "org.springframework.cloud.common.security.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // EvoMaster cannot parse the audit records' inserts, so cleaning the tables it saw
        // written would keep those rows while resetting every sequence, the audit's included.
        return List.of(new DbSpecification(DatabaseType.H2, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // Security is off unless OAuth2 is configured.
        return List.of();
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/v3/api-docs", null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
    }

    /** Keeps the module's test-only configurations out of the application's component scan. */
    static final class TestClassFilter extends TypeExcludeFilter {

        @Override
        public boolean match(MetadataReader reader, MetadataReaderFactory factory) throws IOException {
            return reader.getResource().getURL().getPath().contains("/test-classes/");
        }

        @Override
        public boolean equals(Object other) {
            return other instanceof TestClassFilter;
        }

        @Override
        public int hashCode() {
            return TestClassFilter.class.hashCode();
        }
    }
}
