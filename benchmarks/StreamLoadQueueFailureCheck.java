import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.plugin.RecordReceiver;
import com.alibaba.datax.common.spi.Writer;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.core.transport.record.DefaultRecord;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.*;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** Real managers, Tasks and HTTP clients; loopback simulator, not database or Engine. */
public class StreamLoadQueueFailureCheck {
    public static void main(String[] args) throws Exception {
        boolean unsafe = Arrays.asList(args).contains("--expect-unsafe");
        String backends = args[0];
        for (String backend : backends.split(",")) {
            for (int attempt = 1; attempt <= 4; attempt++) {
                failedBatch(backend, attempt, unsafe);
                if (!unsafe) {
                    successfulClose(backend, attempt, false);
                    successfulClose(backend, attempt, true);
                    failedTask(backend, attempt);
                    saturatedAbort(backend, attempt);
                }
            }
        }
    }

    static class Endpoint implements AutoCloseable {
        final HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        final CountDownLatch arrived = new CountDownLatch(1), release = new CountDownLatch(1);
        final List<String> labels = Collections.synchronizedList(new ArrayList<String>());
        final List<String> bodies = Collections.synchronizedList(new ArrayList<String>());
        volatile String first;
        volatile Throwable failure;
        final boolean failFirst;

        Endpoint(boolean failFirst, boolean blockFirst) throws IOException {
            this.failFirst = failFirst;
            if (!blockFirst) release.countDown();
            server.createContext("/", this::respond);
            server.start();
        }

        void respond(HttpExchange request) throws IOException {
            try {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                byte[] buffer = new byte[4096]; int read;
                while ((read = request.getRequestBody().read(buffer)) != -1) bytes.write(buffer, 0, read);
                String body = new String(bytes.toByteArray(), StandardCharsets.UTF_8);
                String uri = request.getRequestURI().getPath();
                boolean uploadAddress = uri.equals("/copy/upload");
                String response = "{}";
                int status = 200;
                if (uploadAddress || (request.getRequestMethod().equals("PUT") && !uri.startsWith("/upload/"))) {
                    String label = request.getRequestHeaders().getFirst(uploadAddress ? "fileName" : "label");
                    labels.add(label);
                    if (!uploadAddress) bodies.add(body);
                    if (first == null) {
                        first = label; arrived.countDown();
                        require(release.await(10, TimeUnit.SECONDS), "First request was not released");
                    }
                    boolean failed = failFirst && label.equals(first);
                    if (uploadAddress) {
                        status = failed ? 500 : 307;
                        if (!failed) request.getResponseHeaders().set("Location", address() + "/upload/" + label);
                    } else {
                        int rows = body.split("\n", -1).length - 1;
                        response = failed ? "{\"Status\":\"Fail\",\"Message\":\"injected first batch failure\"}"
                            : "{\"Status\":\"Success\",\"NumberTotalRows\":" + rows
                              + ",\"NumberLoadedRows\":" + rows
                              + ",\"NumberFilteredRows\":0,\"NumberUnselectedRows\":0}";
                    }
                } else if (uri.startsWith("/upload/")) {
                    bodies.add(body);
                    response = ""; // Empty upload response releases the HTTP connection in the existing client.
                } else if (uri.equals("/copy/query")) {
                    response = "{\"code\":0,\"data\":{\"code\":\"0\",\"result\":{\"state\":\"FINISHED\",\"msg\":\"\"}}}";
                }
                byte[] result = response.getBytes(StandardCharsets.UTF_8);
                request.sendResponseHeaders(status, result.length == 0 ? -1 : result.length);
                try (OutputStream output = request.getResponseBody()) { if (result.length > 0) output.write(result); }
            } catch (Throwable e) {
                failure = e; request.close();
            }
        }
        String address() { return "http://127.0.0.1:" + server.getAddress().getPort(); }
        public void close() { release.countDown(); server.stop(0); }
    }

