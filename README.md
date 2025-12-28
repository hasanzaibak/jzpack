# jzpack

High-compression JSON library using columnar storage + zstd.

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

## Benchmarks

100K records, zstd level 3, 3 iterations averaged.

**Realistic Data** (mixed repetition):

| Strategy | Size | Ratio | Compress | Decompress |
|:---|---:|---:|---:|---:|
| json + gzip | 5.71 MB | 3.45x | 14 MB/s | 91 MB/s |
| orjson + zstd | 5.86 MB | 3.36x | 220 MB/s | 143 MB/s |
| msgpack + zstd | 5.71 MB | 3.44x | 154 MB/s | 125 MB/s |
| **jzpack** | 4.60 MB | 4.28x | 48 MB/s | 53 MB/s |

**High Cardinality** (worst case, unique values):

| Strategy | Size | Ratio | Compress | Decompress |
|:---|---:|---:|---:|---:|
| json + gzip | 19.94 MB | 1.78x | 14 MB/s | 102 MB/s |
| orjson + zstd | 19.61 MB | 1.81x | 159 MB/s | 166 MB/s |
| msgpack + zstd | 19.04 MB | 1.87x | 147 MB/s | 175 MB/s |
| **jzpack** | 15.86 MB | 2.24x | 75 MB/s | 79 MB/s |

**vs msgpack+zstd** (same underlying compression): **17-19% smaller**, 2-3x slower.

Run: `python benchmark.py`

## When to Use

Cold storage, archival, network transfer  
Datasets with field repetition  

## API

```python
from jzpack import compress, decompress, JZPackCompressor, StreamingCompressor

compress(data, level=3) -> bytes
decompress(data) -> list[dict]

compressor = JZPackCompressor(compression_level=3)
compressor.compress(data) -> bytes
compressor.decompress(data) -> list[dict]
compressor.compress_to_file(data, "out.jzpk")
compressor.decompress_from_file("out.jzpk") -> list[dict]

stream = StreamingCompressor(compression_level=3)
stream.add_record(record)
stream.add_batch(records)
stream.finalize() -> bytes
stream.clear()
```

## How It Works

1. **Schema grouping** — records grouped by structure
2. **Columnar storage** — fields stored as columns
3. **Smart encoding** — RLE, Delta, Dictionary per column
4. **MessagePack + Zstandard** — binary serialization + compression

## License

MIT