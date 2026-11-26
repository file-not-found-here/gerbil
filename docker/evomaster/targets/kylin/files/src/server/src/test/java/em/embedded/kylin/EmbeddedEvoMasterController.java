package em.embedded.kylin;

import java.io.File;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.channels.FileChannel;
import java.nio.file.StandardOpenOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Properties;
import java.util.Set;
import java.util.function.BooleanSupplier;

import org.apache.commons.io.FileUtils;
import org.apache.kylin.common.KylinConfig;
import org.apache.kylin.common.persistence.MetadataType;
import org.apache.kylin.common.persistence.RawResource;
import org.apache.kylin.common.persistence.ResourceStore;
import org.apache.kylin.common.persistence.metadata.jdbc.JdbcUtil;
import org.apache.kylin.common.persistence.transaction.UnitOfWork;
import org.apache.kylin.common.persistence.transaction.UnitOfWorkParams;
import org.apache.kylin.common.util.ProcessUtils;
import org.apache.kylin.common.util.TempMetadataBuilder;
import org.apache.kylin.common.util.Unsafe;
import org.apache.kylin.guava30.shaded.common.io.ByteSource;
import org.apache.kylin.job.execution.ExecutableState;
import org.apache.kylin.job.rest.JobMapperFilter;
import org.apache.kylin.job.scheduler.JdbcJobScheduler;
import org.apache.kylin.job.util.JobContextUtil;
import org.apache.kylin.rest.BootstrapServer;
import org.apache.kylin.source.jdbc.H2Database;
import org.evomaster.client.java.controller.AuthUtils;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.api.dto.database.schema.DatabaseType;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.instrumentation.staticstate.ObjectiveRecorder;
import org.evomaster.client.java.sql.DbSpecification;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;

