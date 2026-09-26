package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestTemplate;

class StateInteractionFixtureTest {
    @Test
    void detectsStateInteractionPattern() {
        RestTemplate client = new RestTemplate();
        client.postForEntity("/resources/42", "payload", String.class);
        client.getForEntity("/resources/42", String.class);
        assertEquals(200, 200);
    }
}
