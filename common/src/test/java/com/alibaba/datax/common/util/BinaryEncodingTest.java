package com.alibaba.datax.common.util;

import org.junit.Test;
import java.util.Base64;
import java.util.Random;
import javax.xml.bind.DatatypeConverter;
import static org.junit.Assert.*;

public class BinaryEncodingTest {
    @Test public void explicitEncodingsPreserveEveryByteAndLength() {
        Random random = new Random(91727);
        for (int size : new int[]{0,1,2,7,8,9,256,4096}) {
            byte[] input = new byte[size];
            random.nextBytes(input);
            if (size != 0) input[0] = 0;
            assertArrayEquals(input, DatatypeConverter.parseHexBinary(BinaryEncoding.HEX.encode(input)));
            assertArrayEquals(input, Base64.getDecoder().decode(BinaryEncoding.BASE64.encode(input)));
            try { BinaryEncoding.REJECT.encode(input); fail("Implicit binary conversion must fail"); }
            catch (IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("binaryEncoding")); }
        }
        assertNull(BinaryEncoding.REJECT.encode(null));
        assertEquals(BinaryEncoding.REJECT, BinaryEncoding.from(null));
        assertEquals(BinaryEncoding.HEX, BinaryEncoding.from("HeX"));
        for (String invalid : new String[]{"", "utf8", "long", "bas64"}) {
            try { BinaryEncoding.from(invalid); fail("Unknown encoding must fail"); }
            catch (IllegalArgumentException expected) { }
        }
    }
}
