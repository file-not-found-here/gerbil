package org.springframework.web.client;

import org.springframework.http.ResponseEntity;

public class RestTemplate {
    public <T> ResponseEntity<T> getForEntity(String url, Class<T> responseType) {
        return null;
    }
}
