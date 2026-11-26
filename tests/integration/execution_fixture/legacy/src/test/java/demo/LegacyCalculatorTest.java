package demo;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class LegacyCalculatorTest {

    private final Calculator calculator = new Calculator();

    @Test
    public void adds() {
        assertEquals(4, calculator.add(1, 3));
    }

    @Test
    public void unselected() {
        assertEquals(1, calculator.divide(1, 1));
    }
}
