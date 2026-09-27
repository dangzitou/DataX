package com.alibaba.datax.plugin.writer.streamwriter;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.util.Configuration;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.*;

public class StreamWriterTest {
    @Rule public TemporaryFolder folder = new TemporaryFolder();

    private Record record(final String... values) {
        return (Record) Proxy.newProxyInstance(Record.class.getClassLoader(), new Class<?>[]{Record.class},
                (proxy, method, args) -> {
                    if (method.getName().equals("getColumnNumber")) return values.length;
                    if (method.getName().equals("getColumn")) return new StringColumn(values[(Integer) args[0]]);
                    throw new UnsupportedOperationException(method.getName());
                });
    }

    private RecordReceiver receiver(final Record... records) {
        return new RecordReceiver() {
            private int index;
            public Record getFromReader() { return index < records.length ? records[index++] : null; }
            public void shutdown() { }
        };
    }

    private StreamWriter.Task task(String fileName, String delimiter, boolean sleep) {
        Configuration config = Configuration.newDefault();
        config.set(Key.PATH, folder.getRoot().getAbsolutePath());
        config.set(Key.FILE_NAME, fileName);
        config.set(Key.FIELD_DELIMITER, delimiter);
        if (sleep) {
            config.set(Key.RECORD_NUM_BEFORE_SLEEP, 1);
            config.set(Key.SLEEP_TIME, 1);
        }
        StreamWriter.Task task = new StreamWriter.Task();
        task.setPluginJobConf(config);
        task.init();
        return task;
    }

    @Test public void preservesFieldsAndDelimiterForLargeSmallAndEmptyRecords() throws Exception {
        String large = new String(new char[100000]).replace("\0", "中文😀");
        String newline = System.lineSeparator();
        for (String delimiter : new String[]{"\t", "||", "中文", "", "😀"}) {
            File file = folder.newFile();
            task(file.getName(), delimiter, false).startWrite(receiver(
                    record(large, null, "tail-end"), record("short", "tail-end"), record()));
            String expected = large + delimiter + "null" + delimiter + "tail-end" + newline
                    + "short" + delimiter + "tail-end" + newline + newline;
            assertArrayEquals(delimiter, expected.getBytes(StandardCharsets.UTF_8), Files.readAllBytes(file.toPath()));
        }
    }

    @Test public void preservesUnicodeAcrossBufferBoundaries() throws Exception {
        for (int prefix : new int[]{8191, 8192, 65535, 65536, 131071}) {
            String text = new String(new char[prefix]).replace('\0', 'a')
                    + "😀𠀀中文";
            File file = folder.newFile();
            task(file.getName(), "\t", false).startWrite(receiver(record(text), record("next😀")));
            String expected = text + System.lineSeparator() + "next😀" + System.lineSeparator();
            assertArrayEquals(expected.getBytes(StandardCharsets.UTF_8), Files.readAllBytes(file.toPath()));
        }
    }

    @Test public void cancellationFailsAndPreservesInterruptFlagFourTimes() {
        int failedWithInterrupt = 0;
        for (int attempt = 0; attempt < 4; attempt++) {
            StreamWriter.Task task = task("cancel-" + attempt, "\t", true);
            Thread.currentThread().interrupt();
            try {
                task.startWrite(receiver(record("first"), record("second")));
            } catch (DataXException expected) {
                if (Thread.currentThread().isInterrupted()) failedWithInterrupt++;
            } finally {
                Thread.interrupted();
            }
        }
        assertEquals("Cancellation must fail, with interrupt preserved, in every attempt", 4, failedWithInterrupt);
    }

    @Test public void concurrentTasksPreserveEveryDistinctRowAcrossBufferBoundaries() throws Exception {
        File file = folder.newFile();
        List<String> expected = new ArrayList<>();
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            List<Future<?>> writes = new ArrayList<>();
            for (int worker = 0; worker < 4; worker++) {
                Record[] rows = new Record[64];
                for (int row = 0; row < rows.length; row++) {
                    String text = worker + ":" + row + ":"
                            + new String(new char[row % 3 == 0 ? 20000 : 2000]).replace("\0", "中文😀");
                    rows[row] = record(text);
                    expected.add(text);
                }
                StreamWriter.Task task = task(file.getName(), "\t", false);
                writes.add(pool.submit(() -> task.startWrite(receiver(rows))));
            }
            for (Future<?> write : writes) write.get(30, TimeUnit.SECONDS);
            List<String> actual = Files.readAllLines(file.toPath(), StandardCharsets.UTF_8);
            Collections.sort(expected);
            Collections.sort(actual);
            assertEquals(expected, actual);
        } finally {
            pool.shutdownNow();
        }
    }
}
