package com.alibaba.datax.plugin.writer.doriswriter;

import com.alibaba.datax.common.util.Configuration;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.Collections;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class DorisStreamLoadObserverTest {
    @Test public void interruptedLabelPollingFailsAndPreservesInterrupt() throws Exception {
        Configuration config = Configuration.newDefault();
        config.set("column", Collections.singletonList("id"));
        DorisStreamLoadObserver visitor = new DorisStreamLoadObserver(new Keys(config));
        Method poll = DorisStreamLoadObserver.class.getDeclaredMethod("checkStreamLoadState", String.class, String.class);
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

