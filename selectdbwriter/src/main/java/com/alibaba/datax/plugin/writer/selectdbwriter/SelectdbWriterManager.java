package com.alibaba.datax.plugin.writer.selectdbwriter;

import com.google.common.base.Strings;
import org.apache.commons.lang3.concurrent.BasicThreadFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public class SelectdbWriterManager {

    private static final Logger LOG = LoggerFactory.getLogger(SelectdbWriterManager.class);

    private final SelectdbCopyIntoObserver visitor;
    private final Keys options;
    private final List<byte[]> buffer = new ArrayList<>();
    private int batchCount = 0;
    private long batchSize = 0;
    private volatile boolean closed = false;
    private volatile Exception flushException;
    private final LinkedBlockingDeque<WriterTuple> flushQueue;
    private final ScheduledThreadPoolExecutor scheduler;
    private Thread flushThread;
    private volatile boolean stopped;
    private ScheduledFuture<?> scheduledFuture;

    public SelectdbWriterManager(Keys options) {
        this.options = options;
        this.visitor = new SelectdbCopyIntoObserver(options);
        flushQueue = new LinkedBlockingDeque<>(options.getFlushQueueLength());
        this.scheduler = new ScheduledThreadPoolExecutor(1, new BasicThreadFactory.Builder()
                .namingPattern("selectdb-interval-flush").daemon(true).build());
        this.scheduler.setRemoveOnCancelPolicy(true);
        this.startScheduler();
        this.startAsyncFlushing();
    }

    public void startScheduler() {
        synchronized (scheduler) {
            stopScheduler();
            if (closed || flushException != null) return;
            scheduledFuture = scheduler.schedule(() -> {
                synchronized (SelectdbWriterManager.this) {
                    if (!closed) {
                        try {
                            if (batchCount == 0) startScheduler();
                            else flush(createBatchLabel(), false);
                        } catch (Exception e) {
                            if (flushException == null) flushException = e;
                            abort();
                        }
                    }
                }
            }, options.getFlushInterval(), TimeUnit.MILLISECONDS);
        }
    }

    public void stopScheduler() {
        synchronized (scheduler) {
            if (scheduledFuture != null) scheduledFuture.cancel(false);
        }
    }

    public final synchronized void writeRecord(String record) throws IOException {
        checkFlushException();
        if (closed) throw new IOException("Writer is closed");
        try {
            byte[] bts = record.getBytes(StandardCharsets.UTF_8);
            buffer.add(bts);
            batchCount++;
            batchSize += bts.length;
            if (batchCount >= options.getBatchRows() || batchSize >= options.getBatchSize()) {
                String label = createBatchLabel();
                if(LOG.isDebugEnabled()){
                    LOG.debug(String.format("buffer Sinking triggered: rows[%d] label [%s].", batchCount, label));
                }
                flush(label, false);
            }
        } catch (Exception e) {
            throw new SelectdbWriterException("Writing records to selectdb failed.", e);
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
        enqueue(new WriterTuple(label, batchSize, new ArrayList<>(buffer)));
        if (waitUtilDone) {
            // wait the last flush
            waitAsyncFlushingDone();
        }
        buffer.clear();
        batchCount = 0;
        batchSize = 0;
    }

    public synchronized void close() throws IOException {
        try {
            if (!closed) {
                closed = true;
                flush(createBatchLabel(), true);
            }
            checkFlushException();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Closing Selectdb writer was interrupted", e);
        } catch (Exception e) {
            throw new RuntimeException("Writing records to Selectdb failed", e);
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

    private void enqueue(WriterTuple tuple) throws Exception {
        do {
            checkFlushException();
            if (stopped) throw new IOException("Writer is stopped");
        } while (!flushQueue.offer(tuple, 100, TimeUnit.MILLISECONDS));
        checkFlushException();
        if (stopped) throw new IOException("Writer is stopped");
    }

    public String createBatchLabel() {
        StringBuilder sb = new StringBuilder();
        if (!Strings.isNullOrEmpty(options.getLabelPrefix())) {
            sb.append(options.getLabelPrefix());
        }
        return sb.append(UUID.randomUUID().toString())
                .toString();
    }

    private void startAsyncFlushing() {
        // start flush thread
        flushThread = new Thread(new Runnable() {
            public void run() {
                while (!stopped) {
                    try {
                        asyncFlush();
                    } catch (Exception e) {
                        if (!stopped && flushException == null) flushException = e;
                        abort();
                    }
                }
            }
        });
        flushThread.setDaemon(true);
        flushThread.start();
    }

    private void waitAsyncFlushingDone() throws Exception {
        // wait previous flushings
        for (int i = 0; i <= options.getFlushQueueLength(); i++) {
            enqueue(new WriterTuple("", 0l, null));
        }
        checkFlushException();
    }

    private void asyncFlush() throws Exception {
        WriterTuple flushData = flushQueue.take();
        if (stopped || flushException != null || Strings.isNullOrEmpty(flushData.getLabel())) {
            return;
        }
        stopScheduler();
        for (int i = 0; i <= options.getMaxRetries(); i++) {
            if (stopped) throw new IOException("Writer is stopped");
            try {
                // copy into
                visitor.streamLoad(flushData);
                startScheduler();
                break;
            } catch (Exception e) {
                LOG.warn("Failed to flush batch data to selectdb, retry times = {}", i, e);
                if (i >= options.getMaxRetries()) {
                    throw new RuntimeException(e);
                }
                if (e instanceof SelectdbWriterException && ((SelectdbWriterException)e).needReCreateLabel()) {
                    String newLabel = createBatchLabel();
                    LOG.warn(String.format("Batch label changed from [%s] to [%s]", flushData.getLabel(), newLabel));
                    flushData.setLabel(newLabel);
                }
                try {
                    Thread.sleep(1000l * Math.min(i + 1, 100));
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                    throw new RuntimeException("Unable to flush, interrupted while doing another attempt", e);
                }
            }
        }
    }

    private void checkFlushException() {
        if (flushException != null) {
            throw new RuntimeException("Writing records to selectdb failed.", flushException);
        }
    }
}
