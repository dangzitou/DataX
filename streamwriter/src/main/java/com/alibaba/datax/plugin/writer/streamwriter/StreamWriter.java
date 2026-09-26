
package com.alibaba.datax.plugin.writer.streamwriter;

import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.spi.Writer;
import com.alibaba.datax.common.util.Configuration;
import org.apache.commons.io.FileUtils;
import org.apache.commons.io.IOUtils;
import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class StreamWriter extends Writer {
    public static class Job extends Writer.Job {
        private static final Logger LOG = LoggerFactory
                .getLogger(Job.class);

        private Configuration originalConfig;

        @Override
        public void init() {
            this.originalConfig = super.getPluginJobConf();

            String path = this.originalConfig.getString(Key.PATH, null);
            String fileName = this.originalConfig.getString(Key.FILE_NAME, null);

            if(StringUtils.isNoneBlank(path) && StringUtils.isNoneBlank(fileName)) {
                validateParameter(path, fileName);
            }
        }

        private void validateParameter(String path, String fileName) {
            try {
                // warn: 这里用户需要配一个目录
                File dir = new File(path);
                if (dir.isFile()) {
                    throw DataXException
                            .asDataXException(
                                    StreamWriterErrorCode.ILLEGAL_VALUE,
                                    String.format(
                                            "您配置的path: [%s] 不是一个合法的目录, 请您注意文件重名, 不合法目录名等情况.",
                                            path));
                }
                if (!dir.exists()) {
                    boolean createdOk = dir.mkdirs();
                    if (!createdOk) {
                        throw DataXException
                                .asDataXException(
                                        StreamWriterErrorCode.CONFIG_INVALID_EXCEPTION,
                                        String.format("您指定的文件路径 : [%s] 创建失败.",
                                                path));
                    }
                }

                String fileFullPath = buildFilePath(path, fileName);
                File newFile = new File(fileFullPath);
                if(newFile.exists()) {
                    try {
                        FileUtils.forceDelete(newFile);
                    } catch (IOException e) {
                        throw DataXException.asDataXException(
                                StreamWriterErrorCode.RUNTIME_EXCEPTION,
                                String.format("删除文件失败 : [%s] ", fileFullPath), e);
                    }
                }
            } catch (SecurityException se) {
                throw DataXException.asDataXException(
                        StreamWriterErrorCode.SECURITY_NOT_ENOUGH,
                        String.format("您没有权限创建文件路径 : [%s] ", path), se);
            }
        }

        @Override
        public void prepare() {
        }

        @Override
        public List<Configuration> split(int mandatoryNumber) {
            List<Configuration> writerSplitConfigs = new ArrayList<Configuration>();
            for (int i = 0; i < mandatoryNumber; i++) {
                writerSplitConfigs.add(this.originalConfig);
            }

            return writerSplitConfigs;
        }

        @Override
        public void post() {
        }

        @Override
        public void destroy() {
        }
    }

    public static class Task extends Writer.Task {
        private static final Logger LOG = LoggerFactory
                .getLogger(Task.class);

        private static final String NEWLINE_FLAG = System.getProperty("line.separator", "\n");
        private static final int BUFFER_SIZE = 64 * 1024;
        private StringBuilder rowBuffer = new StringBuilder(256);

        private Configuration writerSliceConfig;

        private String fieldDelimiter;
        private boolean print;

        private String path;
        private String fileName;

        private long recordNumBeforSleep;
        private long sleepTime;



        @Override
        public void init() {
            this.writerSliceConfig = getPluginJobConf();

            this.fieldDelimiter = this.writerSliceConfig.getString(
                    Key.FIELD_DELIMITER, "\t");
            this.print = this.writerSliceConfig.getBool(Key.PRINT, true);

            this.path = this.writerSliceConfig.getString(Key.PATH, null);
            this.fileName = this.writerSliceConfig.getString(Key.FILE_NAME, null);
            this.recordNumBeforSleep = this.writerSliceConfig.getLong(Key.RECORD_NUM_BEFORE_SLEEP, 0);
            this.sleepTime = this.writerSliceConfig.getLong(Key.SLEEP_TIME, 0);
            if(recordNumBeforSleep < 0) {
                throw DataXException.asDataXException(StreamWriterErrorCode.CONFIG_INVALID_EXCEPTION, "recordNumber 不能为负值");
            }
            if(sleepTime <0) {
                throw DataXException.asDataXException(StreamWriterErrorCode.CONFIG_INVALID_EXCEPTION, "sleep 不能为负值");
            }

        }

        @Override
        public void prepare() {
        }

        @Override
        public void startWrite(RecordReceiver recordReceiver) {


                if(StringUtils.isNoneBlank(path) && StringUtils.isNoneBlank(fileName)) {
                    writeToFile(recordReceiver,path, fileName, recordNumBeforSleep, sleepTime);
                } else {
                    try {
                        OutputStream writer = System.out;

                        Record record;
                        while ((record = recordReceiver.getFromReader()) != null) {
                            if (this.print) {
                                appendRecord(record);
                                if (rowBuffer.length() >= BUFFER_SIZE) flushRecords(writer);
                            } else {
                        /* do nothing */
                            }
                        }
                        flushRecords(writer);
                        writer.flush();

                    } catch (Exception e) {
                        throw DataXException.asDataXException(StreamWriterErrorCode.RUNTIME_EXCEPTION, e);
                    }
                }
        }

        private void writeToFile(RecordReceiver recordReceiver, String path, String fileName,
                                 long recordNumBeforSleep, long sleepTime) {

            LOG.info("begin do write...");
            String fileFullPath = buildFilePath(path, fileName);
            LOG.info(String.format("write to file : [%s]", fileFullPath));
            try (OutputStream writer = new FileOutputStream(fileFullPath, true) {
                        @Override
                        public void write(byte[] bytes) throws IOException {
                            // ponytail: one JVM-wide file-write lock; use per-file locks if unrelated files contend.
                            // Buffers contain whole UTF-8 records, including records larger than the buffer.
                            synchronized (Task.class) {
                                super.write(bytes);
                            }
                        }
                    }) {
                Record record;
                long count = 0;
                while ((record = recordReceiver.getFromReader()) != null) {
                    if(recordNumBeforSleep > 0 && sleepTime >0 &&count == recordNumBeforSleep) {
                        LOG.info("StreamWriter start to sleep ... recordNumBeforSleep={},sleepTime={}",recordNumBeforSleep,sleepTime);
                        TimeUnit.SECONDS.sleep(sleepTime);
                    }
                   appendRecord(record);
                   if (rowBuffer.length() >= BUFFER_SIZE) flushRecords(writer);
                   count++;
                }
                flushRecords(writer);
                writer.flush();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw DataXException.asDataXException(StreamWriterErrorCode.RUNTIME_EXCEPTION, e);
            } catch (Exception e) {
                throw DataXException.asDataXException(StreamWriterErrorCode.RUNTIME_EXCEPTION, e);
            }
        }

        @Override
        public void post() {
        }

        @Override
        public void destroy() {
        }

        private void appendRecord(Record record) {
            int recordLength = record.getColumnNumber();
            for (int i = 0; i < recordLength; i++) {
                if (i > 0) rowBuffer.append(fieldDelimiter);
                Column column = record.getColumn(i);
                rowBuffer.append(column.asString());
            }
            rowBuffer.append(NEWLINE_FLAG);
        }

        private void flushRecords(OutputStream writer) throws IOException {
            if (rowBuffer.length() == 0) return;
            writer.write(rowBuffer.toString().getBytes(StandardCharsets.UTF_8));
            // Bound retained memory after a wide row; batching avoids per-row byte arrays.
            if (rowBuffer.capacity() > 2 * BUFFER_SIZE) rowBuffer = new StringBuilder(256);
            rowBuffer.setLength(0);
        }
    }

    private static String buildFilePath(String path, String fileName) {
        boolean isEndWithSeparator = false;
        switch (IOUtils.DIR_SEPARATOR) {
            case IOUtils.DIR_SEPARATOR_UNIX:
                isEndWithSeparator = path.endsWith(String
                        .valueOf(IOUtils.DIR_SEPARATOR));
                break;
            case IOUtils.DIR_SEPARATOR_WINDOWS:
                isEndWithSeparator = path.endsWith(String
                        .valueOf(IOUtils.DIR_SEPARATOR_WINDOWS));
                break;
            default:
                break;
        }
        if (!isEndWithSeparator) {
            path = path + IOUtils.DIR_SEPARATOR;
        }
        return String.format("%s%s", path, fileName);
    }
}
