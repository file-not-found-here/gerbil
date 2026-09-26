package io.restassured.specification;

import io.restassured.response.Response;

public interface RequestSpecification {
    RequestSpecification when();

    Response get(String path);
}
