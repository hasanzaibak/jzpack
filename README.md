# jzpack

High-compression JSON-record storage using columnar encoding, MessagePack, and Zstandard.

Status: beta. The public API is intentionally small, while the JZPK binary format is being
stabilized for long-term and cross-language use.

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

Run the local benchmark with `python benchmarks/benchmark.py`. Benchmark results depend on
hardware, Python, dependency versions, and dataset shape; treat the table above as a reference,
not a guarantee.

The benchmark is opt-in and uses deterministic records. For a smaller local run:

```bash
python benchmarks/benchmark.py --records 1000 --iterations 3 --level 3
python benchmarks/benchmark.py --records 1000 --iterations 3 --json
```

Human-readable output reports average, minimum, maximum, and population standard deviation for
compression and decompression. Timing uses `time.perf_counter()` and CPU budget flags apply to the
reported average:

```bash
python benchmarks/benchmark.py --records 1000 --iterations 3 \
  --max-compress-seconds 1.0 \
  --max-decompress-seconds 1.0 \
  --max-memory-mib 128
```

`--max-memory-mib` applies to the maximum `peak_python_memory_mib` observed across compression and
decompression in all iterations. This portable metric is the peak Python allocation traced by
`tracemalloc`; it includes Python objects and buffers tracked by `tracemalloc`, but excludes native
Zstandard allocations and total process RSS. The reference input size is the UTF-8 byte count of
canonical JSON records (sorted keys, compact separators, and no newline), not a JZPK payload size.

On successful benchmark completion—including a completed run that exceeds a budget—`--json` writes
exactly one JSON object on stdout containing configuration, dataset sizes, safe runtime/dependency
versions, timing samples, memory samples, round-trip status, and budget status. If `argparse` or
benchmark execution fails before a result exists, stdout may be empty and diagnostics go to stderr.
Budget violations return exit status 1; invalid CLI values are rejected by `argparse` with exit
status 2; benchmark execution failures return exit status 3. The benchmark does not print records,
paths, environment variables, or credentials.

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
compressed = compressor.compress(data)
compressor.decompress(compressed)
compressor.compress_to_file(data, "out.jzpk")
compressor.decompress_from_file("out.jzpk")

# Streaming API
stream = StreamingCompressor(compression_level=3, fast=False)
stream.add_record(record)
stream.add_batch(records)
stream.finalize()
stream.clear()
```

### File helpers

`compress_to_file` and `decompress_from_file` accept `str` paths, `os.PathLike[str]` paths such as
`pathlib.Path`, and binary file-like objects with `write(bytes)` or `read()` methods. Filesystem
path destinations are written to a temporary file in the destination directory and atomically
replaced after the complete JZPK payload has been written, flushed, and synced. A generic
file-like stream is caller-managed and does not receive atomic replacement semantics.

```python
import io

buffer = io.BytesIO()
compressor.compress_to_file(data, buffer)
buffer.seek(0)
assert compressor.decompress_from_file(buffer) == data
```

File-like objects are never closed, and both operations use the stream's current position. The
file helpers still buffer the complete JZPK payload in memory; they are not bounded-memory
streaming APIs. `compress_to_file` returns the compressed byte count for both paths and streams.

**Parameters:**
- `level`: zstd compression level 1-22 (default: 3)
- `fast`: skip column encoding analysis for speed (default: False)
- `max_output_size`: optional decompression limit in bytes
- `max_records`: optional decompression limit in records

`compress` accepts a mapping, a list of mappings, or any iterable of mappings. Record keys must
be strings. Nested mappings, Unicode text, lists, numbers, booleans, nulls, and bytes supported
by MessagePack are preserved. The format does not normalize or translate Unicode values.

`StreamingCompressor` currently buffers its column data until `finalize()`. It is useful for
incremental ingestion, but it is not yet a bounded-memory file writer; see [ROADMAP.md](ROADMAP.md).

## How It Works

1. **Schema grouping** — records grouped by field structure
2. **Columnar storage** — fields stored as columns
3. **Smart encoding** — RLE, Delta, Dictionary per column type
4. **MessagePack + Zstandard** — binary serialization + compression

## Format and compatibility

JZPK version 2 is the current writer format. It includes deterministic schema identifiers,
explicit row counts, and collision-safe nested paths. Readers accept version 1 and version 2
headers where the legacy payload is structurally valid. The format is documented in
[FORMAT.md](FORMAT.md).

Malformed payloads raise typed exceptions exported from the package, including
`InvalidFormatError`, `UnsupportedVersionError`, and `ResourceLimitError`.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

The project roadmap is maintained in [ROADMAP.md](ROADMAP.md).

## License

MIT
