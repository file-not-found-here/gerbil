package com.example.recovery.app;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

// The /apis mount prefix exists only in deployment config, so tests address the
// endpoint without it; coverage must recover the match via the suffix fallback.
@RestController
@RequestMapping("/apis/registry/v3")
public class RegistrySearchController {

    @GetMapping("/search/artifacts")
    public String searchArtifacts() {
        return "";
    }
}
