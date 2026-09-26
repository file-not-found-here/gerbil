package com.example.recovery;

import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

// Helper-parameter shape: the dispatch lives inside a helper whose path
// argument is supplied as a literal at the call site.
class HelperParameterBindingTest {

    private final RestTemplate restTemplate = new RestTemplate();

    private ResponseEntity<String> fetchJson(String url) {
        return restTemplate.getForEntity(url, String.class);
    }

    @Test
    void pingsRegistry() {
        ResponseEntity<String> response = fetchJson("/api/registry/ping");
    }
}
