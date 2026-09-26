package com.example.recovery;

import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

// WeEvent shape: the request URL splices a runtime port between a host-only
// literal and the path literal.
class HostConcatRestTemplateTest {

    private int port = 8080;

    @Test
    void listsTopics() {
        RestTemplate restTemplate = new RestTemplate();
        ResponseEntity<String> response = restTemplate.getForEntity(
            "http://localhost:" + port + "/broker/rest/list", String.class);
    }

    @Test
    void searchesArtifactsUnderConfiguredMount() {
        RestTemplate restTemplate = new RestTemplate();
        ResponseEntity<String> response = restTemplate.getForEntity(
            "http://localhost:" + port + "/registry/v3/search/artifacts", String.class);
    }
}
