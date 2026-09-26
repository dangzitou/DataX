package com.alibaba.datax.core.job;

import com.alibaba.datax.common.spi.Reader;
import com.alibaba.datax.common.spi.Writer;
import com.alibaba.datax.common.util.Configuration;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Test;
import static org.junit.Assert.*;
import static org.mockito.Mockito.*;

public class JobContainerCleanupTest {
    @Test public void readerClosesEvenWhenWriterCleanupFails() throws Exception {
        Method destroy = JobContainer.class.getDeclaredMethod("destroy");
        destroy.setAccessible(true);
        Field readerField = JobContainer.class.getDeclaredField("jobReader");
        Field writerField = JobContainer.class.getDeclaredField("jobWriter");
        readerField.setAccessible(true);
        writerField.setAccessible(true);
        AtomicInteger cleaned = new AtomicInteger();
        int preserved = 0, suppressed = 0, cleared = 0;
        for (boolean readerFails : new boolean[] {false, true}) {
            for (int attempt = 0; attempt < 4; attempt++) {
                RuntimeException writerFailure = new RuntimeException("writer cleanup");
                RuntimeException readerFailure = new RuntimeException("reader cleanup");
                Writer.Job writer = mock(Writer.Job.class);
                Reader.Job reader = mock(Reader.Job.class);
                doThrow(writerFailure).when(writer).destroy();
                doAnswer(invocation -> {
                    cleaned.incrementAndGet();
                    if (readerFails) throw readerFailure;
                    return null;
                }).when(reader).destroy();
                JobContainer job = new JobContainer(Configuration.newDefault());
                readerField.set(job, reader);
                writerField.set(job, writer);
                try {
                    destroy.invoke(job);
                } catch (InvocationTargetException e) {
                    if (e.getCause() == writerFailure) preserved++;
                    if (writerFailure.getSuppressed().length == 1
                            && writerFailure.getSuppressed()[0] == readerFailure) suppressed++;
                }
                if (readerField.get(job) == null && writerField.get(job) == null) cleared++;
            }
        }
        assertEquals("Reader resources must always be released", 8, cleaned.get());
        assertEquals(8, preserved);
        assertEquals(4, suppressed);
        assertEquals(8, cleared);
    }
}
