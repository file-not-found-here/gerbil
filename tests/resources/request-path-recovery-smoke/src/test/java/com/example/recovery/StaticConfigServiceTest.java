package com.example.recovery;

import io.restassured.RestAssured;
import org.junit.jupiter.api.BeforeEach;

// openrouteservice shape: the deployment mount lives in static RestAssured
// config set by a shared base class, never in any request chain. baseURI is
// dynamic and must not contribute; the literal basePath must.
abstract class StaticConfigServiceTest {

    @BeforeEach
    void setUpRestAssured() {
        RestAssured.baseURI = resolveServerUri();
        RestAssured.basePath = "/ors/v2";
    }

    static String resolveServerUri() {
        return System.getProperty("test.server.uri");
    }
}
