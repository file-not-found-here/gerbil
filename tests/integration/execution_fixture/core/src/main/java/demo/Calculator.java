package demo;

public final class Calculator {

    public int add(int left, int right) {
        return left + right;
    }

    public int divide(int dividend, int divisor) {
        if (divisor == 0) {
            throw new IllegalArgumentException("divisor is zero");
        }
        return dividend / divisor;
    }

    public boolean isPositive(int value) {
        return value > 0;
    }

    // javac copies the finally block onto each way out of the try, and PIT merges the
    // copies' mutants into one with several instruction indexes.
    public int tracked(int left, int right) {
        try {
            return add(left, right);
        } finally {
            calls++;
        }
    }

    private int calls;
}
