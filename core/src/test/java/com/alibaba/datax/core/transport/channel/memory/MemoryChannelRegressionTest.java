package com.alibaba.datax.core.transport.channel.memory;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.core.statistics.communication.Communication;
import com.alibaba.datax.core.transport.record.DefaultRecord;
import com.alibaba.datax.core.transport.record.TerminateRecord;
import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.*;

import static org.junit.Assert.*;

public class MemoryChannelRegressionTest {
    private MemoryChannel channel(int capacity, int bytes) {
        Configuration c = Configuration.newDefault();
        c.set("core.container.taskGroup.id", 0);
        c.set("core.transport.channel.capacity", capacity);
        c.set("core.transport.channel.byteCapacity", bytes);
        c.set("core.transport.channel.speed.byte", -1);
        c.set("core.transport.channel.speed.record", -1);
        c.set("core.transport.exchanger.bufferSize", 2);
        MemoryChannel channel = new MemoryChannel(c);
        channel.setCommunication(new Communication());
        return channel;
    }

    private Record record() {
        Record record = new DefaultRecord();
        record.addColumn(new StringColumn("test"));
        return record;
    }

    private ExecutorService worker() {
        return Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r);
            t.setDaemon(true);
            return t;
        });
    }

    @Test public void clearReleasesByteBudget() throws Exception {
        Record r = record();
        MemoryChannel channel = channel(2, r.getMemorySize());
        channel.pushAll(Arrays.asList(r));
        channel.clear();
        ExecutorService worker = worker();
        try {
            worker.submit(() -> channel.pushAll(Arrays.asList(r))).get(1, TimeUnit.SECONDS);
            assertSame(r, channel.pull());
        } finally { worker.shutdownNow(); }
    }

    @Test public void batchesLargerThanCapacityMakeProgress() throws Exception {
        Record r = record();
        MemoryChannel channel = channel(2, r.getMemorySize() * 2);
        ExecutorService worker = worker();
        try {
            Future<?> producer = worker.submit(() -> channel.pushAll(Arrays.asList(r, r, r)));
            // A separate consumer must be able to drain a partial oversized batch.
            ExecutorService consumer = worker();
            try {
                consumer.submit(() -> { for (int i = 0; i < 3; i++) assertSame(r, channel.pull()); })
                        .get(2, TimeUnit.SECONDS);
                producer.get(1, TimeUnit.SECONDS);
            } finally { consumer.shutdownNow(); }
        } finally { worker.shutdownNow(); }
    }

    @Test public void interruptedAcquisitionKeepsOriginalFailure() {
        MemoryChannel channel = channel(2, 1024);
        Thread.currentThread().interrupt();
        try {
            channel.pushAll(Arrays.asList(record()));
            fail("interrupted push succeeded");
        } catch (DataXException expected) {
            assertTrue(Thread.currentThread().isInterrupted());
            assertTrue(expected.getCause() instanceof InterruptedException);
        } finally { Thread.interrupted(); }
    }

    @Test(timeout = 5000) public void mixedSingleAndBatchOperationsPreserveOrder() throws Exception {
        MemoryChannel channel = channel(2, 4096);
        ExecutorService worker = worker();
        try {
            Future<?> producer = worker.submit(() -> {
                for (int i = 0; i < 1000; i++) {
                    Record r = new DefaultRecord();
                    r.addColumn(new StringColumn(String.valueOf(i)));
                    if ((i & 1) == 0) channel.push(r); else channel.pushAll(Arrays.asList(r));
                }
                channel.pushTerminate(TerminateRecord.get());
            });
            List<Record> batch = new ArrayList<Record>();
            int index = 0;
            boolean done = false;
            while (!done) {
                channel.pullAll(batch);
                for (Record r : batch) {
                    if (r instanceof TerminateRecord) done = true;
                    else assertEquals(String.valueOf(index++), r.getColumn(0).asString());
                }
            }
            producer.get(2, TimeUnit.SECONDS);
            assertEquals(1000, index);
            assertTrue(channel.isEmpty());
        } finally { worker.shutdownNow(); }
    }

    @Test(timeout = 2000) public void oversizedRecordFailsBeforeQueuingAnyBatchData() {
        MemoryChannel channel = channel(2, 1);
        try { channel.pushAll(Arrays.asList(record())); fail("oversized record accepted"); }
        catch (IllegalArgumentException expected) { assertTrue(channel.isEmpty()); }
    }

    @Test(timeout = 3000) public void closeWakesBatchReader() throws Exception {
        MemoryChannel channel = channel(2, 1024);
        ExecutorService worker = worker();
        try {
            Future<?> consumer = worker.submit(() -> {
                List<Record> records = new ArrayList<Record>();
                channel.pullAll(records);
                assertEquals(1, records.size());
                assertTrue(records.get(0) instanceof TerminateRecord);
            });
            channel.close();
            consumer.get(1, TimeUnit.SECONDS);
        } finally { worker.shutdownNow(); }
    }
}
