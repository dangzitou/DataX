package com.alibaba.datax.core.transport.channel.memory;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.core.transport.channel.Channel;
import com.alibaba.datax.core.transport.record.TerminateRecord;
import com.alibaba.datax.core.util.FrameworkErrorCode;
import com.alibaba.datax.core.util.container.CoreConstant;

import java.util.ArrayDeque;
import java.util.Collection;
import java.util.Collections;
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;

/** Bounded FIFO; one lock protects the queue and its byte budget together. */
public class MemoryChannel extends Channel {
    private final int bufferSize;
    private long memoryBytes;
    private final ArrayDeque<Record> queue;
    private final ReentrantLock lock = new ReentrantLock();
    private final Condition notSufficient = lock.newCondition();
    private final Condition notEmpty = lock.newCondition();

    public MemoryChannel(Configuration configuration) {
        super(configuration);
        bufferSize = configuration.getInt(CoreConstant.DATAX_CORE_TRANSPORT_EXCHANGER_BUFFERSIZE);
        if (bufferSize <= 0 || byteCapacity <= 0) {
            throw new IllegalArgumentException("bufferSize and byteCapacity must be positive");
        }
        queue = new ArrayDeque<Record>(getCapacity());
    }

    @Override public void close() {
        super.close();
        doPush(TerminateRecord.get());
    }

    @Override public void clear() {
        lock.lock();
        try {
            queue.clear();
            memoryBytes = 0;
            notSufficient.signalAll();
        } finally { lock.unlock(); }
    }

    @Override protected void doPush(Record record) {
        doPushAll(Collections.singletonList(record));
    }

    @Override protected void doPushAll(Collection<Record> records) {
        if (records.isEmpty()) return;
        long bytes = 0;
        for (Record record : records) {
            if (record.getMemorySize() > byteCapacity) {
                throw new IllegalArgumentException("A record exceeds channel byteCapacity");
            }
            bytes += record.getMemorySize();
        }
        if (bytes > byteCapacity || records.size() > capacity) {
            // Oversized batches must allow the consumer to drain partial progress.
            for (Record record : records) doPush(record);
            return;
        }
        long start = System.nanoTime();
        acquire();
        try {
            while (memoryBytes + bytes > byteCapacity || queue.size() + records.size() > capacity) {
                notSufficient.await();
            }
            queue.addAll(records);
            memoryBytes += bytes;
            notEmpty.signalAll();
            waitWriterTime += System.nanoTime() - start;
        } catch (InterruptedException e) {
            throw interrupted(e);
        } finally { lock.unlock(); }
    }

    @Override protected Record doPull() {
        long start = System.nanoTime();
        acquire();
        try {
            while (queue.isEmpty()) notEmpty.await();
            Record record = queue.removeFirst();
            memoryBytes -= record.getMemorySize();
            notSufficient.signalAll();
            waitReaderTime += System.nanoTime() - start;
            return record;
        } catch (InterruptedException e) {
            throw interrupted(e);
        } finally { lock.unlock(); }
    }

    @Override protected void doPullAll(Collection<Record> records) {
        records.clear();
        long start = System.nanoTime();
        acquire();
        try {
            while (queue.isEmpty()) notEmpty.await();
            int count = Math.min(bufferSize, queue.size());
            for (int i = 0; i < count; i++) {
                Record record = queue.removeFirst();
                records.add(record);
                memoryBytes -= record.getMemorySize();
            }
            notSufficient.signalAll();
            waitReaderTime += System.nanoTime() - start;
        } catch (InterruptedException e) {
            throw interrupted(e);
        } finally { lock.unlock(); }
    }

    private void acquire() {
        try { lock.lockInterruptibly(); }
        catch (InterruptedException e) { throw interrupted(e); }
    }

    private static DataXException interrupted(InterruptedException e) {
        Thread.currentThread().interrupt();
        return DataXException.asDataXException(FrameworkErrorCode.RUNTIME_ERROR, e);
    }

    @Override public int size() {
        lock.lock();
        try { return queue.size(); }
        finally { lock.unlock(); }
    }

    @Override public boolean isEmpty() { return size() == 0; }
}
