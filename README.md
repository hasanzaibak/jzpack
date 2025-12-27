# jzpack

High-performance JSON/NDJSON compression library that significantly outperforms GZIP.

## Features

- **83-99% smaller** than GZIP on typical JSON log data
- **Column-oriented** storage with automatic schema detection
- **Smart encoding** selection per column (RLE, Delta, Dictionary)
- **Zstandard** compression with MessagePack serialization
- **Streaming support** for large datasets
- **Type hints** included

## Benchmarks

Tested on 200,000 JSON records:

| Dataset | GZIP (level 9) | jzpack | Improvement |
|---------|----------------|--------|-------------|
| High-Cardinality Logs | 1.47 MB | 244 KB | **83.8%** smaller |
| Mixed Service Logs | 1.72 MB | 99 KB | **94.4%** smaller |
| Highly Repetitive Data | 175 KB | 579 B | **99.7%** smaller |

## Installation
```bash
pip install jzpack
```

## Quick Start
```python
from jzpack import compress, decompress

data = [{"service": "api", "status": "ok", "latency": 42} for _ in range(10000)]

compressed = compress(data)
original = decompress(compressed)
```

### File I/O
```python
import json
from jzpack import compress, decompress

# Compress JSON file
with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

with open("data.jzpk", "wb") as f:
    f.write(compress([data]))

# Decompress
with open("data.jzpk", "rb") as f:
    data = decompress(f.read())[0]
```

### Compression Level
```python
from jzpack import compress

compressed = compress(data, level=19)  # 1-22, higher = smaller but slower
```

## Advanced Usage

### JZPackCompressor
```python
from jzpack import JZPackCompressor

compressor = JZPackCompressor(compression_level=3)

compressed = compressor.compress(data)
original = compressor.decompress(compressed)

compressor.compress_to_file(data, "output.jzpk")
data = compressor.decompress_from_file("output.jzpk")
```

### StreamingCompressor
```python
from jzpack import StreamingCompressor

compressor = StreamingCompressor(compression_level=3)

for record in records_iterator:
    compressor.add_record(record)

compressor.add_batch(batch_of_records)
compressed = compressor.finalize()
compressor.clear()
```

## How It Works

1. **Schema Detection** - Groups records by structure
2. **Column-Oriented Storage** - Stores each field as a column
3. **Smart Encoding Selection** per column:
   - **RLE** for repetitive values
   - **Delta** for sequential numbers
   - **Dictionary** for low-cardinality strings
4. **Binary Serialization** via MessagePack
5. **Zstandard Compression**

## API Reference

| Function | Description |
|----------|-------------|
| `compress(data, level=3)` | Compress list of dicts to bytes |
| `decompress(data)` | Decompress bytes to list of dicts |

## License

MIT