    static Configuration config(Endpoint endpoint, int rows, int interval) {
        Configuration config = Configuration.newDefault();
        config.set("column", Arrays.asList("id", "txt"));
        config.set("selectedDatabase", "test"); config.set("table", "target");
        config.set("connection[0].selectedDatabase", "test");
        config.set("connection[0].table[0]", "target");
        config.set("username", "test"); config.set("password", "");
        config.set("loadUrl", Collections.singletonList("127.0.0.1:" + endpoint.server.getAddress().getPort()));
        config.set("loadProps", new HashMap<String, Object>());
        config.set("maxBatchRows", rows); config.set("flushQueueLength", 2);
        config.set("flushInterval", interval);
        return config;
    }
    static String prefix(String backend) {
        return backend.equals("starrocks") ? "com.starrocks.connector.datax.plugin.writer.starrockswriter."
                : "com.alibaba.datax.plugin.writer." + backend + "writer.";
    }
    static String writerName(String backend) {
        return backend.equals("starrocks") ? "StarRocksWriter" : backend.equals("doris") ? "DorisWriter" : "SelectdbWriter";
    }
    static Object manager(String backend, Configuration config) throws Exception {
        Class<?> options = Class.forName(prefix(backend) + (backend.equals("starrocks") ? "StarRocksWriterOptions" : "Keys"));
        Class<?> manager = Class.forName(prefix(backend) + (backend.equals("starrocks") ? "manager." : "") + writerName(backend) + "Manager");
        return manager.getConstructor(options).newInstance(options.getConstructor(Configuration.class).newInstance(config));
    }
    static Object field(Object target, String name) throws Exception {
        Field field = target.getClass().getDeclaredField(name); field.setAccessible(true); return field.get(target);
    }
    static void write(Object manager, String record) throws Exception {
        manager.getClass().getMethod("writeRecord", String.class).invoke(manager, record);
    }
    static void call(Object manager, String method) throws Exception { manager.getClass().getMethod(method).invoke(manager); }
    static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    static void rejected(Object manager, String method) throws Exception {
        try { call(manager, method); throw new AssertionError(method + " falsely succeeded"); }
        catch (InvocationTargetException expected) { require(expected.getCause() instanceof Exception, "Wrong failure"); }
    }
    static void terminated(Object manager) throws Exception {
        Thread worker = (Thread) field(manager, "flushThread"); worker.join(2000);
        require(!worker.isAlive(), "Flush worker leaked");
        require(((ExecutorService) field(manager, "scheduler")).awaitTermination(2, TimeUnit.SECONDS), "Scheduler leaked");
    }
    static void result(String backend, int attempt, String scenario, String detail) {
        System.out.println("RESULT {\"backend\":\"" + backend + "\",\"attempt\":" + attempt
            + ",\"scenario\":\"" + scenario + "\"," + detail + "}");
    }

    static void failedBatch(String backend, int attempt, boolean unsafe) throws Exception {
        ExecutorService closer = Executors.newSingleThreadExecutor();
        try (Endpoint endpoint = new Endpoint(true, true)) {
            Object manager = manager(backend, config(endpoint, 1, 60000));
            Object options = field(manager, backend.equals("starrocks") ? "writerOptions" : "options");
            int attempts = (Integer) options.getClass().getMethod("getMaxRetries").invoke(options) + 1;
            write(manager, "1\t中文😀");
            require(endpoint.arrived.await(10, TimeUnit.SECONDS), "First request missing");
            write(manager, "2\tqueued"); write(manager, "3\tqueued");
            Future<?> close = closer.submit(() -> { try { rejected(manager, "close"); } catch (Exception e) { throw new RuntimeException(e); } });
            endpoint.release.countDown(); close.get(20, TimeUnit.SECONDS);
            if (!unsafe) terminated(manager);
            int later = endpoint.labels.size() - Collections.frequency(endpoint.labels, endpoint.first);
            require(later == (unsafe ? 2 : 0), "Later batch requests: " + later);
            require(Collections.frequency(endpoint.labels, endpoint.first) == attempts, "Unexpected retry count");
            require(endpoint.failure == null, "HTTP handler failed: " + endpoint.failure);
            result(backend, attempt, "terminal-failure", "\"first_batch_attempts\":" + attempts
                + ",\"later_batch_requests\":" + later + ",\"close_failed\":true");
        } finally { closer.shutdownNow(); }
    }

