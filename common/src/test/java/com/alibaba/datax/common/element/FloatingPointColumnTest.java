package com.alibaba.datax.common.element;

import com.alibaba.datax.common.exception.DataXException;
import org.junit.Test;

import java.math.BigDecimal;
import java.util.Random;

import static org.junit.Assert.*;

public class FloatingPointColumnTest {
    private void sameBits(double expected, double actual) {
        assertEquals(Double.doubleToLongBits(expected), Double.doubleToLongBits(actual));
    }

    @Test public void acceptedSpecialTextConvertsConsistently() {
        for (String text : new String[]{"Infinity", "+Infinity", "infinity", "+INFINITY", "-Infinity", "-infinity", "NaN", "nan"}) {
            DoubleColumn value = new DoubleColumn(text);
            sameBits(text.equalsIgnoreCase("nan") ? Double.NaN :
                    text.startsWith("-") ? Double.NEGATIVE_INFINITY : Double.POSITIVE_INFINITY, value.asDouble());
            assertEquals(text, value.asString());
            assertEquals(text.length(), value.getByteSize());
            try { value.asBigDecimal(); fail("A special float is not a decimal"); }
            catch (DataXException expected) { }
        }
    }

    @Test public void boxedFloatingValuesRetainSpecialValuesAndNegativeZero() {
        for (double value : new double[]{-0.0, 0.0, Double.NaN, Double.POSITIVE_INFINITY,
                Double.NEGATIVE_INFINITY, Double.MIN_VALUE, -Double.MIN_VALUE, Double.MAX_VALUE, -Double.MAX_VALUE}) {
            sameBits(value, new DoubleColumn(value).asDouble());
        }
        for (float value : new float[]{-0.0f, 0.0f, Float.NaN, Float.POSITIVE_INFINITY,
                Float.NEGATIVE_INFINITY, Float.MIN_VALUE, -Float.MIN_VALUE, Float.MAX_VALUE}) {
            assertEquals(Float.floatToIntBits(value), Float.floatToIntBits(new DoubleColumn(value).asDouble().floatValue()));
        }
        assertEquals("-0.0", new DoubleColumn(-0.0).asString());
        assertEquals("-0.0", new DoubleColumn(-0.0f).asString());
        assertNull(new DoubleColumn((Double) null).asDouble());
        assertNull(new DoubleColumn((Float) null).asDouble());
    }

    @Test public void signedZeroTextRetainsSignInBothColumnTypes() {
        for (String text : new String[]{"-0", "-0.0", "-0e18", "-0e-999", "0", "+0.00"}) {
            for (Column value : new Column[]{new DoubleColumn(text), new StringColumn(text)}) {
                sameBits(text.startsWith("-") ? -0.0 : 0.0, value.asDouble());
                assertEquals(text, value.asString());
                assertEquals(Long.valueOf(0), value.asLong());
            }
        }
    }

    @Test public void finiteFormattingPrecisionAndRangeChecksRemainIntact() {
        assertEquals("0.00000010", new DoubleColumn(1e-7).asString());
        assertEquals("10000000", new DoubleColumn(1e7f).asString());
        for (String text : new String[]{"12345678901234567890.123456789012345678", "1.2300", "-0.00001", "１２.５"}) {
            DoubleColumn value = new DoubleColumn(text);
            assertEquals(text, value.asString());
            assertEquals(new BigDecimal(text), value.asBigDecimal());
        }
        for (String text : new String[]{"1e309", "-1e309", "1e-325", "-1e-325"}) {
            for (Column value : new Column[]{new DoubleColumn(text), new StringColumn(text)}) {
                try { value.asDouble(); fail("Out of range: " + text); }
                catch (DataXException expected) { }
            }
        }
        for (String text : new String[]{"", "not-a-number", "1.2.3", "0x1p0", "1d", " Infinity"}) {
            try { new DoubleColumn(text); fail("Invalid numeric input: " + text); }
            catch (DataXException expected) { }
        }
    }

    @Test public void seededFiniteBitPatternsSurviveBoxedRoundTrips() {
        Random random = new Random(9272026);
        for (int i = 0; i < 4096; i++) {
            double value = Double.longBitsToDouble(random.nextLong());
            if (!Double.isNaN(value)) sameBits(value, new DoubleColumn(value).asDouble());
            float single = Float.intBitsToFloat(random.nextInt());
            if (!Float.isNaN(single)) assertEquals(Float.floatToIntBits(single),
                    Float.floatToIntBits(new DoubleColumn(single).asDouble().floatValue()));
        }
    }
}
