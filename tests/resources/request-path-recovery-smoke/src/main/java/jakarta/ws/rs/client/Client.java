package jakarta.ws.rs.client;

// Minimal stub so CLDK resolves client-chain receiver types without jars.
public interface Client {
    WebTarget target(String uri);
}
