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

**Realistic Data** (mixed repetition, 19.68 MB):

| Strategy | Size | Ratio | Compress | Decompress |
|:---|---:|---:|---:|---:|
| json + gzip | 5.71 MB | 3.45x | 14 MB/s | 96 MB/s |
| orjson + zstd | 5.86 MB | 3.36x | 227 MB/s | 149 MB/s |
| msgpack + zstd | 5.71 MB | 3.44x | 154 MB/s | 122 MB/s |
| **jzpack** | 4.56 MB | 4.32x | 99 MB/s | 93 MB/s |
| **jzpack (fast)** | 4.89 MB | 4.03x | 117 MB/s | 92 MB/s |

**High Cardinality** (worst case, 35.58 MB):

| Strategy | Size | Ratio | Compress | Decompress |
|:---|---:|---:|---:|---:|
| json + gzip | 19.94 MB | 1.78x | 14 MB/s | 95 MB/s |
| orjson + zstd | 19.61 MB | 1.81x | 160 MB/s | 126 MB/s |
| msgpack + zstd | 19.04 MB | 1.87x | 126 MB/s | 163 MB/s |
| **jzpack** | 15.86 MB | 2.24x | 116 MB/s | 104 MB/s |
| **jzpack (fast)** | 16.09 MB | 2.21x | 125 MB/s | 117 MB/s |

**vs msgpack+zstd**: 17-20% smaller, 65-90% of the speed.

Run: `python benchmark.py`

## When to Use

- Cold storage and archival
- Network transfer where bandwidth matters
- Datasets with field repetition or low cardinality

## API

```python
from jzpack import compress, decompress, JZPackCompressor, StreamingCompressor

# Simple API
compressed = compress(data, level=3, fast=False)
original = decompress(compressed)

# Class-based API
compressor = JZPackCompressor(compression_level=3, fast=False)
compressor.compress(data)
compressor.decompress(data)
compressor.compress_to_file(data, "out.jzpk")
compressor.decompress_from_file("out.jzpk")

# Streaming API
stream = StreamingCompressor(compression_level=3, fast=False)
stream.add_record(record)
stream.add_batch(records)
stream.finalize()
stream.clear()
```

**Parameters:**
- `level`: zstd compression level 1-22 (default: 3)
- `fast`: skip column encoding analysis for speed (default: False)

## How It Works

1. **Schema grouping** — records grouped by field structure
2. **Columnar storage** — fields stored as columns
3. **Smart encoding** — RLE, Delta, Dictionary per column type
4. **MessagePack + Zstandard** — binary serialization + compression

## License

MIT
