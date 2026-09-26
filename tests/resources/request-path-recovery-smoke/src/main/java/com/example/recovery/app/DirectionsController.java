package com.example.recovery.app;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/ors/v2/directions")
public class DirectionsController {

    @GetMapping("/{profile}/json")
    public String directionsJson() {
        return "";
    }
}
