package com.starrocks.connector.datax.plugin.writer.starrockswriter.row;

import java.io.Serializable;
import java.nio.charset.StandardCharsets;

import com.alibaba.datax.common.element.Record;

public interface StarRocksISerializer extends Serializable {

    String serialize(Record row);

    default byte[] serializeBytes(Record row) {
        return serialize(row).getBytes(StandardCharsets.UTF_8);
    }
    
}
