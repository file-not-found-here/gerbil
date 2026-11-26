package em.embedded.clouddriver;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

import com.netflix.spinnaker.clouddriver.Main;
import com.netflix.spinnaker.clouddriver.data.task.TaskRepository;
import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Runs Spinnaker's Clouddriver with Redis and SQL off, as its MainSpec does: caches, tasks
 * and scheduling live in memory and no cloud provider is enabled.
 */
public class EmbeddedEvoMasterController extends EmbeddedSutController {

    // Static, since each test class makes its own controller and one service serves the JVM.
    // Restarts keep the first start's port, which EvoMaster 6.2.0's phases after the search
    // keep calling.
    private static int port;
    private static ConfigurableApplicationContext context;

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
        context = new SpringApplicationBuilder(Main.class).run(
                "--server.port=" + port,
                "--redis.enabled=false",
                "--sql.enabled=false",
                "--spring.application.name=clouddriver",
                "--swagger.enabled=true");
        port = Integer.parseInt(context.getEnvironment().getProperty("local.server.port"));
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
        context.close();
    }

    @Override
    public boolean isSutRunning() {
        return context != null && context.isRunning();
    }

    @Override
    public void resetStateOfSUT() {
        // Without providers, the only state is the in-memory task history, which offers no
        // way to clear it.
        TaskRepository tasks = context.getBean(TaskRepository.class);
        try {
            for (String name : List.of("repository", "clientRequestRepository")) {
                Field field = tasks.getClass().getDeclaredField(name);
                field.setAccessible(true);
                ((Map<?, ?>) field.get(tasks)).clear();
            }
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public String getPackagePrefixesToCover() {
        return "com.netflix.spinnaker.clouddriver.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        return null;
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // Fiat is off, so every request is allowed.
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
}
