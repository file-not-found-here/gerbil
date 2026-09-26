package com.example.recovery;

import io.restassured.response.Response;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;

class StaticBasePathDirectionsTest extends StaticConfigServiceTest {

    @Test
    void getsDirectionsJson() {
        Response response = given().when().get("/directions/driving-car/json");
    }
}
