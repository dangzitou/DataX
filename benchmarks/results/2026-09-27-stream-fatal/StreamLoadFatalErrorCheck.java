import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Field;
import java.util.AbstractList;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/** Real client allocation failure with a streaming HTTP simulator, not a database or Engine. */
public class StreamLoadFatalErrorCheck extends StreamLoadQueueFailureCheck {
    static class OversizedResponse extends Endpoint {
        final String backend;
        final CountDownLatch reading = new CountDownLatch(1), respond = new CountDownLatch(1);
        int oversizedRequests;
        OversizedResponse(String backend) throws IOException {
            super(false, false);
            this.backend = backend;
        }
        @Override void respond(HttpExchange request) throws IOException {
            String path = request.getRequestURI().getPath();
            if (!(backend.equals("selectdb") ? path.equals("/copy/query") : request.getRequestMethod().equals("PUT"))) {
                super.respond(request);
                return;
            }
            try {
                byte[] chunk = new byte[8192];
                while (request.getRequestBody().read(chunk) != -1) { }
                oversizedRequests++;
                reading.countDown();
                require(respond.await(10, TimeUnit.SECONDS), "Response not released");
                // EntityUtils allocates a char[] from Content-Length. 160 MiB cannot fit a 128 MiB heap.
                int length = 80 * 1024 * 1024;
                request.sendResponseHeaders(200, length);
                try (OutputStream output = request.getResponseBody()) {
                    for (int sent = 0; sent < length; sent += chunk.length) output.write(chunk);
                }
            } catch (IOException expectedDisconnect) {
                // The client may close the response after the failed allocation.
            } catch (Throwable error) { failure = error; }
            finally { request.close(); }
        }
        @Override public void close() { respond.countDown(); super.close(); }
    }

    static boolean hasOom(Throwable error) {
        for (; error != null; error = error.getCause()) if (error instanceof OutOfMemoryError) return true;
        return false;
    }

    static void set(Object target, String name, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(name); field.setAccessible(true); field.set(target, value);
    }

    static void timer(String backend, boolean unsafe) throws Exception {
        try (Endpoint endpoint = new Endpoint(false, false)) {
            Object manager = manager(backend, config(endpoint, 100, 50));
            OutOfMemoryError injected = new OutOfMemoryError("injected timer snapshot failure");
            CountDownLatch failed = new CountDownLatch(1);
            try {
                synchronized (manager) {
                    set(manager, "buffer", new AbstractList<byte[]>() {
                        public int size() { return 1; }
                        public byte[] get(int i) { throw injected; }
                        @Override public Object[] toArray() { failed.countDown(); throw injected; }
                    });
                    set(manager, "batchCount", 1);
                }
                require(failed.await(5, TimeUnit.SECONDS), "Timer did not hit injected error");
                // Synchronize after the timer's catch, so the latch cannot race the observed state.
                synchronized (manager) {
                    boolean recorded = field(manager, "flushException") == injected;
                    require(recorded != unsafe, "Timer error recording was wrong");
                    require(((ExecutorService) field(manager, "scheduler")).isShutdown() != unsafe,
                            "Timer shutdown was wrong");
                    if (!unsafe) {
                        try { write(manager, "2\trejected"); throw new AssertionError("Timer error was lost"); }
                        catch (InvocationTargetException expected) { require(hasOom(expected), "Wrong timer cause"); }
                    }
                    result(backend, 1, "injected-timer-oom", "\"real_allocation_oom\":false,\"error_recorded\":"
                            + recorded + ",\"scheduler_shutdown\":" + !unsafe + ",\"requests\":" + endpoint.labels.size());
                }
            } finally { call(manager, "abort"); terminated(manager); }
        }
    }

    public static void main(String[] args) throws Exception {
        String backend = args[0];
        boolean unsafe = java.util.Arrays.asList(args).contains("--expect-stall");
        if (args[1].equals("timer")) { timer(backend, unsafe); return; }
        ExecutorService producer = Executors.newSingleThreadExecutor();
        Object manager = null;
        try (OversizedResponse endpoint = new OversizedResponse(backend)) {
            manager = manager(backend, config(endpoint, 1, 60000));
            final Object target = manager;
            Thread worker = (Thread) field(manager, "flushThread");
            AtomicReference<Throwable> uncaught = new AtomicReference<>();
            worker.setUncaughtExceptionHandler((thread, error) -> uncaught.set(error));
            write(manager, "1\tin-flight");
            require(endpoint.reading.await(10, TimeUnit.SECONDS), "No critical response request");
            write(manager, "2\tqueued"); write(manager, "3\tqueued");
            Future<Throwable> blocked = producer.submit(() -> {
                try { write(target, "4\tblocked"); return null; }
                catch (InvocationTargetException failure) { return failure.getCause(); }
            });
            try { blocked.get(200, TimeUnit.MILLISECONDS); throw new AssertionError("Producer was not blocked"); }
            catch (TimeoutException expected) { }
            endpoint.respond.countDown();
            worker.join(10000);
            require(!worker.isAlive() && hasOom(uncaught.get()), "Actual allocation OOM did not terminate worker");
            uncaught.get().printStackTrace(System.err);
            boolean allocationSite = false;
            for (StackTraceElement frame : uncaught.get().getStackTrace()) {
                if (frame.getClassName().endsWith("org.apache.http.util.CharArrayBuffer")) allocationSite = true;
            }
            require(allocationSite, "OOM was not the intended HTTP character buffer allocation");
            boolean recorded = hasOom((Throwable) field(manager, "flushException"));
            require(recorded != unsafe, "Unexpected failure recording");
            if (unsafe) {
                try { blocked.get(1000, TimeUnit.MILLISECONDS); throw new AssertionError("Stall not reproduced"); }
                catch (TimeoutException expected) { }
                call(manager, "abort"); // Harness cleanup only, after proving the production stall.
                require(blocked.get(2, TimeUnit.SECONDS) != null, "Cleanup did not release producer");
            } else {
                require(hasOom(blocked.get(2, TimeUnit.SECONDS)), "Producer lost original OOM");
                try { call(manager, "close"); throw new AssertionError("Close falsely succeeded"); }
                catch (InvocationTargetException expected) { require(hasOom(expected), "Close lost original OOM"); }
            }
            terminated(manager);
            require(endpoint.oversizedRequests == 1, "Retried or sent queued batches after OOM");
            require(endpoint.failure == null, "HTTP simulator failed: " + endpoint.failure);
            result(backend, 1, "response-allocation-oom", "\"worker_oom\":true,\"error_recorded\":" + recorded
                    + ",\"producer_stalled\":" + unsafe + ",\"critical_requests\":1,\"max_heap_bytes\":"
                    + Runtime.getRuntime().maxMemory());
        } finally {
            if (manager != null) call(manager, "abort");
            producer.shutdownNow();
        }
    }
}
