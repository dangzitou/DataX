import com.alibaba.datax.common.util.Configuration;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.*;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Actual visitor/HTTP clients to a hashing sink; not a database/Engine test. */
public class StreamLoadPayloadCheck {
    static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder();
        for (byte value : bytes) text.append(String.format("%02x", value & 255));
        return text.toString();
    }
    static byte[] utf8(String text) { return text.getBytes(StandardCharsets.UTF_8); }

    public static void main(String[] args) throws Exception {
        String backend = args[0], format = args[1], mode = args[2];
        boolean expectOom = Arrays.asList(args).contains("--expect-oom");
        boolean json = format.equals("json"), large = mode.equals("large");
        List<byte[]> rows = new ArrayList<byte[]>();
        if (large) {
            // Distinct real allocations, not 64 references to one aliased row.
            for (int i = 0; i < 64; i++) {
                byte[] bytes = new byte[1024 * 1024];
                Arrays.fill(bytes, (byte) 'a');
                byte[] prefix = utf8(json ? "{\"v\":\"" + String.format("%08x", i) : String.format("%08x", i));
                System.arraycopy(prefix, 0, bytes, 0, prefix.length);
                if (json) { bytes[bytes.length-2] = '"'; bytes[bytes.length-1] = '}'; }
                rows.add(bytes);
            }
        } else if (!mode.equals("empty")) {
            rows.add(utf8(json ? "{\"v\":\"中文😀\\n\"}" : "1\t中文😀"));
            rows.add(utf8(json ? "{\"v\":null}" : "2\t\\N"));
            rows.add(rows.get(0));
        }
        byte[] delimiter = utf8(json ? "," : "\r\n");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long encodedBytes = 0, expectedBytes = json ? 2 : 0;
        if (json) digest.update((byte) '[');
        for (int i = 0; i < rows.size(); i++) {
            if (json && i > 0) { digest.update(delimiter); expectedBytes += delimiter.length; }
            digest.update(rows.get(i)); encodedBytes += rows.get(i).length; expectedBytes += rows.get(i).length;
            if (!json) { digest.update(delimiter); expectedBytes += delimiter.length; }
        }
        if (json) digest.update((byte) ']');
        final String expectedSha = hex(digest.digest());
        final long bodyBytes = expectedBytes;
        final List<String> received = Collections.synchronizedList(new ArrayList<String>());
        final List<String> labels = Collections.synchronizedList(new ArrayList<String>());
        final Throwable[] serverFailure = {null};
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        final String address = "http://127.0.0.1:" + server.getAddress().getPort();
        server.createContext("/", request -> {
            try {
                String uri = request.getRequestURI().getPath();
                if (uri.equals("/copy/upload")) {
                    request.getResponseHeaders().set("Location", address + "/sink");
                    reply(request, 307, ""); return;
                }
                if (uri.equals("/copy/query")) {
                    while (request.getRequestBody().read() != -1) {}
                    reply(request, 200, "{\"code\":0,\"data\":{\"code\":\"0\",\"result\":{\"state\":\"FINISHED\",\"msg\":\"\"}}}");
                    return;
                }
                if (!request.getRequestMethod().equals("PUT")) { reply(request, 200, "{}"); return; }
                MessageDigest actual = MessageDigest.getInstance("SHA-256");
                byte[] buffer = new byte[8192]; long count = 0; int n;
                while ((n = request.getRequestBody().read(buffer)) != -1) { actual.update(buffer, 0, n); count += n; }
                String sha = hex(actual.digest());
                require(count == bodyBytes && sha.equals(expectedSha), "Payload changed: " + count + ":" + sha);
                String contentLength = request.getRequestHeaders().getFirst("Content-Length");
                if (backend.equals("selectdb")) {
                    require(contentLength == null, "SelectDB upload unexpectedly became fixed length");
                    require("chunked".equalsIgnoreCase(request.getRequestHeaders().getFirst("Transfer-Encoding")), "Missing chunked framing");
                } else {
                    require(Long.toString(bodyBytes).equals(contentLength), "Wrong Content-Length: " + contentLength);
                    labels.add(request.getRequestHeaders().getFirst("label"));
                }
                received.add(sha);
                if (!backend.equals("selectdb") && !uri.equals("/sink") && !mode.equals("replay")) {
                    request.getResponseHeaders().set("Location", address + "/sink");
                    reply(request, 307, ""); return;
                }
                if (mode.equals("replay") && received.size() == 1) {
                    reply(request, 500, "{\"Status\":\"Fail\",\"Message\":\"injected upload failure\"}"); return;
                }
                reply(request, 200, backend.equals("selectdb") ? "" : "{\"Status\":\"Success\",\"NumberTotalRows\":" + rows.size()
                    + ",\"NumberLoadedRows\":" + rows.size() + ",\"NumberFilteredRows\":0,\"NumberUnselectedRows\":0}");
            } catch (Throwable error) { serverFailure[0] = error; request.close(); }
        });
        server.start();
        Object visitor = null;
        boolean oom = false;
        try {
            String prefix = backend.equals("starrocks") ? "com.starrocks.connector.datax.plugin.writer.starrockswriter."
                : "com.alibaba.datax.plugin.writer." + backend + "writer.";
            Class<?> optionsClass = Class.forName(prefix + (backend.equals("starrocks") ? "StarRocksWriterOptions" : "Keys"));
            Configuration config = Configuration.newDefault();
            config.set("username", "test"); config.set("password", ""); config.set("column", Collections.singletonList("v"));
            config.set("selectedDatabase", "test"); config.set("table", "target");
            config.set("loadUrl", Collections.singletonList("127.0.0.1:" + server.getAddress().getPort()));
            Map<String, Object> props = new HashMap<String, Object>();
            props.put(backend.equals("selectdb") ? "file.type" : "format", format);
            props.put(backend.equals("selectdb") ? "file.line_delimiter" : backend.equals("starrocks") ? "row_delimiter" : "line_delimiter", "\\x0D0A");
            config.set("loadProps", props);
            Object options = optionsClass.getConstructor(Configuration.class).newInstance(config);
            String visitorName = backend.equals("starrocks") ? "manager.StarRocksStreamLoadVisitor"
                : backend.equals("doris") ? "DorisStreamLoadObserver" : "SelectdbCopyIntoObserver";
            String tupleName = backend.equals("starrocks") ? "manager.StarRocksFlushTuple" : "WriterTuple";
            visitor = Class.forName(prefix + visitorName).getConstructor(optionsClass).newInstance(options);
            Class<?> tupleClass = Class.forName(prefix + tupleName);
            Object tuple = tupleClass.getConstructor(String.class, Long.class, List.class).newInstance("payload-test", encodedBytes, rows);
            Method load = visitor.getClass().getMethod(backend.equals("starrocks") ? "doStreamLoad" : "streamLoad", tupleClass);
            if (mode.equals("replay")) {
                try { load.invoke(visitor, tuple); throw new AssertionError("Injected rejection falsely succeeded"); }
                catch (InvocationTargetException expected) { require(expected.getCause() instanceof Exception, "Unexpected retry failure"); }
            }
            try { load.invoke(visitor, tuple); }
            catch (InvocationTargetException error) {
                if (!(error.getCause() instanceof OutOfMemoryError)) throw error;
                oom = true;
            }
            require(oom == expectOom, "Unexpected OOM=" + oom);
            require(serverFailure[0] == null, "Server failure: " + serverFailure[0]);
            if (!oom) {
                int expectedRequests = backend.equals("selectdb") && !mode.equals("replay") ? 1 : 2;
                require(received.size() == expectedRequests, "Wrong number of payloads: " + received.size());
                for (String label : labels) require(label.equals("payload-test"), "Changed retry label");
            } else require(received.isEmpty(), "OOM after transmitting data");
            System.out.println("RESULT {\"backend\":\"" + backend + "\",\"format\":\"" + format + "\",\"mode\":\"" + mode
                + "\",\"source_bytes\":" + encodedBytes + ",\"http_body_bytes\":" + bodyBytes + ",\"body_sha256\":\"" + expectedSha
                + "\",\"received_bodies\":" + received.size() + ",\"expected_oom\":" + oom + ",\"max_heap_bytes\":" + Runtime.getRuntime().maxMemory() + "}");
        } finally {
            if (visitor != null && backend.equals("selectdb")) visitor.getClass().getMethod("close").invoke(visitor);
            server.stop(0);
        }
    }
    static void reply(HttpExchange request, int status, String text) throws IOException {
        byte[] bytes = utf8(text);
        request.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        try (OutputStream output = request.getResponseBody()) { if (bytes.length > 0) output.write(bytes); }
    }
}
