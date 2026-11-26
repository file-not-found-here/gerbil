package em.embedded.uaa;

import java.io.File;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

import com.webfuzzing.commons.auth.LoginEndpoint;
import com.webfuzzing.commons.auth.TokenHandling;
import org.cloudfoundry.experimental.boot.UaaBootApplication;
import org.cloudfoundry.identity.uaa.impl.config.YamlServletProfileInitializer;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

/**
 * Runs UAA's boot application with the configuration the developers' MockMvc tests load,
 * under their postgresql profile on a throwaway cluster, and resets it between tests the
 * way their pollution prevention does.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    // Static, since each test class makes its own controller and one service serves the JVM.
    private static LocalPostgres postgres;
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;
    private static Connection connection;
    // Holds the snapshot in its session's temporary tables, which EvoMaster does not see.
    private static Connection snapshots;
    private static String restore;

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
        // UaaBootApplication.main sets these before running, since UAA's beans need them.
        System.setProperty("spring.main.allow-bean-definition-overriding", "true");
        System.setProperty("spring.main.allow-circular-references", "true");
        System.setProperty("UAA_CONFIG_URL", "classpath:integration_test_properties.yml");
        // The schema is for the search only, so the suite measures UAA as its developers run it.
        boolean searching = isInstrumentationActivated();
        SpringApplication application = searching
                ? new SpringApplication(UaaBootApplication.class, OpenApiAccess.class)
                : new SpringApplication(UaaBootApplication.class);
        application.addInitializers(new YamlServletProfileInitializer(),
                context -> context.getBeanFactory().addBeanPostProcessor(new SqlVisiblePool()));
        try {
            postgres = new LocalPostgres();
            context = application.run("--server.port=" + port, "--server.servlet.context-path=/",
                    "--springdoc.api-docs.enabled=" + searching,
                    "--spring_profiles=postgresql",
                    "--database.url=" + postgres.url(),
                    "--database.username=postgres",
                    "--database.password=");
            port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
            connection = DriverManager.getConnection(postgres.url(), "postgres", "");
            snapshots = DriverManager.getConnection(postgres.url(), "postgres", "");
            restore = snapshot(snapshots);
        } catch (IOException | SQLException e) {
            throw new IllegalStateException(e);
        }
        return baseUrl();
    }

    /** Copies the rows UAA's bootstraps wrote, and returns the script that restores them. */
    private static String snapshot(Connection connection) throws SQLException {
        List<String> tables = new ArrayList<>();
        try (ResultSet result = connection.getMetaData().getTables(null, "public", "%", new String[]{"TABLE"})) {
            while (result.next()) {
                String table = result.getString("TABLE_NAME");
                if (!table.equals("schema_version")) {
                    tables.add(table);
                }
            }
        }
        List<String> script = new ArrayList<>();
        // Foreign keys are not checked while the rows go back in, in whatever order.
        script.add("SET LOCAL session_replication_role = replica");
        script.add("TRUNCATE TABLE " + String.join(", ", tables.stream().map(t -> "public.\"" + t + "\"").toList()));
        try (Statement statement = connection.createStatement()) {
            for (String table : tables) {
                statement.execute("CREATE TEMPORARY TABLE \"" + table + "\" AS TABLE public.\"" + table + "\"");
                script.add("INSERT INTO public.\"" + table + "\" SELECT * FROM pg_temp.\"" + table + "\"");
            }
            // Ids handed out after a reset then match those of a fresh start.
            try (ResultSet result = statement.executeQuery("SELECT sequencename, start_value, last_value"
                    + " FROM pg_sequences WHERE schemaname = 'public'")) {
                while (result.next()) {
                    String sequence = "'public.\"" + result.getString(1) + "\"'";
                    long last = result.getLong(3);
                    script.add(result.wasNull()
                            ? "SELECT setval(" + sequence + ", " + result.getLong(2) + ", false)"
                            : "SELECT setval(" + sequence + ", " + last + ", true)");
                }
            }
        }
        return String.join(";\n", script);
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
            // The cluster is removed below.
        }
        context.close();
        postgres.stop();
    }

    @Override
    public boolean isSutRunning() {
        return context != null && context.isRunning();
    }

    @Override
    public void resetStateOfSUT() {
        // Restores the rows UAA's bootstraps wrote at startup, which is the state the
        // developers' PollutionPreventionExtension recreates by rerunning them, without
        // hashing every client secret and password again before each test.
        try {
            snapshots.setAutoCommit(false);
            try (Statement statement = snapshots.createStatement()) {
                statement.execute(restore);
                snapshots.commit();
            } catch (SQLException e) {
                // An aborted transaction would fail every later reset too.
                snapshots.rollback();
                throw e;
            } finally {
                snapshots.setAutoCommit(true);
            }
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.cloudfoundry.identity.uaa.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // Cleaning every table a test touches would also drop the clients and users UAA
        // bootstraps at startup.
        return List.of(new DbSpecification(DatabaseType.POSTGRES, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        return List.of(
                token("admin", "grant_type=client_credentials&client_id=admin&client_secret=adminsecret"),
                token("marissa", "grant_type=password&client_id=cf&client_secret=&username=marissa&password=koala"));
    }

    private static AuthenticationDto token(String name, String payload) {
        TokenHandling token = new TokenHandling();
        token.setExtractFrom(TokenHandling.ExtractFrom.BODY);
        token.setExtractSelector("/access_token");
        token.setSendIn(TokenHandling.SendIn.HEADER);
        token.setSendName("Authorization");
        token.setSendTemplate("Bearer {token}");
        LoginEndpoint login = new LoginEndpoint();
        login.setEndpoint("/oauth/token");
        login.setVerb(LoginEndpoint.HttpVerb.POST);
        login.setContentType("application/x-www-form-urlencoded");
        login.setPayloadRaw(payload);
        login.setExpectCookies(false);
        login.setToken(token);
        AuthenticationDto dto = new AuthenticationDto(name);
        dto.setLoginEndpointAuth(login);
        return dto;
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/v3/api-docs", null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
    }

    /**
     * Turns off the Tomcat pool's statement facade, which only lets closed statements answer
     * equals and hashCode: EvoMaster reads each statement's SQL from its toString, which the
     * facade replaces with a description of itself.
     */
    static final class SqlVisiblePool implements BeanPostProcessor {

        @Override
        public Object postProcessAfterInitialization(Object bean, String name) {
            if (bean instanceof org.apache.tomcat.jdbc.pool.DataSource pool) {
                pool.setUseStatementFacade(false);
            }
            return bean;
        }
    }

    /** Serves springdoc's schema, which UAA's catch-all chain would send to its login page. */
    @Configuration
    static class OpenApiAccess {

        @Bean
        @Order(Ordered.HIGHEST_PRECEDENCE)
        SecurityFilterChain openApiDocuments(HttpSecurity http) throws Exception {
            return http.antMatcher("/v3/api-docs/**")
                    .authorizeHttpRequests(requests -> requests.anyRequest().permitAll())
                    .build();
        }
    }

    /** A cluster of the image's PostgreSQL server, which refuses to run as root. */
    static final class LocalPostgres {

        private final Path directory;
        private final int port;
        private boolean stopped;

        LocalPostgres() throws IOException {
            directory = Files.createTempDirectory("evomaster-postgres");
            // Generated tests leave the service running, and the server outlives the JVM.
            Runtime.getRuntime().addShutdownHook(new Thread(this::stop));
            try (ServerSocket socket = new ServerSocket(0)) {
                port = socket.getLocalPort();
            }
            run("chown", "postgres:postgres", directory.toString());
            asPostgres(binary("initdb"), "-D", data(), "-U", "postgres", "-A", "trust",
                    "-E", "UTF8", "--locale=C", "--no-sync");
            asPostgres(binary("pg_ctl"), "-D", data(), "-l", directory.resolve("log").toString(), "-w",
                    "-o", "-p " + port + " -k " + directory + " -c listen_addresses=localhost -c fsync=off",
                    "start");
        }

        String url() {
            return "jdbc:postgresql://localhost:" + port + "/postgres";
        }

        synchronized void stop() {
            if (stopped) {
                return;
            }
            stopped = true;
            try {
                asPostgres(binary("pg_ctl"), "-D", data(), "-m", "immediate", "-w", "stop");
                try (Stream<Path> paths = Files.walk(directory)) {
                    paths.sorted(Comparator.reverseOrder()).map(Path::toFile).forEach(File::delete);
                }
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }

        private String data() {
            return directory.resolve("data").toString();
        }

        private static String binary(String name) throws IOException {
            try (Stream<Path> versions = Files.list(Path.of("/usr/lib/postgresql"))) {
                return versions.map(version -> version.resolve("bin").resolve(name))
                        .filter(Files::isExecutable)
                        .findFirst()
                        .orElseThrow(() -> new IOException("no PostgreSQL " + name))
                        .toString();
            }
        }

        private static void asPostgres(String... command) throws IOException {
            List<String> full = new ArrayList<>(List.of("runuser", "-u", "postgres", "--"));
            full.addAll(List.of(command));
            run(full.toArray(String[]::new));
        }

        private static void run(String... command) throws IOException {
            Process process = new ProcessBuilder(command).inheritIO().start();
            try {
                if (process.waitFor() != 0) {
                    throw new IOException(String.join(" ", command) + " exited with " + process.exitValue());
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException(e);
            }
        }
    }
}
