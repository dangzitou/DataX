package com.alibaba.datax.common.element;

import com.alibaba.datax.common.exception.DataXException;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.Random;
import org.junit.Test;
import static org.junit.Assert.*;

public class LongColumnRegressionTest {
    @Test public void integerFastPathPreservesExistingConversions() {
        String[][] cases = {{"0", "0"}, {"-12", "-12"}, {"+17", "17"},
                {"18446744073709551615", "18446744073709551615"},
                {"-9223372036854775808", "-9223372036854775808"},
                {"1.23e3", "1230"}, {"-1.99", "-1"}, {"001", "1"}};
        for (String[] pair : cases) {
            LongColumn column = new LongColumn(pair[0]);
            assertEquals(pair[1], column.asString());
            assertEquals(pair[0].length(), column.getByteSize());
        }
        assertNull(new LongColumn((String) null).asString());
    }
    @Test(expected = DataXException.class) public void invalidIntegerStillFails() {
        new LongColumn("12oops");
    }

    @Test public void integerPathsMatchExactDecimalOracle() {
        String[] edges = {"-9223372036854775809", "-9223372036854775808", "9223372036854775807",
                "9223372036854775808", "18446744073709551615", "+00000000000000000001", "-0",
                "９００１２３", "-١٢٣", "1e40", "-12.999", "0.0001", "999999999999999999"};
        for (String value : edges) checkExact(value);
        Random random = new Random(763);
        for (int i = 0; i < 10000; i++) {
            checkExact(Long.toString(random.nextLong()));
            BigInteger value = new BigInteger(i % 513, random);
            checkExact((i % 2 == 0 ? value : value.negate()).toString());
        }
        for (String invalid : new String[]{"", " ", "+", "--1", "0x10", "NaN", "Infinity", "12oops"}) {
            try { new LongColumn(invalid); fail("Accepted invalid number: " + invalid); }
            catch (DataXException expected) { }
        }
    }

    private static void checkExact(String input) {
        BigInteger expected = new BigDecimal(input).toBigInteger();
        LongColumn column = new LongColumn(input);
        assertEquals(BigInteger.class, column.getRawData().getClass());
        assertEquals(expected, column.asBigInteger());
        assertEquals(expected.toString(), column.asString());
        assertEquals(input.length(), column.getByteSize());
        boolean overflow = expected.compareTo(BigInteger.valueOf(Long.MIN_VALUE)) < 0
                || expected.compareTo(BigInteger.valueOf(Long.MAX_VALUE)) > 0;
        assertEquals(overflow, OverFlowUtil.isLongOverflow(expected));
        try {
            assertEquals(expected.longValue(), column.asLong().longValue());
            assertFalse(overflow);
        } catch (DataXException failure) { assertTrue(overflow); }
    }
}
