package com.starrocks.connector.datax.plugin.writer.starrockswriter.manager;

import com.alibaba.datax.common.util.Configuration;
import com.starrocks.connector.datax.plugin.writer.starrockswriter.StarRocksWriterOptions;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.Collections;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class StarRocksStreamLoadVisitorTest {
    @Test public void interruptedLabelPollingFailsAndPreservesInterrupt() throws Exception {
        Configuration config = Configuration.newDefault();
        config.set("column", Collections.singletonList("id"));
        StarRocksStreamLoadVisitor visitor = new StarRocksStreamLoadVisitor(new StarRocksWriterOptions(config));
        Method poll = StarRocksStreamLoadVisitor.class.getDeclaredMethod("checkLabelState", String.class, String.class);
        poll.setAccessible(true);
        int rejected = 0;
        int interruptPreserved = 0;
        for (int attempt = 0; attempt < 4; attempt++) {
            Thread.currentThread().interrupt();
            try {
                // The interrupted sleep must fail before any network access.
                poll.invoke(visitor, "http://127.0.0.1:1", "unconfirmed");
            } catch (InvocationTargetException e) {
                if (e.getCause() instanceof IOException
                        && e.getCause().getCause() instanceof InterruptedException) rejected++;
                if (Thread.currentThread().isInterrupted()) interruptPreserved++;
            } finally {
                Thread.interrupted();
            }
        }
        assertEquals("Unconfirmed load must never return success", 4, rejected);
        assertEquals("Caller must retain cancellation", 4, interruptPreserved);
    }
}

