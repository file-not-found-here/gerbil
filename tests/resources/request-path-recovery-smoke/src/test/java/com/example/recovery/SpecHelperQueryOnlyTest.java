package com.example.recovery;

import io.restassured.RestAssured;
import io.restassured.response.Response;
import io.restassured.specification.RequestSpecification;
import org.junit.jupiter.api.Test;

// kylo shape: a spec-returning helper receives the resource base path while the
// event itself carries only a query string.
class SpecHelperQueryOnlyTest {

    static final String DATASOURCE_BASE = "/v1/metadata/datasource";

    private RequestSpecification given(String base) {
        RestAssured.basePath = configuredBase() + base;
        return RestAssured.given();
    }

    private String configuredBase() {
        return System.getProperty("proxy.base");
    }

    @Test
    void listsUserDatasources() {
        Response response = given(DATASOURCE_BASE).when().get("?type=UserDatasource");
    }
}
