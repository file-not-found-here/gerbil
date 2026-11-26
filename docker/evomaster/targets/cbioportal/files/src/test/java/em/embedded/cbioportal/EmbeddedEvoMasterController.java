package em.embedded.cbioportal;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.List;

import org.cbioportal.PortalApplication;
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
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.Resource;
import org.springframework.core.type.classreading.MetadataReader;
import org.springframework.core.type.classreading.MetadataReaderFactory;
import org.springframework.jdbc.datasource.init.ScriptUtils;

/**
 * Runs the portal without authentication on in-memory H2 in MySQL mode, with the H2 schema
 * of the developers' repository tests and the data their full-application tests seed.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    private static final String DATABASE_URL = "jdbc:h2:mem:cgds;DATABASE_TO_UPPER=false;MODE=MySQL;"
            + "NON_KEYWORDS=END,KEY,VALUE;DB_CLOSE_DELAY=-1";
    private static final ClassPathResource SCHEMA = new ClassPathResource("cgds-h2.sql");

    private static final Resource DATA = seed();

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
        try {
            connection = DriverManager.getConnection(DATABASE_URL, "sa", "");
            // Loaded before the context, whose services read it while starting, as the
            // developers' TestConfig builds its embedded database.
            connection.createStatement().execute("DROP ALL OBJECTS");
            ScriptUtils.executeSqlScript(connection, SCHEMA);
            ScriptUtils.executeSqlScript(connection, DATA);
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        SpringApplication application = new SpringApplication(PortalApplication.class);
        application.addInitializers(context -> context.getBeanFactory()
                .registerSingleton("evoMasterTestClassFilter", new TestClassFilter()));
        context = application.run(
                "--server.port=" + port,
                "--authenticate=false",
                "--session.service.url=",
                "--spring.datasource.url=" + DATABASE_URL,
                "--spring.datasource.driver-class-name=org.h2.Driver",
                "--spring.datasource.username=sa",
                "--spring.datasource.password=",
                "--spring.sql.init.mode=never",
                "--springdoc.api-docs.version=openapi_3_0");
        port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
        return baseUrl();
    }

    private static Resource seed() {
        try {
            String script = new ClassPathResource("seed_mini.sql").getContentAsString(StandardCharsets.UTF_8);
            // MySQL needs this to read the script's double quotes as identifiers; H2 does so already.
            return new ByteArrayResource(script.replaceFirst("(?m)^SET SESSION sql_mode.*;$", "")
                    .getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
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
        // Services cache studies, genes and cancer types at startup, so a reset restores
        // exactly the data they started from.
        DbCleaner.clearDatabase_H2(connection, null, null);
        ScriptUtils.executeSqlScript(connection, DATA);
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.cbioportal.,org.mskcc.cbio.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        return List.of(new DbSpecification(DatabaseType.H2, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        return List.of();
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/api/v3/api-docs/public", null);
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
