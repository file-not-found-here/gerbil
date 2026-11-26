package em.embedded.kogito;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

import org.evomaster.client.java.controller.EmbeddedSutController;
import org.evomaster.client.java.controller.InstrumentedSutStarter;
import org.evomaster.client.java.controller.api.dto.SutInfoDto;
import org.evomaster.client.java.controller.api.dto.auth.AuthenticationDto;
import org.evomaster.client.java.controller.problem.ProblemInfo;
import org.evomaster.client.java.controller.problem.RestProblem;
import org.evomaster.client.java.sql.DbSpecification;
import org.kie.kogito.integrationtests.springboot.KogitoSpringbootApplication;
import org.kie.kogito.process.MutableProcessInstances;
import org.kie.kogito.process.ProcessInstance;
import org.kie.kogito.process.ProcessInstanceReadMode;
import org.kie.kogito.process.ProcessInstances;
import org.kie.kogito.process.Processes;
import org.kie.kogito.usertask.UserTaskInstances;
import org.kie.kogito.usertask.UserTasks;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Runs the processes integration-test application, whose BPMN processes and add-ons stand
 * for Kogito's REST surface. Process instances live in memory, so a reset removes them.
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
        context = SpringApplication.run(KogitoSpringbootApplication.class,
                "--server.port=" + port,
                "--springdoc.api-docs.version=openapi_3_0");
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
        Processes processes = context.getBean(Processes.class);
        for (String processId : processes.processIds()) {
            ProcessInstances<?> instances = processes.processById(processId).instances();
            // Aborting cancels an instance's timers and work items, which removing does not.
            for (ProcessInstance<?> instance : instances.stream(ProcessInstanceReadMode.MUTABLE).toList()) {
                try {
                    instance.abort();
                } catch (RuntimeException alreadyEnded) {
                    // Removed below either way.
                }
            }
            if (instances instanceof MutableProcessInstances<?> mutable) {
                instances.stream().map(ProcessInstance::id).toList().forEach(mutable::remove);
            }
        }
        clearUserTasks(context.getBean(UserTasks.class).instances());
    }

    /** The in-memory user task store can neither list nor clear all its tasks. */
    private static void clearUserTasks(UserTaskInstances instances) {
        try {
            Field tasks = instances.getClass().getDeclaredField("userTaskInstances");
            tasks.setAccessible(true);
            ((Map<?, ?>) tasks.get(instances)).clear();
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public String getPackagePrefixesToCover() {
        // The generated REST resources and models, and the runtime they drive.
        return "org.kie.kogito.,org.jbpm.,org.acme.,com.example.";
    }

    @Override
    public List<DbSpecification> getDbSpecifications() {
        return null;
    }

    @Override
    public List<AuthenticationDto> getInfoForAuthentication() {
        // User tasks take their user and groups as query parameters.
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
