package com.alibaba.datax.common.util;

import org.junit.Test;

import java.math.BigInteger;
import java.net.ProtocolException;
import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.*;

public class StreamLoadResponseValidatorTest {
    @Test
    public void requiresEveryExactCounterWithoutNumericTruncation() throws Exception {
        Map<String, Object> response = new HashMap<String, Object>();
        response.put("NumberTotalRows", 3);
        response.put("NumberLoadedRows", 3L);
        response.put("NumberFilteredRows", "0");
        response.put("NumberUnselectedRows", BigInteger.ZERO);
        StreamLoadResponseValidator.validate(response, 3, "test-label");
        for (String key : response.keySet().toArray(new String[0])) {
            Object original = response.get(key);
            for (Object invalid : new Object[]{null, -1, 4, 0.1, true, "bad",
                    new BigInteger("18446744073709551619")}) {
                response.put(key, invalid);
                assertRejected(response, key);
            }
            response.remove(key);
            assertRejected(response, key);
            response.put(key, original);
        }
    }

    private void assertRejected(Map<String, Object> response, String key) throws Exception {
        try {
            StreamLoadResponseValidator.validate(response, 3, "test-label");
            fail("Accepted invalid " + key + ": " + response);
        } catch (ProtocolException expected) {
            assertTrue(expected.getMessage().contains(key));
            assertTrue(expected.getMessage().contains("test-label"));
            assertTrue(expected.getMessage().contains("automatic retry is disabled"));
        }
    }

    @Test
    public void requiresOriginalCountersEvenAfterLabelCommit() throws Exception {
        Map<String, Object> response = new HashMap<String, Object>();
        response.put("Status", "Publish Timeout");
        response.put("NumberTotalRows", 3);
        response.put("NumberLoadedRows", 3);
        response.put("NumberFilteredRows", 0);
        response.put("NumberUnselectedRows", 0);
        StreamLoadResponseValidator.validate(response, 3, "test-label");
        response.put("NumberLoadedRows", 2);
        assertRejected(response, "NumberLoadedRows");
        response.put("NumberLoadedRows", 3);
        response.put("Status", "Label Already Exists");
        assertRejected(response, "Unverified Stream Load");
        response.clear();
        response.put("Status", "Label Already Exists");
        assertRejected(response, "Unverified Stream Load");
    }
}