/** Runs an all-mode Kylin node on its developers' test metadata, without ZooKeeper or a cluster. */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    // Responses show this path, so it must not carry the pid that Kylin's dev profile names its copy after.
    private static final String METADATA_COPY = "../examples/test_data/evomaster";
    private static final String SNAPSHOT_PREFIX = "evomaster_snapshot_";
    private static final Duration JOBS_TIMEOUT = Duration.ofMinutes(10);
    private static final int QUIET_SECONDS = 5;

    private static FileChannel metadataLock;
    private static File metadataCopy;

    // Static, since each test class makes its own controller and one service serves the JVM.
    private static ConfigurableApplicationContext context;
    private static boolean stopped;
    private static Connection database;
    // Holds the snapshot in its session's local temporary tables, which EvoMaster's schema
    // extraction does not see: it looks tables up by name, so copies elsewhere could stand in
    // for the tables Kylin uses.
    private static Connection snapshots;
    private static List<String> tables;
    private static List<String> identityRestarts;
    private static Map<String, byte[]> initialMetadata;
    private static Map<String, RawResource> knownMetadata;

    public static void main(String[] args) {
        new InstrumentedSutStarter(new EmbeddedEvoMasterController()).start();
    }

    public EmbeddedEvoMasterController() {
        setControllerPort(40100);
    }

    @Override
    public String startSut() {
        stopped = false;
        if (context != null && context.isRunning()) {
            return baseUrl();
        }
        File metadata = copyTestMetadata();
        Unsafe.setProperty("KYLIN_HOME", metadata.getAbsolutePath());
        // The default lock is ZooKeeper's, as the developers' tests replace too.
        Unsafe.setProperty("kylin.metadata.distributed-lock-impl", "org.apache.kylin.common.lock.LocalLockFactory");
        // Kylin activates the dev profile, whose YAML turns discovery off, after Spring has
        // read the YAML, so service discovery would still connect to ZooKeeper.
        Unsafe.setProperty("kylin.env.zookeeper.enabled", "false");
        // The schema stage reads the API documentation, which the developers' config leaves off.
        Unsafe.setProperty("kylin.swagger.enabled", Boolean.toString(isInstrumentationActivated()));
        context = SpringApplication.run(BootstrapServer.class, "--server.port=0");
        awaitStartupJobs();
        try {
            Properties store = JdbcUtil.datasourceParameters(KylinConfig.getInstanceFromEnv().getMetadataUrl());
            database = DriverManager.getConnection(
                    store.getProperty("url"), store.getProperty("username"), store.getProperty("password"));
            snapshots = DriverManager.getConnection(
                    store.getProperty("url"), store.getProperty("username"), store.getProperty("password"));
            snapshotDatabase();
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        knownMetadata = currentMetadata();
        initialMetadata = new HashMap<>();
        knownMetadata.forEach((path, resource) -> initialMetadata.put(path, resource.getContent().clone()));
        return baseUrl();
    }

    /**
     * Does what Kylin's dev profile does when it finds no profile, but into a fixed
     * directory. Concurrent JVMs in one working directory, such as PIT's workers, would
     * overwrite each other's copy, so a JVM that cannot lock the directory uses its own.
     */
    private static File copyTestMetadata() {
        try {
            if (metadataLock == null) {
                new File(METADATA_COPY).getParentFile().mkdirs();
                // Held until the JVM exits; the static field keeps the channel from being collected.
                metadataLock = FileChannel.open(new File(METADATA_COPY + ".lock").toPath(),
                        StandardOpenOption.CREATE, StandardOpenOption.WRITE);
                metadataCopy = metadataLock.tryLock() != null
                        ? new File(METADATA_COPY)
                        : new File(METADATA_COPY + "-" + ProcessUtils.getCurrentId("0"));
            }
            FileUtils.deleteQuietly(metadataCopy);
            FileUtils.copyDirectory(new File(TempMetadataBuilder.KAP_META_TEST_DATA), metadataCopy);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        File copy = metadataCopy;
        Unsafe.setProperty("dev.dont-load-test-metadata", "true");
        KylinConfig.setKylinConfigForLocalTest(copy.getPath());
        Unsafe.setProperty("hadoop.home.dir", copy.getAbsolutePath() + "/working-dir");
        Unsafe.setProperty("spark.local", "true");
        Unsafe.setProperty("kylin.query.pushdown-enabled", "true");
        Unsafe.setProperty("kylin.query.pushdown.runner-class-name", "org.apache.kylin.query.pushdown.PushDownRunnerJdbcImpl");
        Unsafe.setProperty("kylin.query.pushdown.jdbc.url", "jdbc:h2:mem:db_default;SCHEMA=DEFAULT");
        Unsafe.setProperty("kylin.query.pushdown.jdbc.driver", "org.h2.Driver");
        Unsafe.setProperty("kylin.query.pushdown.jdbc.username", "sa");
        Unsafe.setProperty("kylin.query.pushdown.jdbc.password", "");
        try {
            Connection pushdown = DriverManager.getConnection("jdbc:h2:mem:db_default;DB_CLOSE_DELAY=-1", "sa", "");
            new H2Database(pushdown, KylinConfig.getInstanceFromEnv(), "default").loadAllTables();
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
        return copy;
    }

    /**
     * Waits out the cron jobs Kylin submits once ready: during startSut EvoMaster counts their
     * code as boot time, while later they race its objective bookkeeping when a search starts.
     */
    private static void awaitStartupJobs() {
        JobMapperFilter unfinished = JobMapperFilter.builder()
                .statuses(Arrays.asList(ExecutableState.READY, ExecutableState.PENDING, ExecutableState.RUNNING))
                .build();
        // The jobs are submitted from a scheduler thread, so an empty queue must also stay empty.
        int[] quiet = {0};
        await(() -> {
            long count = JobContextUtil.getJobInfoDao(KylinConfig.getInstanceFromEnv()).countByFilter(unfinished);
            quiet[0] = count == 0 ? quiet[0] + 1 : 0;
            return quiet[0] >= QUIET_SECONDS;
        }, 1000);
    }

    /** Keeps the rows of Kylin's JDBC stores at startup, its finished startup jobs among them. */
    private void snapshotDatabase() throws SQLException {
        tables = new ArrayList<>();
        try (ResultSet result = database.getMetaData().getTables(null, "PUBLIC", "%", new String[]{"TABLE"})) {
            while (result.next()) {
                tables.add(result.getString("TABLE_NAME"));
            }
        }
        try (Statement statement = snapshots.createStatement()) {
            for (String table : tables) {
                statement.execute("CREATE LOCAL TEMPORARY TABLE \"" + SNAPSHOT_PREFIX + table
                        + "\" AS SELECT * FROM PUBLIC.\"" + table + "\"");
            }
            // Restored rows leave identity counters where tests left them, which ids in responses show.
            identityRestarts = new ArrayList<>();
            try (ResultSet identity = statement.executeQuery("SELECT TABLE_NAME, COLUMN_NAME, IDENTITY_BASE"
                    + " FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'PUBLIC' AND IS_IDENTITY = 'YES'")) {
                while (identity.next()) {
                    identityRestarts.add("ALTER TABLE \"" + identity.getString(1) + "\" ALTER COLUMN \""
                            + identity.getString(2) + "\" RESTART WITH " + identity.getLong(3));
                }
            }
        }
    }

    private String baseUrl() {
        return "http://localhost:" + context.getEnvironment().getProperty("local.server.port");
    }

    @Override
    public void stopSut() {
        // One service serves the JVM, as Spring's context cache does for the developers' tests.
        // Kylin takes minutes to start, and its startup jobs need not settle again in the JVM
        // that stopped it, so a restart the search asks for is a reset: the service only
        // counts as stopped until the next start.
        stopped = true;
    }

    @Override
    public boolean isSutRunning() {
        return !stopped && context != null && context.isRunning();
    }

    /**
     * Restores the startup state of Kylin's JDBC stores, jobs and query history among them,
     * and of its metadata, users included, after the jobs a test started have stopped.
     */
    @Override
    public void resetStateOfSUT() {
        if (isInstrumentationActivated()) {
            forgetUnmappedTargetIds();
        }
        // Dropping the queued jobs first keeps the schedulers from starting them meanwhile.
        restoreDatabase();
        // In UT mode Kylin runs jobs in JobContextUtil's own context rather than a Spring bean.
        JdbcJobScheduler jobs = JobContextUtil.getJobContext(KylinConfig.getInstanceFromEnv()).getJobScheduler();
        if (jobs.hasRunningJob()) {
            await(() -> !jobs.hasRunningJob(), 100);
            restoreDatabase();
        }
        restoreMetadata();
    }

    private void restoreDatabase() {
        try (Statement statement = snapshots.createStatement()) {
            for (String table : tables) {
                statement.execute("DELETE FROM PUBLIC.\"" + table + "\"");
                statement.execute("INSERT INTO PUBLIC.\"" + table + "\" SELECT * FROM \"" + SNAPSHOT_PREFIX + table + "\"");
            }
            for (String restart : identityRestarts) {
                statement.execute(restart);
            }
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void await(BooleanSupplier done, long pollMillis) {
        long deadline = System.nanoTime() + JOBS_TIMEOUT.toNanos();
        while (!done.getAsBoolean()) {
            if (System.nanoTime() > deadline) {
                throw new IllegalStateException("Kylin's jobs ran past " + JOBS_TIMEOUT);
            }
            try {
                Thread.sleep(pollMillis);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(e);
            }
        }
    }

    /** Restores changed metadata as Kylin's metadata restore does. */
    private void restoreMetadata() {
        Map<String, RawResource> current = currentMetadata();
        List<String> changed = new ArrayList<>();
        for (String path : initialMetadata.keySet()) {
            RawResource now = current.get(path);
            RawResource known = knownMetadata.get(path);
            if (now == null || now.getMvcc() != known.getMvcc() || !Objects.equals(now.getTs(), known.getTs())) {
                changed.add(path);
            }
        }
        List<String> added = new ArrayList<>(current.keySet());
        added.removeAll(initialMetadata.keySet());
        if (changed.isEmpty() && added.isEmpty()) {
            return;
        }
        UnitOfWork.doInTransactionWithRetry(UnitOfWorkParams.builder()
                .unitName(UnitOfWork.GLOBAL_UNIT)
                .maxRetry(1)
                .useProjectLock(true)
                .processor(() -> {
                    ResourceStore store = ResourceStore.getKylinMetaStore(KylinConfig.getInstanceFromEnv());
                    for (String path : changed) {
                        RawResource now = store.getResource(path, true);
                        UnitOfWork.get().getCopyForWriteItems().add(path);
                        store.checkAndPutResource(path, ByteSource.wrap(initialMetadata.get(path)),
                                now == null ? -1L : now.getMvcc());
                    }
                    for (String path : added) {
                        UnitOfWork.get().getCopyForWriteItems().add(path);
                        store.deleteResource(path);
                    }
                    return null;
                }).build());
        knownMetadata = currentMetadata();
    }

    private static Map<String, RawResource> currentMetadata() {
        ResourceStore store = ResourceStore.getKylinMetaStore(KylinConfig.getInstanceFromEnv());
        Set<String> paths = store.listResourcesRecursively(MetadataType.ALL.name());
        paths.remove(ResourceStore.METASTORE_IMAGE);
        Map<String, RawResource> resources = new HashMap<>();
        for (String path : paths) {
            resources.put(path, store.getResource(path));
        }
        return resources;
    }

    /**
     * EvoMaster 4.0.0 clears its target id maps one after the other when a search starts, so
     * a target that a Kylin background thread reaches in between keeps an id that maps back
     * to nothing, and the search fails once it asks for that target's coverage. A search
     * starts between two resets, so the next reset lets such targets take new ids.
     */
    private static void forgetUnmappedTargetIds() {
        Map<String, Integer> ids = recorderMap("idMapping");
        Map<Integer, String> targets = recorderMap("reversedIdMapping");
        Map<Integer, Double> coverage = recorderMap("maxObjectiveCoverage");
        ids.forEach((target, id) -> {
            if (!targets.containsKey(id) && ids.remove(target, id)) {
                // The new id that reaches this one later must still count as a first sighting.
                coverage.remove(id);
            }
        });
    }

    @SuppressWarnings("unchecked")
    private static <K, V> Map<K, V> recorderMap(String name) {
        try {
            Field field = ObjectiveRecorder.class.getDeclaredField(name);
            field.setAccessible(true);
            return (Map<K, V>) field.get(null);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "org.apache.kylin.";
    }

    @Override
    public String packagesToSkipInstrumentation() {
        // Third-party code shaded under Kylin's prefix; the Guava root also holds three Kylin classes.
        return "org.apache.kylin.guava30.shaded.,"
                + "org.apache.kylin.shaded.curator.,"
                + "org.apache.kylin.shaded.influxdb.,"
                + "org.apache.kylin.shaded.jackson.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        // The reset restores the startup rows, which cleaning the tables a test wrote would drop.
        return Collections.singletonList(new DbSpecification(DatabaseType.H2, database).withDisabledSmartClean());
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        return Collections.singletonList(AuthUtils.getForBasic("admin", "ADMIN", "KYLIN"));
    }

    @Override
    public ProblemInfo getProblemInfo() {
        return new RestProblem(baseUrl() + "/kylin/v2/api-docs?group=v4%20internal", null);
    }

    @Override
    public SutInfoDto.OutputFormat getPreferredOutputFormat() {
        return SutInfoDto.OutputFormat.JAVA_JUNIT_5;
    }
}
