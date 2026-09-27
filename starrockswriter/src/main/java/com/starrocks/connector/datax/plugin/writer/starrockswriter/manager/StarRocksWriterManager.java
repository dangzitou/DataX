package com.starrocks.connector.datax.plugin.writer.starrockswriter.manager;

import org.apache.commons.lang3.concurrent.BasicThreadFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.ProtocolException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import com.google.common.base.Strings;
import com.starrocks.connector.datax.plugin.writer.starrockswriter.StarRocksWriterOptions;

public class StarRocksWriterManager {
    
    private static final Logger LOG = LoggerFactory.getLogger(StarRocksWriterManager.class);

    private final StarRocksStreamLoadVisitor starrocksStreamLoadVisitor;
    private final StarRocksWriterOptions writerOptions;

    private final List<byte[]> buffer = new ArrayList<>();
    private int batchCount = 0;
    private long batchSize = 0;
    private volatile boolean closed = false;
    private volatile Throwable flushException;
    private final LinkedBlockingDeque<StarRocksFlushTuple> flushQueue;
    private final ScheduledThreadPoolExecutor scheduler;
    private Thread flushThread;
    private volatile boolean stopped;
    private ScheduledFuture<?> scheduledFuture;

    public StarRocksWriterManager(StarRocksWriterOptions writerOptions) {
        this.writerOptions = writerOptions;
        this.starrocksStreamLoadVisitor = new StarRocksStreamLoadVisitor(writerOptions);
        flushQueue = new LinkedBlockingDeque<>(writerOptions.getFlushQueueLength()); 
        this.scheduler = new ScheduledThreadPoolExecutor(1, new BasicThreadFactory.Builder()
                .namingPattern("starrocks-interval-flush").daemon(true).build());
        this.scheduler.setRemoveOnCancelPolicy(true);
        this.startScheduler();
        this.startAsyncFlushing();
    }

    public void startScheduler() {
        synchronized (scheduler) {
            stopScheduler();
            if (closed || flushException != null) return;
            scheduledFuture = scheduler.schedule(() -> {
                synchronized (StarRocksWriterManager.this) {
                    if (!closed) {
                        try {
                            if (batchCount == 0) startScheduler();
                            else flush(createBatchLabel(), false);
                        } catch (Throwable e) {
                            if (flushException == null) flushException = e;
                            abort();
                            if (e instanceof Error) throw (Error) e;
                        }
                    }
                }
            }, writerOptions.getFlushInterval(), TimeUnit.MILLISECONDS);
        }
    }

    public void stopScheduler() {
        synchronized (scheduler) {
            if (scheduledFuture != null) scheduledFuture.cancel(false);
        }
    }

    public final void writeRecord(String record) throws IOException {
        writeRecord(record.getBytes(StandardCharsets.UTF_8));
    }

    public final synchronized void writeRecord(byte[] bts) throws IOException {
        checkFlushException();
        if (closed) throw new IOException("Writer is closed");
        try {
            buffer.add(bts);
            batchCount++;
            batchSize += bts.length;
            if (batchCount >= writerOptions.getBatchRows() || batchSize >= writerOptions.getBatchSize()) {
                String label = createBatchLabel();
                if (LOG.isDebugEnabled()) {
                    LOG.debug(String.format("StarRocks buffer Sinking triggered: rows[%d] label[%s].", batchCount, label));
                }
                flush(label, false);
            }
        } catch (Exception e) {
            throw new IOException("Writing records to StarRocks failed.", e);
        }
    }

    public synchronized void flush(String label, boolean waitUtilDone) throws Exception {
        checkFlushException();
        if (batchCount == 0) {
            if (waitUtilDone) {
                waitAsyncFlushingDone();
            }
            return;
        }
        enqueue(new StarRocksFlushTuple(label, batchSize,  new ArrayList<>(buffer)));
        if (waitUtilDone) {
            // wait the last flush
            waitAsyncFlushingDone();
        }
        buffer.clear();
        batchCount = 0;
        batchSize = 0;
    }
    
