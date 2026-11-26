import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;
import java.util.stream.Collectors;

import org.pitest.bytecode.analysis.ClassTree;
import org.pitest.mutationtest.build.InterceptorParameters;
import org.pitest.mutationtest.build.InterceptorType;
import org.pitest.mutationtest.build.MutationInterceptor;
import org.pitest.mutationtest.build.MutationInterceptorFactory;
import org.pitest.mutationtest.engine.Mutater;
import org.pitest.mutationtest.engine.MutationDetails;
import org.pitest.mutationtest.engine.MutationIdentifier;
import org.pitest.plugin.Feature;
import org.pitest.plugin.FeatureParameter;

/**
 * PIT feature KEEPLISTED(file[path]): mutates only the mutants the file lists, one
 * "class|method|descriptor|mutator|indexes" per line, as Gerbil reads them from PIT's XML.
 */
public final class GerbilMutantFilter implements MutationInterceptorFactory {

    private static final FeatureParameter FILE = FeatureParameter.named("file")
            .withDescription("File listing the mutants to keep, one per line");

    @Override
    public String description() {
        return "Keeps only the mutants a file lists";
    }

    @Override
    public Feature provides() {
        return Feature.named("KEEPLISTED")
                .withOnByDefault(false)
                .withDescription(description())
                .withParameter(FILE);
    }

    @Override
    public MutationInterceptor createInterceptor(InterceptorParameters parameters) {
        String file = parameters.getString(FILE)
                .orElseThrow(() -> new IllegalArgumentException("KEEPLISTED needs file[path]"));
        try {
            return new KeepListed(
                    new HashSet<>(Files.readAllLines(Paths.get(file), StandardCharsets.UTF_8)));
        } catch (IOException error) {
            throw new UncheckedIOException(error);
        }
    }

    static String id(MutationIdentifier id) {
        return id.getClassName().asJavaName()
                + "|" + id.getLocation().getMethodName()
                + "|" + id.getLocation().getMethodDesc()
                + "|" + id.getMutator()
                + "|" + id.getIndexes().stream().map(String::valueOf).collect(Collectors.joining(","));
    }

    private static final class KeepListed implements MutationInterceptor {

        private final Set<String> listed;

        KeepListed(Set<String> listed) {
            this.listed = listed;
        }

        // PIT applies interceptors in type order; REPORT is last, after the filters
        // that drop or merge mutants, so the listed ids are compared with final ones.
        @Override
        public InterceptorType type() {
            return InterceptorType.REPORT;
        }

        @Override
        public void begin(ClassTree clazz) {
        }

        @Override
        public Collection<MutationDetails> intercept(
                Collection<MutationDetails> mutations, Mutater mutater) {
            return mutations.stream()
                    .filter(mutation -> listed.contains(id(mutation.getId())))
                    .collect(Collectors.toList());
        }

        @Override
        public void end() {
        }
    }
}
