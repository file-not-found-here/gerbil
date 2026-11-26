package em.embedded.openvsx;

import java.io.File;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import com.webfuzzing.commons.auth.Header;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.eclipse.openvsx.RegistryApplication;
import org.eclipse.openvsx.security.IdPrincipal;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.evomaster.client.java.sql.SqlScriptRunner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.TypeExcludeFilter;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.cache.CacheManager;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.type.classreading.MetadataReader;
import org.springframework.core.type.classreading.MetadataReaderFactory;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.web.context.RequestAttributeSecurityContextRepository;
import org.springframework.security.web.csrf.DefaultCsrfToken;
import org.springframework.security.web.csrf.HttpSessionCsrfTokenRepository;
import org.springframework.session.web.http.SessionRepositoryFilter;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Runs the registry with its test configuration on a throwaway PostgreSQL cluster (its
 * migrations only run there) and database search instead of Elasticsearch, logging requests
 * in as a seeded account through a test-only header.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    // The super user and token of the dev seeds (src/dev/resources/db/migration), and the
    // user the developers' tests log in as.
    private static final List<String> SEED = List.of(
            "INSERT INTO user_data (id, login_name, role) VALUES (1001, 'super_user', 'admin')",
            "INSERT INTO personal_access_token (id, user_data, value, active, created_timestamp,"
                    + " accessed_timestamp, description) VALUES (1001, 1001, 'super_token', true,"
                    + " current_timestamp, current_timestamp, 'For publishing test extensions')",
            "INSERT INTO user_data (id, login_name, provider) VALUES (1002, 'test_user', 'github')");
    static final String LOGIN_HEADER = "X-EvoMaster-Login";
    // Principals as openvsx's OAuth login builds them for these accounts.
    private static final Map<String, IdPrincipal> ACCOUNTS = Map.of(
            "super_user", new IdPrincipal(1001, "super_user", AuthorityUtils.createAuthorityList("ROLE_ADMIN")),
            "test_user", new IdPrincipal(1002, "test_user", AuthorityUtils.NO_AUTHORITIES));
    private static final String CSRF_TOKEN = "evomaster";
    // Written by the server's schedulers rather than through the API.
    private static final List<String> SERVER_TABLES = List.of(
            "flyway_schema_history", "shedlock", "migration_item");

    // Static, since each test class makes its own controller and one service serves the JVM.
    private static LocalPostgres postgres;
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;
    private static Connection connection;
    private static String truncation;

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
            // Ehcache locks its disk store, ${java.io.tmpdir}, so concurrent JVMs in one
            // container, such as PIT's workers, each need their own.
            System.setProperty("java.io.tmpdir", Files.createTempDirectory("evomaster-openvsx").toString());
            postgres = new LocalPostgres();
            SpringApplication application = new SpringApplication(RegistryApplication.class);
            application.addInitializers(context -> {
                context.getBeanFactory().registerSingleton("evoMasterTestClassFilter", new TestClassFilter());
                FilterRegistrationBean<TestLogin> login = new FilterRegistrationBean<>(new TestLogin());
                // Inside Spring Session's JDBC sessions, where Spring Security looks for the
                // CSRF token, and ahead of Spring Security's chain, which reads what it sets.
                login.setOrder(SessionRepositoryFilter.DEFAULT_ORDER + 1);
                context.getBeanFactory().registerSingleton("evoMasterTestLogin", login);
            });
            context = application.run(
                    "--server.port=" + port,
                    "--spring.datasource.driver-class-name=org.postgresql.Driver",
                    "--spring.datasource.url=" + postgres.url(),
                    "--spring.datasource.username=postgres",
                    "--spring.datasource.password=",
                    "--ovsx.elasticsearch.enabled=false",
                    "--ovsx.databasesearch.enabled=true",
                    "--springdoc.api-docs.version=openapi_3_0");
            port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
            connection = DriverManager.getConnection(postgres.url(), "postgres", "");
            truncation = truncation(connection);
            SqlScriptRunner.runCommands(connection, SEED);
        } catch (IOException | SQLException e) {
            throw new IllegalStateException(e);
        }
        return baseUrl();
    }

    /** One statement emptying every table but the servers' own, which is fast in PostgreSQL. */
    private static String truncation(Connection connection) throws SQLException {
        List<String> tables = new ArrayList<>();
        try (ResultSet result = connection.getMetaData().getTables(null, "public", "%", new String[]{"TABLE"})) {
            while (result.next()) {
                String table = result.getString("TABLE_NAME");
                if (!SERVER_TABLES.contains(table) && !table.startsWith("jobrunr_")
                        && !table.endsWith("_migration_item")) {
                    tables.add("public.\"" + table + "\"");
                }
            }
        }
        // Sequences keep counting, since Hibernate hands out ids from blocks it holds in memory.
        return "TRUNCATE TABLE " + String.join(", ", tables) + " CONTINUE IDENTITY";
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
        SqlScriptRunner.runCommands(connection, List.of(truncation));
        SqlScriptRunner.runCommands(connection, SEED);
        // The caches would otherwise keep answering with what earlier tests stored.
        CacheManager caches = context.getBean(CacheManager.class);
        caches.getCacheNames().forEach(name -> caches.getCache(name).clear());
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.eclipse.openvsx.";
    }

    @Override
    public String packagesToSkipInstrumentation() {
        // The search's method replacements in Ehcache make it read one of the caches
        // ehcache.xml declares twice, so the service could not start.
        return "org.ehcache.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // Cleaning every table a test touches would also drop the job server's state.
        return List.of(new DbSpecification(DatabaseType.POSTGRES, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // Publishing and admin APIs also take the seeded token as a `token` query parameter,
        // which the search can find in the database.
        return List.of(login("admin", "super_user"), login("user", "test_user"));
    }

    private static AuthenticationDto login(String name, String account) {
        AuthenticationDto dto = new AuthenticationDto(name);
        dto.setFixedHeaders(List.of(header(LOGIN_HEADER, account), header("X-CSRF-TOKEN", maskedCsrfToken())));
        return dto;
    }

    /** The token as Spring Security 6 expects a client to send it: masked, here by zeros. */
    private static String maskedCsrfToken() {
        byte[] token = CSRF_TOKEN.getBytes(StandardCharsets.UTF_8);
        byte[] masked = new byte[token.length * 2];
        System.arraycopy(token, 0, masked, token.length, token.length);
        return Base64.getUrlEncoder().encodeToString(masked);
    }

    private static Header header(String name, String value) {
        Header header = new Header();
        header.setName(name);
        header.setValue(value);
        return header;
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/v3/api-docs", null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
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

    /**
     * Logs a request in as the seeded account its header names. Users log in through GitHub
     * and eclipse.org only, which the developers' tests replace by stubbing
     * UserService.findLoggedInUser.
     */
    static final class TestLogin extends OncePerRequestFilter {

        @Override
        protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            String account = request.getHeader(LOGIN_HEADER);
            IdPrincipal principal = account == null ? null : ACCOUNTS.get(account);
            if (principal != null) {
                SecurityContext context = SecurityContextHolder.createEmptyContext();
                context.setAuthentication(new OAuth2AuthenticationToken(principal, principal.getAuthorities(), "github"));
                request.setAttribute(RequestAttributeSecurityContextRepository.DEFAULT_REQUEST_ATTR_NAME, context);
                // The CSRF token a logged-in browser's session holds.
                request.getSession().setAttribute(HttpSessionCsrfTokenRepository.class.getName() + ".CSRF_TOKEN",
                        new DefaultCsrfToken("X-CSRF-TOKEN", "_csrf", CSRF_TOKEN));
            }
            chain.doFilter(request, response);
        }
    }

    /** Keeps the module's test-only configurations out of the application's component scan. */
    static final class TestClassFilter extends TypeExcludeFilter {

        @Override
        public boolean match(MetadataReader reader, MetadataReaderFactory factory) throws IOException {
            return reader.getResource().getURL().getPath().matches(".*/build/classes/[^/]+/test/.*");
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