    static void successfulClose(String backend, int attempt, boolean timer) throws Exception {
        try (Endpoint endpoint = new Endpoint(false, false)) {
            Object manager = manager(backend, config(endpoint, timer ? 100 : 1, timer ? 50 : 60000));
            Object scheduler = field(manager, "scheduler");
            String[] rows = {"1\t中文😀", "2\tduplicate", "2\tduplicate"};
            for (int i = 0; i < rows.length; i++) {
                write(manager, rows[i]);
                if (timer && i == 0) require(endpoint.arrived.await(5, TimeUnit.SECONDS), "Timer did not flush");
                if (!timer) manager.getClass().getMethod("flush", String.class, boolean.class).invoke(manager, "barrier", true);
                require(scheduler == field(manager, "scheduler"), "Scheduler recreated for each batch");
            }
            call(manager, "close"); call(manager, "close"); terminated(manager);
            try { write(manager, "4\tclosed"); throw new AssertionError("Closed writer accepted record"); }
            catch (InvocationTargetException expected) { }
            require(String.join("", endpoint.bodies).equals(String.join("\n", rows) + "\n"), "Lost, changed or duplicated payload");
            require(endpoint.failure == null, "HTTP handler failed: " + endpoint.failure);
            result(backend, attempt, timer ? "timer-close" : "batch-close", "\"exact_rows\":3,\"scheduler_reused\":true,\"threads_terminated\":true");
        }
    }

    static void failedTask(String backend, int attempt) throws Exception {
        try (Endpoint endpoint = new Endpoint(false, false)) {
            Writer.Task task = (Writer.Task) Class.forName(prefix(backend) + writerName(backend) + "$Task").newInstance();
            task.setPluginJobConf(config(endpoint, 100, 60000)); task.init();
            Object manager = field(task, "writerManager");
            try {
                task.startWrite(new RecordReceiver() {
                    int count;
                    public Record getFromReader() {
                        if (count == 2) return null;
                        Record record = new DefaultRecord(); record.addColumn(new LongColumn(1));
                        if (count++ == 0) record.addColumn(new StringColumn("buffered"));
                        return record; // The second record has an invalid column count.
                    }
                    public void shutdown() { }
                });
                throw new AssertionError("Malformed row was accepted");
            } catch (com.alibaba.datax.common.exception.DataXException expected) {
                require(expected.toString().contains("WRITE_DATA_ERROR") || expected.getCause() != null, "Unexpected task error");
            } finally { task.destroy(); }
            terminated(manager); rejected(manager, "close");
            require(endpoint.labels.isEmpty(), "Failed task flushed its buffered row");
            result(backend, attempt, "failed-task-destroy", "\"requests\":0,\"threads_terminated\":true");
        }
    }

    static void saturatedAbort(String backend, int attempt) throws Exception {
        ExecutorService producer = Executors.newSingleThreadExecutor();
        try (Endpoint endpoint = new Endpoint(false, true)) {
            Object manager = manager(backend, config(endpoint, 1, 60000));
            write(manager, "1\tin-flight");
            require(endpoint.arrived.await(10, TimeUnit.SECONDS), "First request missing");
            write(manager, "2\tqueued"); write(manager, "3\tqueued");
            CountDownLatch started = new CountDownLatch(1);
            Future<?> blocked = producer.submit(() -> {
                started.countDown();
                try { write(manager, "4\tblocked"); throw new AssertionError("Aborted producer succeeded"); }
                catch (InvocationTargetException expected) { }
                catch (Exception e) { throw new RuntimeException(e); }
            });
            require(started.await(2, TimeUnit.SECONDS), "Producer not started");
            try { blocked.get(100, TimeUnit.MILLISECONDS); throw new AssertionError("Queue did not block producer"); }
            catch (TimeoutException expected) { }
            call(manager, "abort"); blocked.get(2, TimeUnit.SECONDS);
            endpoint.release.countDown(); terminated(manager); rejected(manager, "close");
            require(endpoint.labels.size() == 1, "Abort sent queued work");
            require(endpoint.failure == null, "HTTP handler failed: " + endpoint.failure);
            result(backend, attempt, "saturated-abort", "\"batch_requests\":1,\"blocked_producer_released\":true,\"in_flight_may_commit\":true");
        } finally { producer.shutdownNow(); }
    }
}
