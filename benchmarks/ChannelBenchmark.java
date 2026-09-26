import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.core.statistics.communication.Communication;
import com.alibaba.datax.core.transport.channel.memory.MemoryChannel;
import com.alibaba.datax.core.transport.record.DefaultRecord;
import com.alibaba.datax.core.transport.record.TerminateRecord;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

/** Isolated transport microbenchmark, NOT database or end-to-end throughput. */
public class ChannelBenchmark {
    private static double run(int rows) throws Exception {
        Configuration config = Configuration.newDefault();
        config.set("core.container.taskGroup.id", 0);
        config.set("core.transport.channel.capacity", 512);
        config.set("core.transport.channel.byteCapacity", 67108864);
        config.set("core.transport.channel.speed.byte", -1);
        config.set("core.transport.channel.speed.record", -1);
        config.set("core.transport.exchanger.bufferSize", 32);
        MemoryChannel channel = new MemoryChannel(config);
        channel.setCommunication(new Communication());
        List<Record> batch = new ArrayList<Record>();
        for (int i = 0; i < 32; i++) {
            Record record = new DefaultRecord();
            record.addColumn(new LongColumn(i));
            batch.add(record);
        }
        AtomicReference<Throwable> failure = new AtomicReference<Throwable>();
        Thread producer = new Thread(() -> {
            try {
                for (int i = 0; i < rows; i += 32) channel.pushAll(batch);
                channel.pushTerminate(TerminateRecord.get());
            } catch (Throwable e) { failure.set(e); }
        });
        long start = System.nanoTime();
        producer.start();
        List<Record> received = new ArrayList<Record>();
        int count = 0;
        boolean done = false;
        while (!done) {
            channel.pullAll(received);
            for (Record record : received) {
                if (record instanceof TerminateRecord) { done = true; break; }
                if (record.getColumn(0).asLong() != count % 32) throw new AssertionError("FIFO/order mismatch");
                count++;
            }
        }
        producer.join();
        if (failure.get() != null) throw new AssertionError(failure.get());
        if (count != rows) throw new AssertionError("record count mismatch");
        return (System.nanoTime() - start) / 1e9;
    }

    public static void main(String[] args) throws Exception {
        run(1048576);
        int rows = 67108864;
        double seconds = run(rows);
        System.out.println("BENCHMARK {\"rows\":" + rows + ",\"seconds\":" + seconds
                + ",\"rows_per_second\":" + rows / seconds + "}");
    }
}
