package em.embedded.dspace;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.sql.Blob;
import java.sql.Clob;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import javax.sql.DataSource;

import com.webfuzzing.commons.auth.Header;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.apache.solr.client.solrj.SolrClient;
import org.apache.solr.client.solrj.SolrServerException;
import org.dspace.app.rest.WebApplication;
import org.dspace.app.rest.security.DSpaceCsrfTokenRepository;
import org.dspace.app.rest.utils.DSpaceConfigurationInitializer;
import org.dspace.app.rest.utils.DSpaceKernelInitializer;
import org.dspace.authorize.AuthorizeException;
import org.dspace.core.Context;
import org.dspace.core.I18nUtil;
import org.dspace.eperson.EPerson;
import org.dspace.eperson.Group;
import org.dspace.eperson.factory.EPersonServiceFactory;
import org.dspace.eperson.service.EPersonService;
import org.dspace.eperson.service.GroupService;
import org.dspace.kernel.ServiceManager;
import org.dspace.servicemanager.DSpaceKernelImpl;
import org.dspace.servicemanager.DSpaceKernelInit;
import org.dspace.services.ConfigurationService;
import org.dspace.services.factory.DSpaceServicesFactory;
import org.dspace.solr.MockSolrServer;
import org.dspace.storage.rdbms.DatabaseUtils;
import org.evomaster.client.java.controller.AuthUtils;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.evomaster.client.java.utils.SimpleLogger;
import org.hibernate.Session;
import org.hibernate.SessionFactory;
import org.hibernate.context.internal.ThreadLocalSessionContext;
import org.springdoc.core.converters.models.DefaultPageable;
import org.springdoc.core.customizers.OperationCustomizer;
import org.springdoc.core.utils.SpringDocUtils;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.TypeExcludeFilter;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.DefaultParameterNameDiscoverer;
import org.springframework.core.MethodParameter;
import org.springframework.core.Ordered;
import org.springframework.core.io.support.ResourcePropertySource;
import org.springframework.core.type.classreading.MetadataReader;
import org.springframework.core.type.classreading.MetadataReaderFactory;
import org.springframework.data.domain.Pageable;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.context.support.StandardServletEnvironment;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Runs the REST API as the developers' integration tests boot it: on the test installation,
 * with in-memory H2, embedded Solr cores and the tests' two accounts.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    private static final String PASSWORD = "mySuperS3cretP4ssW0rd";
    private static final String ADMIN = "admin@email.com";
    private static final String EPERSON = "test@email.com";
    private static final String LOGIN = "/api/authn/login";
    private static final String BEARER = "Bearer ";
    // Only the harness's own login asks for the token in a body, so fuzzed logins stay unchanged.
    private static final String TOKEN_IN_BODY = "X-EvoMaster-Token-In-Body";
    // DSpace checks that the CSRF header repeats the CSRF cookie, so a client may pick the token.
    private static final String CSRF_TOKEN = "evomaster";
    // Their writes would land between a test's calls, and offline their remote calls only fail.
    private static final List<String> SCHEDULES = List.of(
            "sitemap.cron",
            "ldn.queue.extractor.cron",
            "ldn.queue.timeout.checker.cron",
            "solr-database-resync.cron",
            "google.analytics.cron");
    private static final List<String> SOLR_CORES =
            List.of("search", "statistics", "authority", "qaevent", "suggestion");
    private static final Map<String, Pattern> PATTERNS = new ConcurrentHashMap<>();

    // Static, since each test class makes its own controller and one service serves the JVM.
    private static ConfigurableApplicationContext context;
    private static boolean stopped;
    private static Connection connection;
    private static DatabaseSnapshot seeded;
    private static List<SolrClient> solrCores;

    public static void main(String[] args) {
        new InstrumentedSutStarter(new EmbeddedEvoMasterController()).start();
    }

    public EmbeddedEvoMasterController() {
        setControllerPort(40100);
    }

    /** org.dspace.app.TestApplication, the application the developers' integration tests boot. */
    @SpringBootApplication(scanBasePackageClasses = WebApplication.class)
    static class Application {
    }

    @Override
    public String startSut() {
        stopped = false;
        if (context != null && context.isRunning()) {
            return baseUrl();
        }
        // As AbstractDSpaceIntegrationTest does, since dates are stored in UTC.
        TimeZone.setDefault(TimeZone.getTimeZone(ZoneOffset.UTC));
        SCHEDULES.forEach(schedule -> System.setProperty(schedule, "-"));
        // The tests' @BeforeClass methods start the kernel and migrate before Spring Boot starts.
        DSpaceKernelImpl kernel = DSpaceKernelInit.getKernel(null);
        if (!kernel.isRunning()) {
            kernel.start(System.getProperty("dspace.dir"));
        }
        try {
            initDatabase();
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        // Only the search reads the schema, so the generated tests run the developers' application.
        boolean documented = isInstrumentationActivated();
        SpringApplication application = new SpringApplication(Application.class);
        application.setEnvironment(testEnvironment());
        application.addInitializers(
                new DSpaceKernelInitializer(),
                new DSpaceConfigurationInitializer(),
                context -> {
                    context.getBeanFactory().registerSingleton("evoMasterTestClassFilter", new TestClassFilter());
                    context.getBeanFactory().registerSingleton("evoMasterLoginTokenBody", loginTokenBody());
                    context.getBeanFactory().registerSingleton("evoMasterTransactionRelease", transactionRelease());
                    if (documented) {
                        context.getBeanFactory().registerSingleton("evoMasterQueryMaps", queryMapsLeftOut());
                    }
                });
        context = application.run("--server.port=0",
                "--springdoc.api-docs.enabled=" + documented,
                "--springdoc.api-docs.version=openapi_3_0");
        // After startup, which registers springdoc's own Pageable replacement; the schema is built on first use.
        if (documented) {
            documentInjectedArguments();
        }
        ConfigurationService configuration = DSpaceServicesFactory.getInstance().getConfigurationService();
        // Utils.getBaseObjectRestFromUri only accepts URIs under the configured server URL.
        configuration.setProperty("dspace.server.url", baseUrl());
        try {
            createAccounts();
            connection = DriverManager.getConnection(configuration.getProperty("db.url"),
                    configuration.getProperty("db.username"), configuration.getProperty("db.password"));
            seeded = DatabaseSnapshot.take(connection);
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        solrCores = SOLR_CORES.stream().map(core -> new MockSolrServer(core).getSolrServer()).toList();
        return baseUrl();
    }

    /**
     * Has springdoc describe Pageable as its page, size and sort parameters and leave out the arguments
     * Spring injects, which it would otherwise list as required query objects.
     */
    private static void documentInjectedArguments() {
        SpringDocUtils.getConfig()
                .replaceParameterObjectWithClass(Pageable.class, DefaultPageable.class)
                .addRequestWrapperToIgnore(PagedResourcesAssembler.class, CsrfToken.class);
    }

    /** Leaves out a @RequestParam map, which takes whatever query parameters a request has, not one named after it. */
    private static OperationCustomizer queryMapsLeftOut() {
        return (operation, handler) -> {
            String[] names = new DefaultParameterNameDiscoverer().getParameterNames(handler.getMethod());
            for (MethodParameter parameter : handler.getMethodParameters()) {
                if (parameter.hasParameterAnnotation(RequestParam.class)
                        && Map.class.isAssignableFrom(parameter.getParameterType())
                        && names != null && operation.getParameters() != null) {
                    String name = names[parameter.getParameterIndex()];
                    operation.getParameters().removeIf(p -> p.getName().equals(name));
                }
            }
            return operation;
        };
    }

    /** The environment @TestPropertySource gives the developers' tests. */
    private static StandardServletEnvironment testEnvironment() {
        StandardServletEnvironment environment = new StandardServletEnvironment();
        try {
            environment.getPropertySources().addFirst(
                    new ResourcePropertySource("classpath:application-test.properties"));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        return environment;
    }

    /** AbstractIntegrationTestWithDatabase.initDatabase(). */
    private static void initDatabase() throws SQLException {
        DatabaseUtils.updateDatabase();
        DataSource dataSource = serviceManager().getServiceByName("dataSource", DataSource.class);
        try (Connection c = dataSource.getConnection(); Statement statement = c.createStatement()) {
            statement.execute("CREATE ALIAS IF NOT EXISTS matches FOR '"
                    + EmbeddedEvoMasterController.class.getName() + ".matches'");
        }
    }

    /** The regular expression test that org.dspace.util.DSpaceH2Dialect gives H2. */
    public static boolean matches(String regex, String value) {
        return PATTERNS.computeIfAbsent(regex, Pattern::compile).matcher(value).matches();
    }

    /** The accounts AbstractIntegrationTestWithDatabase.setUp() creates. */
    private static void createAccounts() throws SQLException {
        EPersonService ePersonService = EPersonServiceFactory.getInstance().getEPersonService();
        GroupService groupService = EPersonServiceFactory.getInstance().getGroupService();
        Context context = new Context(Context.Mode.READ_WRITE);
        context.turnOffAuthorisationSystem();
        try {
            createAccount(context, ePersonService, EPERSON, "first", "last");
            groupService.initDefaultGroupNames(context);
            EPerson admin = createAccount(context, ePersonService, ADMIN, "first (admin)", "last (admin)");
            groupService.addMember(context, groupService.findByName(context, Group.ADMIN), admin);
        } catch (AuthorizeException e) {
            throw new IllegalStateException(e);
        }
        context.restoreAuthSystemState();
        context.complete();
    }

    /** What EPersonBuilder does for the accounts. */
    private static EPerson createAccount(Context context, EPersonService ePersonService, String email,
                                         String firstName, String lastName)
            throws SQLException, AuthorizeException {
        EPerson ePerson = ePersonService.create(context);
        ePerson.setFirstName(context, firstName);
        ePerson.setLastName(context, lastName);
        ePerson.setEmail(email);
        ePerson.setCanLogIn(true);
        ePerson.setLanguage(context, I18nUtil.getDefaultLocale().getLanguage());
        ePersonService.setPassword(ePerson, PASSWORD);
        ePersonService.update(context, ePerson);
        return ePerson;
    }

    private static ServiceManager serviceManager() {
        return DSpaceServicesFactory.getInstance().getServiceManager();
    }

    private String baseUrl() {
        return "http://localhost:" + context.getEnvironment().getProperty("local.server.port");
    }

    @Override
    public void stopSut() {
        // One service serves the JVM, as Spring's context cache does for the developers' tests.
        // DSpace cannot start again in the JVM that stopped it (its context listener
        // deregisters the JDBC drivers, and its kernel keeps the closed data source), so a
        // restart the search asks for is a reset: the service only counts as stopped until
        // the next start.
        stopped = true;
    }

    @Override
    public boolean isSutRunning() {
        return !stopped && context != null && context.isRunning();
    }

    @Override
    public void resetStateOfSUT() {
        try {
            abortSessionsHoldingLocks();
            seeded.restore(connection);
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        // Entities and queries Hibernate cached may no longer be in the database.
        sessionFactory().getCache().evictAllRegions();
        // AbstractIntegrationTestWithDatabase.destroy() empties these; committing makes it visible at once.
        for (SolrClient core : solrCores) {
            try {
                core.deleteByQuery("*:*");
                core.commit();
            } catch (SolrServerException | IOException e) {
                throw new IllegalStateException(e);
            }
        }
    }

    /** Ends what other database sessions left open, since restoring the tables waits for their locks. */
    private void abortSessionsHoldingLocks() throws SQLException {
        List<Long> sessions = new ArrayList<>();
        try (Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery("SELECT SESSION_ID FROM INFORMATION_SCHEMA.SESSIONS"
                     + " WHERE SESSION_ID <> SESSION_ID() AND (CONTAINS_UNCOMMITTED"
                     + " OR SESSION_ID IN (SELECT SESSION_ID FROM INFORMATION_SCHEMA.LOCKS))")) {
            while (result.next()) {
                sessions.add(result.getLong(1));
            }
        }
        for (long session : sessions) {
            SimpleLogger.warn("Aborting database session " + session + ", which a previous test left open");
            try (Statement statement = connection.createStatement()) {
                statement.execute("CALL ABORT_SESSION(" + session + ")");
            }
        }
    }

    private static SessionFactory sessionFactory() {
        return serviceManager().getServiceByName("sessionFactory", SessionFactory.class);
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.dspace.";
    }

    @Override
    public String packagesToSkipInstrumentation() {
        // The search's taint tracking in third-party code breaks these at startup: ehcache's
        // JAXB-bound configuration gets duplicate cache aliases, and iterating Lucene's lock set
        // races with the embedded Solr cores loading in parallel.
        return "org.ehcache.,org.apache.lucene.,org.apache.solr.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // A cleaned database would lose the registries, groups and site that migrations seed.
        return List.of(new DbSpecification(DatabaseType.H2, connection).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        AuthenticationDto anonymous = new AuthenticationDto("anonymous");
        anonymous.setFixedHeaders(csrfHeaders());
        return List.of(login("admin", ADMIN), login("eperson", EPERSON), anonymous);
    }

    private static AuthenticationDto login(String name, String email) {
        String form = "user=" + URLEncoder.encode(email, StandardCharsets.UTF_8) + "&password=" + PASSWORD;
        AuthenticationDto dto = AuthUtils.getForJsonToken(
                name, LOGIN, form, "/token", BEARER, MediaType.APPLICATION_FORM_URLENCODED_VALUE);
        List<Header> headers = new ArrayList<>(csrfHeaders());
        headers.add(header(TOKEN_IN_BODY, "true"));
        dto.getLoginEndpointAuth().setHeaders(headers);
        dto.setFixedHeaders(csrfHeaders());
        return dto;
    }

    private static List<Header> csrfHeaders() {
        return List.of(
                header(DSpaceCsrfTokenRepository.DEFAULT_CSRF_HEADER_NAME, CSRF_TOKEN),
                header(HttpHeaders.COOKIE, DSpaceCsrfTokenRepository.DEFAULT_CSRF_COOKIE_NAME + "=" + CSRF_TOKEN));
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
        return SutInfoDto.OutputFormat.JAVA_JUNIT_4;
    }

    private static FilterRegistrationBean<LoginTokenBody> loginTokenBody() {
        FilterRegistrationBean<LoginTokenBody> registration = new FilterRegistrationBean<>(new LoginTokenBody());
        registration.addUrlPatterns(LOGIN);
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }

    private static FilterRegistrationBean<TransactionRelease> transactionRelease() {
        FilterRegistrationBean<TransactionRelease> registration =
                new FilterRegistrationBean<>(new TransactionRelease());
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }

    /**
     * Rolls back a transaction a request leaves open on its thread, as a login filter that throws after
     * updating the user does; an isolation measure, since its locks would outlast the test.
     */
    static final class TransactionRelease extends OncePerRequestFilter {

        @Override
        protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            try {
                chain.doFilter(request, response);
            } finally {
                Session session = ThreadLocalSessionContext.unbind(sessionFactory());
                if (session != null) {
                    if (session.getTransaction().getStatus().canRollback()) {
                        SimpleLogger.warn("Rolling back the transaction " + request.getMethod() + " "
                                + request.getRequestURI() + " left open");
                        session.getTransaction().rollback();
                    }
                    session.close();
                }
            }
        }
    }

    /** Repeats the token of the harness's login in a JSON body, the only place EvoMaster 4.0.0 reads tokens from. */
    static final class LoginTokenBody extends OncePerRequestFilter {

        @Override
        protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                throws ServletException, IOException {
            chain.doFilter(request, response);
            String authorization = response.getHeader(HttpHeaders.AUTHORIZATION);
            if (request.getHeader(TOKEN_IN_BODY) != null && authorization != null && !response.isCommitted()) {
                response.setContentType(MediaType.APPLICATION_JSON_VALUE);
                response.getWriter().write("{\"token\": \"" + authorization.substring(BEARER.length()) + "\"}");
            }
        }
    }

    /** Keeps the module's test-only components out of the scan, as staged trees do not have them. */
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

    /** The seeded database's rows and sequence positions, which every test starts from. */
    static final class DatabaseSnapshot {

        private static final String TABLES = "SELECT TABLE_NAME, LAST_MODIFICATION FROM INFORMATION_SCHEMA.TABLES"
                + " WHERE TABLE_SCHEMA = 'PUBLIC' AND TABLE_TYPE = 'BASE TABLE'";
        private static final String SEQUENCES = "SELECT SEQUENCE_NAME, BASE_VALUE FROM INFORMATION_SCHEMA.SEQUENCES"
                + " WHERE SEQUENCE_SCHEMA = 'PUBLIC'";

        private final Map<String, List<Object[]>> rows;
        private final Map<String, Long> sequences;
        // H2 bumps a table's modification id on every write, so unchanged tables need no restoring.
        private Map<String, Long> modifications;

        private DatabaseSnapshot(Map<String, List<Object[]>> rows, Map<String, Long> sequences,
                                 Map<String, Long> modifications) {
            this.rows = rows;
            this.sequences = sequences;
            this.modifications = modifications;
        }

        static DatabaseSnapshot take(Connection connection) throws SQLException {
            Map<String, Long> modifications = longs(connection, TABLES);
            Map<String, List<Object[]>> rows = new LinkedHashMap<>();
            try (Statement statement = connection.createStatement()) {
                for (String table : modifications.keySet()) {
                    List<Object[]> tableRows = new ArrayList<>();
                    try (ResultSet result = statement.executeQuery("SELECT * FROM " + quoted(table))) {
                        int columns = result.getMetaData().getColumnCount();
                        while (result.next()) {
                            Object[] row = new Object[columns];
                            for (int column = 0; column < columns; column++) {
                                row[column] = detached(result.getObject(column + 1));
                            }
                            tableRows.add(row);
                        }
                    }
                    rows.put(table, tableRows);
                }
            }
            return new DatabaseSnapshot(rows, longs(connection, SEQUENCES), modifications);
        }

        // LOB values only live as long as the result set that read them.
        private static Object detached(Object value) throws SQLException {
            if (value instanceof Clob clob) {
                return clob.getSubString(1, (int) clob.length());
            }
            if (value instanceof Blob blob) {
                return blob.getBytes(1, (int) blob.length());
            }
            return value;
        }

        void restore(Connection connection) throws SQLException {
            Map<String, Long> current = longs(connection, TABLES);
            List<String> changed = rows.keySet().stream()
                    .filter(table -> !current.get(table).equals(modifications.get(table)))
                    .toList();
            Map<String, Long> positions = longs(connection, SEQUENCES);
            try (Statement statement = connection.createStatement()) {
                statement.execute("SET REFERENTIAL_INTEGRITY FALSE");
                try {
                    for (String table : changed) {
                        statement.execute("TRUNCATE TABLE " + quoted(table));
                        insert(connection, table, rows.get(table));
                    }
                } finally {
                    statement.execute("SET REFERENTIAL_INTEGRITY TRUE");
                }
                for (Map.Entry<String, Long> sequence : sequences.entrySet()) {
                    if (!sequence.getValue().equals(positions.get(sequence.getKey()))) {
                        statement.execute("ALTER SEQUENCE " + quoted(sequence.getKey())
                                + " RESTART WITH " + sequence.getValue());
                    }
                }
            }
            modifications = longs(connection, TABLES);
        }

        private static Map<String, Long> longs(Connection connection, String query) throws SQLException {
            Map<String, Long> values = new LinkedHashMap<>();
            try (Statement statement = connection.createStatement();
                 ResultSet result = statement.executeQuery(query)) {
                while (result.next()) {
                    values.put(result.getString(1), result.getLong(2));
                }
            }
            return values;
        }

        private static void insert(Connection connection, String table, List<Object[]> tableRows)
                throws SQLException {
            if (tableRows.isEmpty()) {
                return;
            }
            String parameters = String.join(", ", Collections.nCopies(tableRows.get(0).length, "?"));
            try (PreparedStatement insert = connection.prepareStatement(
                    "INSERT INTO " + quoted(table) + " VALUES (" + parameters + ")")) {
                for (Object[] row : tableRows) {
                    for (int column = 0; column < row.length; column++) {
                        insert.setObject(column + 1, row[column]);
                    }
                    insert.addBatch();
                }
                insert.executeBatch();
            }
        }

        private static String quoted(String name) {
            return "\"PUBLIC\".\"" + name + "\"";
        }
    }
}
