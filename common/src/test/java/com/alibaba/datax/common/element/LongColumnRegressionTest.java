package com.alibaba.datax.common.element;

import com.alibaba.datax.common.exception.DataXException;
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
}