    public synchronized void close() {
        try {
            if (!closed) {
                closed = true;
                flush(createBatchLabel(), true);
            }
            checkFlushException();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Closing StarRocks writer was interrupted", e);
        } catch (Exception e) {
            throw new RuntimeException("Writing records to StarRocks failed", e);
        } finally {
            abort();
        }
    }

    /** Stop pending work after task failure; an already sent load may have committed. */
    public void abort() {
        if (!closed && flushException == null) flushException = new IOException("Writer aborted");
        closed = true;
        stopped = true;
        stopScheduler();
        scheduler.shutdownNow();
        flushThread.interrupt();
        flushQueue.clear();
    }

    private void enqueue(StarRocksFlushTuple tuple) throws Exception {
        do {
            checkFlushException();
            if (stopped) throw new IOException("Writer is stopped");
        } while (!flushQueue.offer(tuple, 100, TimeUnit.MILLISECONDS));
        checkFlushException();
        if (stopped) throw new IOException("Writer is stopped");
    }

    public String createBatchLabel() {
        StringBuilder sb = new StringBuilder();
        if (!Strings.isNullOrEmpty(writerOptions.getLabelPrefix())) {
            sb.append(writerOptions.getLabelPrefix());
        }
        return sb.append(UUID.randomUUID().toString())
            .toString();
    }

    private void startAsyncFlushing() {
        // start flush thread
        flushThread = new Thread(new Runnable(){
            public void run() {
                while (!stopped) {
                    try {
                        asyncFlush();
                    } catch (Throwable e) {
                        if (!stopped && flushException == null) flushException = e;
                        abort();
                        if (e instanceof Error) throw (Error) e;
                    }
                }
            }   
        });
        flushThread.setDaemon(true);
        flushThread.start();
    }

    private void waitAsyncFlushingDone() throws Exception {
        // wait previous flushings
        for (int i = 0; i <= writerOptions.getFlushQueueLength(); i++) {
            enqueue(new StarRocksFlushTuple("", 0l, null));
        }
        checkFlushException();
    }

    private void asyncFlush() throws Exception {
        StarRocksFlushTuple flushData = flushQueue.take();
        if (stopped || flushException != null || Strings.isNullOrEmpty(flushData.getLabel())) {
            return;
        }
        stopScheduler();
        if (LOG.isDebugEnabled()) {
            LOG.debug(String.format("Async stream load: rows[%d] bytes[%d] label[%s].", flushData.getRows().size(), flushData.getBytes(), flushData.getLabel()));
        }
        for (int i = 0; i <= writerOptions.getMaxRetries(); i++) {
            if (stopped) throw new IOException("Writer is stopped");
            try {
                // flush to StarRocks with stream load
                starrocksStreamLoadVisitor.doStreamLoad(flushData);
                LOG.info(String.format("Async stream load finished: label[%s].", flushData.getLabel()));
                startScheduler();
                break;
            } catch (Exception e) {
                // A committed partial load must not turn into success via Label Already Exists.
                if (e instanceof ProtocolException) {
                    throw e;
                }
                LOG.warn("Failed to flush batch data to StarRocks, retry times = {}", i, e);
                if (i >= writerOptions.getMaxRetries()) {
                    throw new IOException(e);
                }
                if (e instanceof StarRocksStreamLoadFailedException && ((StarRocksStreamLoadFailedException)e).needReCreateLabel()) {
                    String newLabel = createBatchLabel();
                    LOG.warn(String.format("Batch label changed from [%s] to [%s]", flushData.getLabel(), newLabel));
                    flushData.setLabel(newLabel);
                }
                try {
                    Thread.sleep(1000l * Math.min(i + 1, 10));
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                    throw new IOException("Unable to flush, interrupted while doing another attempt", e);
                }
            }
        }
    }

    private void checkFlushException() {
        if (flushException != null) {
            throw new RuntimeException("Writing records to StarRocks failed.", flushException);
        }
    }
}
