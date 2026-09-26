package com.alibaba.datax.common.util;

import java.net.ProtocolException;
import java.util.Map;

/** Validate the row counters of a successful StarRocks/Doris Stream Load batch. */
public final class StreamLoadResponseValidator {
    private StreamLoadResponseValidator() {}

    public static void validate(Map<String, Object> response, int expectedRows, String label)
            throws ProtocolException {
        requireCount(response, "NumberTotalRows", expectedRows, label);
        requireCount(response, "NumberLoadedRows", expectedRows, label);
        requireCount(response, "NumberFilteredRows", 0, label);
        requireCount(response, "NumberUnselectedRows", 0, label);
    }

    private static void requireCount(Map<String, Object> response, String key, long expected,
                                     String label) throws ProtocolException {
        Object actual = response.get(key);
        try {
            // Do not use Number.longValue(): fractions and overflowing integers can look valid.
            if (Long.parseLong(String.valueOf(actual)) == expected) {
                return;
            }
        } catch (NumberFormatException ignored) {
            // Missing or malformed counters cannot prove a complete load.
        }
        throw new ProtocolException("Incomplete Stream Load: label=" + label + ", " + key
                + " expected=" + expected + ", actual=" + actual
                + ". The batch may already be committed; automatic retry is disabled.");
    }
}
