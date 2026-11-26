import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.junit.platform.engine.DiscoverySelector;
import org.junit.platform.engine.FilterResult;
import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.engine.TestSource;
import org.junit.platform.engine.discovery.DiscoverySelectors;
import org.junit.platform.engine.support.descriptor.MethodSource;
import org.junit.platform.launcher.LauncherDiscoveryRequest;
import org.junit.platform.launcher.PostDiscoveryFilter;
import org.junit.platform.launcher.TestExecutionListener;
import org.junit.platform.launcher.TestIdentifier;
import org.junit.platform.launcher.TestPlan;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;

/**
 * Runs the test methods listed in a "class TAB method" file and writes one
 * "status TAB class TAB method" line per test result.
 */
public final class GerbilLauncher {

    private GerbilLauncher() {
    }

    public static void main(String[] args) throws IOException {
        Set<String> selected = new HashSet<>();
        Set<String> topLevelClasses = new LinkedHashSet<>();
        for (String line : Files.readAllLines(Paths.get(args[0]), StandardCharsets.UTF_8)) {
            if (line.isEmpty()) {
                continue;
            }
            selected.add(line);
            String className = line.substring(0, line.indexOf('\t'));
            int nested = className.indexOf('$');
            topLevelClasses.add(nested < 0 ? className : className.substring(0, nested));
        }

        List<DiscoverySelector> selectors = new ArrayList<>();
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        for (String className : topLevelClasses) {
            // An unloadable class would fail discovery for the whole engine; its tests
            // are reported as not run instead.
            try {
                Class.forName(className, false, loader);
            } catch (ClassNotFoundException | LinkageError e) {
                System.err.println("gerbil: cannot load " + className + ": " + e);
                continue;
            }
            selectors.add(DiscoverySelectors.selectClass(className));
        }

        PostDiscoveryFilter filter = descriptor -> {
            TestSource source = descriptor.getSource().orElse(null);
            if (!(source instanceof MethodSource)) {
                return FilterResult.included("not a test method");
            }
            MethodSource method = (MethodSource) source;
            return FilterResult.includedIf(
                    selected.contains(method.getClassName() + "\t" + method.getMethodName()));
        };
        LauncherDiscoveryRequest request = LauncherDiscoveryRequestBuilder.request()
                .selectors(selectors)
                .filters(filter)
                .build();

        try (PrintWriter out = new PrintWriter(
                Files.newBufferedWriter(Paths.get(args[1]), StandardCharsets.UTF_8), true)) {
            LauncherFactory.create().execute(request, new Recorder(out));
        }
        // Tests may leave non-daemon threads (embedded servers, pools) running.
        System.exit(0);
    }

    private static final class Recorder implements TestExecutionListener {

        private final PrintWriter out;
        private TestPlan plan;

        Recorder(PrintWriter out) {
            this.out = out;
        }

        @Override
        public void testPlanExecutionStarted(TestPlan testPlan) {
            plan = testPlan;
        }

        @Override
        public void executionSkipped(TestIdentifier identifier, String reason) {
            recordTree(identifier, "SKIPPED");
        }

        @Override
        public void executionFinished(TestIdentifier identifier, TestExecutionResult result) {
            // A failed container (e.g. a broken @BeforeAll) fails every test beneath it.
            if (identifier.isTest() || result.getStatus() != TestExecutionResult.Status.SUCCESSFUL) {
                recordTree(identifier, result.getStatus().name());
            }
            result.getThrowable().ifPresent(cause -> {
                System.err.println("GerbilLauncher: " + identifier.getUniqueId() + " failed");
                cause.printStackTrace();
            });
        }

        private void recordTree(TestIdentifier identifier, String status) {
            record(identifier, status);
            for (TestIdentifier descendant : plan.getDescendants(identifier)) {
                record(descendant, status);
            }
        }

        private void record(TestIdentifier identifier, String status) {
            if (!identifier.isTest()) {
                return;
            }
            identifier.getSource()
                    .filter(MethodSource.class::isInstance)
                    .map(MethodSource.class::cast)
                    .ifPresent(method -> out.println(
                            status + "\t" + method.getClassName() + "\t" + method.getMethodName()));
        }
    }
}
