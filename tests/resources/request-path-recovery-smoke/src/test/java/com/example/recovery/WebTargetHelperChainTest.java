package com.example.recovery;

import jakarta.ws.rs.client.Client;
import jakarta.ws.rs.client.ClientBuilder;
import jakarta.ws.rs.client.WebTarget;
import org.junit.jupiter.api.Test;

// ejbca shape: the path literal rides the WebTarget-returning helper while the
// dispatch event sits on the derived Invocation.Builder.
class WebTargetHelperChainTest {

    static WebTarget newRequest(String uriPath) {
        Client client = ClientBuilder.newClient();
        return client.target(resolveBaseUrl() + uriPath);
    }

    static WebTarget apiRequest(String uriPath) {
        Client client = ClientBuilder.newClient();
        return client.target(resolveBaseUrl()).path("/api").path(uriPath);
    }

    static String resolveBaseUrl() {
        return System.getProperty("test.base.url");
    }

    @Test
    void getsWidgetCount() {
        Object response = newRequest("/v2/widget/count?isActive=true").request().get();
    }

    @Test
    void getsWidgetSummary() {
        Object response = apiRequest("/v2/widget/summary").request().get();
    }

    @Test
    void listsWidgetTags() {
        Client client = ClientBuilder.newClient();
        Object response =
                client.target(resolveBaseUrl())
                        .path("/api")
                        .path("/v2/widget/tags")
                        .request()
                        .get();
    }
}
