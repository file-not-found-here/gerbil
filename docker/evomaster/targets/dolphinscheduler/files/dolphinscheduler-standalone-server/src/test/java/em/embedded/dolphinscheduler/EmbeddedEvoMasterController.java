package em.embedded.dolphinscheduler;

import java.io.IOException;
import java.io.InputStream;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;

import com.webfuzzing.commons.auth.LoginEndpoint;
import org.apache.dolphinscheduler.StandaloneServer;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbCleaner;
import org.evomaster.client.java.sql.DbSpecification;
import org.evomaster.client.java.sql.SqlScriptRunner;
import org.quartz.Scheduler;
import org.quartz.SchedulerException;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Runs the standalone server (API, master, worker and alert servers in one JVM) on its
 * in-memory H2 database and JDBC registry, as its own configuration does.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    private static final String DATABASE_URL =
            "jdbc:h2:mem:dolphinscheduler;MODE=MySQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=true";
    private static final String SCHEMA = "/sql/dolphinscheduler_h2.sql";
    // Written by the servers themselves rather than through the API: the scheduler's
    // and the registry's state, and the plugins and version recorded at startup.
    private static final List<String> SERVER_TABLES = List.of(
            "qrtz_job_details", "qrtz_triggers", "qrtz_blob_triggers", "qrtz_calendars",
            "qrtz_cron_triggers", "qrtz_fired_triggers", "qrtz_locks",
            "qrtz_paused_trigger_grps", "qrtz_scheduler_state", "qrtz_simple_triggers",
            "qrtz_simprop_triggers", "t_ds_jdbc_registry_data", "t_ds_jdbc_registry_lock",
            "t_ds_jdbc_registry_client_heartbeat", "t_ds_jdbc_registry_data_change_event",
            "t_ds_plugin_define", "t_ds_version");

    // Static, since each test class makes its own controller and one service serves the JVM.
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;
    private static Connection connection;
    private static List<String> seed;

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
        context = SpringApplication.run(StandaloneServer.class,
                "--server.port=" + port,
                // The servers register their ports, so they need free ones rather than 0
                // for PIT's parallel workers not to collide.
                "--master.listen-port=" + freePort(),
                "--worker.listen-port=" + freePort(),
                "--alert.port=" + freePort(),
                "--server.servlet.context-path=/",
                "--api.python-gateway.enabled=false",
                // Load protection holds startup whenever the host is busy.
                "--master.server-load-protection.enabled=false",
                "--worker.server-load-protection.enabled=false");
        port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
        try {
            connection = DriverManager.getConnection(DATABASE_URL, "sa", "");
            seed = seedInserts();
        } catch (SQLException | IOException e) {
            throw new IllegalStateException(e);
        }
        return baseUrl();
    }

    private static int freePort() {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static List<String> seedInserts() throws IOException {
        List<String> inserts = new ArrayList<>();
        try (InputStream schema = StandaloneServer.class.getResourceAsStream(SCHEMA)) {
            String script = new String(schema.readAllBytes(), StandardCharsets.UTF_8);
            for (String statement : script.split(";\\s*\\n")) {
                String trimmed = statement.replaceAll("(?m)^--.*$", "").strip();
                if (trimmed.regionMatches(true, 0, "INSERT INTO", 0, 11)
                        && SERVER_TABLES.stream().noneMatch(trimmed::contains)) {
                    inserts.add(trimmed);
                }
            }
        }
        return inserts;
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
        // Schedules put online keep firing workflows until Quartz forgets them.
        try {
            context.getBean(Scheduler.class).clear();
        } catch (SchedulerException e) {
            throw new IllegalStateException(e);
        }
        // DATABASE_TO_LOWER also lowercases the default schema's name.
        DbCleaner.clearDatabase(connection, "public", SERVER_TABLES, null, DatabaseType.H2, true);
        SqlScriptRunner.runCommands(connection, seed);
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.apache.dolphinscheduler.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // Cleaning every table a test touches would also drop the registry's heartbeats.
        return List.of(new DbSpecification(DatabaseType.H2, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // The administrator seeded by dolphinscheduler_h2.sql; logins keep a session cookie.
        LoginEndpoint login = new LoginEndpoint();
        login.setEndpoint("/login");
        login.setVerb(LoginEndpoint.HttpVerb.POST);
        login.setContentType("application/x-www-form-urlencoded");
        login.setPayloadRaw("userName=admin&userPassword=dolphinscheduler123");
        login.setExpectCookies(true);
        AuthenticationDto admin = new AuthenticationDto("admin");
        admin.setLoginEndpointAuth(login);
        return List.of(admin);
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/v3/api-docs", null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
    }
}
