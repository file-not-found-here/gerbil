package demo;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

class BrokenSetupTest {

    @BeforeAll
    static void breaks() {
        throw new IllegalStateException("broken setup");
    }

    @Test
    void neverRuns() {
    }
}
