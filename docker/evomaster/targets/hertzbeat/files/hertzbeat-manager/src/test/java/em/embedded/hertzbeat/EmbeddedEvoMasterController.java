package em.embedded.hertzbeat;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

import org.apache.hertzbeat.collector.dispatch.timer.TimerDispatch;
import org.apache.hertzbeat.manager.Manager;
import org.evomaster.client.java.controller.AuthUtils;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Runs the manager, which embeds the alerter, warehouse and collector, with the test profile
 * its developers' integration tests use, on in-memory H2.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    private static final String DATABASE_URL = "jdbc:h2:mem:hertzbeat;MODE=MYSQL;DB_CLOSE_DELAY=-1";
    private static final String SNAPSHOT_PREFIX = "evomaster_snapshot_";

    // Static, since each test class makes its own controller and one service serves the JVM.
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;
    private static Connection connection;
    // Holds the snapshot in its session's local temporary tables, which EvoMaster's schema
    // extraction does not see: it looks tables up by name, so copies elsewhere sent its
    // foreign keys to tables without primary keys.
    private static Connection snapshots;
    private static List<String> tables;

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
        try {
            connection = DriverManager.getConnection(DATABASE_URL, "sa", "");
            // A restart during the search starts from an empty database, as the first start does.
            connection.createStatement().execute("DROP ALL OBJECTS");
            context = SpringApplication.run(Manager.class,
                    "--spring.profiles.active=test",
                    "--server.port=" + port,
                    "--spring.datasource.url=" + DATABASE_URL,
                    "--spring.datasource.username=sa",
                    "--spring.datasource.password=",
                    // The collector scheduler's own server would otherwise hold a fixed port.
                    "--scheduler.server.port=0",
                    // OpenTelemetry's JDBC wrapper hides each statement's SQL from EvoMaster.
                    "--otel.instrumentation.jdbc.enabled=false",
                    "--springdoc.api-docs.version=openapi_3_0");
            port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
            snapshot();
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
            snapshots.close();
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
        // Monitors schedule collection jobs that keep writing history and alerts.
        context.getBean(TimerDispatch.class).goOnline();
        try (Statement statement = snapshots.createStatement()) {
            statement.execute("SET REFERENTIAL_INTEGRITY FALSE");
            for (String table : tables) {
                statement.execute("DELETE FROM PUBLIC.\"" + table + "\"");
                statement.execute("INSERT INTO PUBLIC.\"" + table + "\" SELECT * FROM \"" + SNAPSHOT_PREFIX + table + "\"");
            }
            statement.execute("SET REFERENTIAL_INTEGRITY TRUE");
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Keeps the rows the manager writes at startup: its configuration, JWT secret and collector. */
    private void snapshot() throws SQLException {
        tables = new ArrayList<>();
        try (ResultSet result = connection.getMetaData().getTables(null, "PUBLIC", "%", new String[]{"TABLE"})) {
            while (result.next()) {
                tables.add(result.getString("TABLE_NAME"));
            }
        }
        snapshots = DriverManager.getConnection(DATABASE_URL, "sa", "");
        try (Statement statement = snapshots.createStatement()) {
            for (String table : tables) {
                statement.execute("CREATE LOCAL TEMPORARY TABLE \"" + SNAPSHOT_PREFIX + table
                        + "\" AS SELECT * FROM PUBLIC.\"" + table + "\"");
            }
        }
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.apache.hertzbeat.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // Cleaning only the tables a test wrote would drop the rows written at startup.
        return List.of(new DbSpecification(DatabaseType.H2, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // The default accounts of sureness.yml, one per role.
        return List.of(
                login("admin"),
                login("tom"),
                login("guest"));
    }

    private static AuthenticationDto login(String account) {
        return AuthUtils.getForJsonTokenBearer(
                account,
                "/api/account/auth/form",
                "{\"type\": 0, \"identifier\": \"" + account + "\", \"credential\": \"hertzbeat\"}",
                "/data/token");
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(
                baseUrl() + "/v3/api-docs",
                null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
    }
}
