package jakarta.ws.rs.client;

public interface WebTarget {
    WebTarget path(String path);

    Invocation.Builder request();
}
