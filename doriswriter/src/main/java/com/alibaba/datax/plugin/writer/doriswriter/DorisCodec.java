package com.alibaba.datax.plugin.writer.doriswriter;

import com.alibaba.datax.common.element.Record;

import java.io.Serializable;
import java.nio.charset.StandardCharsets;

public interface DorisCodec extends Serializable {

    String codec( Record row);

    default byte[] codecBytes(Record row) {
        return codec(row).getBytes(StandardCharsets.UTF_8);
    }
}
