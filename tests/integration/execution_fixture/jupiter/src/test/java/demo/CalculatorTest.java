package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class CalculatorTest {

    private final Calculator calculator = new Calculator();

    @Test
    void adds() {
        assertEquals(5, calculator.add(2, 3));
    }

    @ParameterizedTest
    @ValueSource(ints = {1, 7})
    void recognizesPositives(int value) {
        assertTrue(calculator.isPositive(value));
    }

    @Test
    void fails() {
        fail("always fails");
    }

    @Test
    @Disabled
    void disabled() {
    }

    @Test
    void unselected() {
        assertEquals(0, calculator.add(0, 0));
        assertEquals(3, calculator.tracked(1, 2));
    }

    @Nested
    class Division {

        @Test
        void divides() {
            assertEquals(2, calculator.divide(6, 3));
        }

        @Test
        void rejectsZero() {
            assertThrows(IllegalArgumentException.class, () -> calculator.divide(1, 0));
        }
    }
}
