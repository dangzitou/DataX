package com.alibaba.datax.common.util;

import java.util.Base64;
import java.util.Locale;

/** Explicit, reversible wire representation for binary Stream Load fields. */
public enum BinaryEncoding {
    REJECT, HEX, BASE64;

    public static BinaryEncoding from(String value) {
        if (value == null) return REJECT;
        try { return valueOf(value.trim().toUpperCase(Locale.ROOT)); }
        catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("binaryEncoding must be reject, hex or base64", error);
        }
    }

    public String encode(byte[] bytes) {
        if (bytes == null) return null;
        if (this == REJECT) throw new IllegalArgumentException(
                "Raw BYTES require explicit binaryEncoding=hex or base64 and a matching destination representation; "
                + "numeric BIT fields must be explicitly cast to an integer in the source query");
        if (this == BASE64) return Base64.getEncoder().encodeToString(bytes);
        char[] encoded = new char[Math.multiplyExact(bytes.length, 2)];
        final String digits = "0123456789abcdef";
        for (int i = 0; i < bytes.length; i++) {
            encoded[2 * i] = digits.charAt((bytes[i] & 255) >>> 4);
            encoded[2 * i + 1] = digits.charAt(bytes[i] & 15);
        }
        return new String(encoded);
    }
}
