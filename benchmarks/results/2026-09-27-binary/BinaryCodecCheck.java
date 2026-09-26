import com.alibaba.datax.common.element.BytesColumn;
import com.alibaba.datax.common.element.Column;
import com.alibaba.datax.common.element.LongColumn;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.fastjson2.JSON;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import javax.xml.bind.DatatypeConverter;

/** Pure codec/factory check, including SelectDB; it does not contact a service. */
public class BinaryCodecCheck {
    private static Object invoke(java.lang.reflect.Method method, Object target, Object... args) throws Exception {
        try { return method.invoke(target,args); }
        catch (InvocationTargetException error) {
            if (error.getCause() instanceof Exception) throw (Exception) error.getCause();
            throw error;
        }
    }
    private static String encode(String[] plugin, String format, String encoding, Column column, String delimiter) throws Exception {
        Configuration config=Configuration.newDefault();
        config.set("column",Collections.singletonList("payload"));
        Map<String,Object> props=new HashMap<>();
        props.put(plugin[5],format);
        if (encoding!=null) config.set("binaryEncoding",encoding);
        if (delimiter!=null) props.put(plugin[6],delimiter);
        config.set("loadProps",props);
        Class<?> options=Class.forName(plugin[0]);
        Object codec=invoke(Class.forName(plugin[1]).getMethod(plugin[2],options),null,
                options.getConstructor(Configuration.class).newInstance(config));
        Record row=(Record) Proxy.newProxyInstance(Record.class.getClassLoader(),new Class<?>[]{Record.class},(p,m,a)->{
            if (m.getName().equals("getColumnNumber")) return 1;
            if (m.getName().equals("getColumn")) return column;
            throw new UnsupportedOperationException(m.getName());
        });
        String text=(String) invoke(codec.getClass().getMethod(plugin[3],Record.class),codec,row);
        return format.equals("json") ? JSON.parseObject(text).getString("payload") : text;
    }
    public static void main(String[] args) throws Exception {
        String sr="com.starrocks.connector.datax.plugin.writer.starrockswriter.";
        String dw="com.alibaba.datax.plugin.writer.doriswriter.";
        String sw="com.alibaba.datax.plugin.writer.selectdbwriter.";
        String[][] plugins={
            {sr+"StarRocksWriterOptions",sr+"row.StarRocksSerializerFactory","createSerializer","serialize","starrocks","format","row_delimiter"},
            {dw+"Keys",dw+"DorisCodecFactory","createCodec","codec","doris","format","line_delimiter"},
            {sw+"Keys",sw+"SelectdbCodecFactory","createCodec","codec","selectdb","file.type","file.line_delimiter"}};
        int checks=0;
        for (String[] plugin:plugins) for (String format:new String[]{"csv","json"}) {
            for (String encoding:new String[]{"hex","base64"}) for (int length:new int[]{0,1,8,9,256}) {
                byte[] bytes=new byte[length]; for (int i=0;i<length;i++) bytes[i]=(byte)i;
                String value=encode(plugin,format,encoding,new BytesColumn(bytes),null);
                byte[] decoded=encoding.equals("hex") ? DatatypeConverter.parseHexBinary(value) : Base64.getDecoder().decode(value);
                if (!Arrays.equals(bytes,decoded)) throw new AssertionError("Binary mismatch: "+plugin[4]);
                checks++;
            }
            for (String encoding:new String[]{null,"hex","base64"}) {
                String value=encode(plugin,format,encoding,new BytesColumn(),null);
                if (format.equals("json") ? value!=null : !"\\N".equals(value)) throw new AssertionError("NULL changed");
                checks++;
            }
            for (Column value:new Column[]{new LongColumn("18446744073709551615"),new StringColumn("ordinary text")}) {
                if (!value.asString().equals(encode(plugin,format,null,value,null))) throw new AssertionError("Scalar changed");
                checks++;
            }
            try { encode(plugin,format,null,new BytesColumn(new byte[]{0,1}),null); throw new AssertionError("Implicit BYTES accepted"); }
            catch (IllegalArgumentException expected) { if (!expected.getMessage().contains("binaryEncoding")) throw expected; checks++; }
            try { encode(plugin,format,"long",new StringColumn("ordinary"),null); throw new AssertionError("Invalid encoding accepted"); }
            catch (IllegalArgumentException expected) { if (!expected.getMessage().contains("binaryEncoding")) throw expected; checks++; }
            if (format.equals("csv")) {
                try { encode(plugin,format,"hex",new BytesColumn(new byte[]{(byte)255}),"f"); throw new AssertionError("Delimiter collision accepted: "+plugin[4]); }
                catch (IllegalArgumentException expected) { if (!expected.getMessage().contains("Unsafe CSV")) throw expected; checks++; }
            }
        }
        System.out.println("PASS "+checks+" pure codec/factory checks; no SelectDB server test");
    }
}
