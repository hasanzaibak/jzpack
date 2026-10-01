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
from jzpack import (
    JZPackCompressor,
    StreamingCompressor,
    compress,
    decompress,
    iter_decompress,
    iter_decompress_recover,
    write_records,
)

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

### Chunked v3 reads

JZPK version 3 is the sole public wire format. `compress` emits a v3 container, and
`iter_decompress` reads one chunk at a time from paths and binary streams without an unbounded
`read()` call. Standalone v1/v2 payloads are unsupported; the v2 payload embedded inside a v3 chunk
is an internal format detail.

```python
from jzpack import ChunkError, ChunkRecords, iter_decompress, iter_decompress_recover

for record in iter_decompress("archive-v3.jzpk", max_chunks=1000):
    consume(record)

# Recovery is explicit: ChunkError means the output is incomplete, and no
# records from its sequence are yielded.
for event in iter_decompress_recover("archive-v3.jzpk"):
    if isinstance(event, ChunkRecords):
        consume_many(event.records)
    else:
        assert isinstance(event, ChunkError)
        report_corrupt_chunk(event.sequence, event.error)
```

Both iterator functions accept bytes-like input, paths, and binary file-like objects. Their optional
limits are `max_output_size`, `max_records`, `max_chunks`, `max_chunk_uncompressed_bytes`, and
`max_chunk_payload_bytes`. `max_output_size` is the aggregate uncompressed MessagePack body size.
`decompress` also accepts valid v3 bytes as a list-returning adapter; its returned list is naturally
not bounded-memory.

### Bounded v3 writing

`write_records` sends independent v3 chunks directly to a binary sink or writes a filesystem path
through a same-directory temporary file and atomic replacement. It consumes an iterable without
retaining the full input and returns the number of archive bytes written.

```python
import json

from jzpack import write_records

with open("events.ndjson", "r", encoding="utf-8") as source:
    records = (json.loads(line) for line in source)

    written = write_records(records, "events.jzpk", max_chunk_records=2_000, max_record_bytes=256_000)
```

The defaults bound each record to 1 MiB, each chunk to 4 MiB of summed input MessagePack bytes and
8 MiB each of uncompressed body and compressed payload, and each chunk to 4,096 rows, 256 schemas,
8,192 flattened paths, 1 MiB of aggregate path bytes, depth 64, and 65,536 nodes. The target is 1
MiB of input MessagePack bytes per chunk; a singleton may exceed that soft target only within the
hard record and chunk limits. Crossing a row, target, schema, path, node, or input-byte limit ends
the current chunk before the next valid record. Encoded-body or compressed-payload violations
raise `ResourceLimitError`; they do not split the finished encoded chunk.

For a binary stream, the writer retries positive short writes synchronously to provide backpressure.
It does not close or flush the caller's stream. If validation, encoding, cancellation, or a sink
write fails, the stream may contain an incomplete archive without a terminal footer. For a path,
failures before replacement leave an existing destination intact and remove the temporary file.

Records must use built-in dictionaries and lists, string keys, supported scalar values, and integers
within the MessagePack range. Tuples, container subclasses, custom objects, and cycles are rejected.
The writer snapshots each record before advancing the input iterator, so a generator may reuse and
mutate its container after yielding it. Limits bound retained rows and structural metadata, but do
not promise a fixed process RSS across Python and Zstandard versions. See
[WRITER.md](docs/WRITER.md) for the resource model and isolated RSS/allocation probe.

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
A binary stream must return an integer byte count from `write(bytes)`. Short writes raise
`OSError`; invalid return types, including `None`, raise `TypeError`. A failed stream write may
already have accepted some bytes; the caller owns recovery and the stream remains open.

**Parameters:**
- `level`: zstd compression level 1-22 (default: 3)
- `fast`: skip column encoding analysis for speed (default: False)
- `max_output_size`: optional decompression limit in bytes
- `max_records`: optional decompression limit in records

`compress` accepts a mapping, a list of mappings, or any iterable of mappings. Record and nested
mapping keys must be strings. The fidelity guarantee covers `None`, booleans, strings, bytes,
MessagePack-range integers, binary64 floats, lists, and nested mappings. It preserves missing versus
explicit null and the exact float bit pattern, including signed zero, infinities, and NaN payloads;
mapping key order is not guaranteed. Strings are not normalized. See [FORMAT.md](FORMAT.md) for
integer bounds and codec behavior.

Tuples may be encoded as arrays and returned as lists, so tuple identity is outside the guarantee.
Custom objects and container subclasses are also outside it; unsupported values ordinarily fail
during serialization. jzpack does not provide a custom conversion hook.

Older v3 archives remain readable, including historical numeric DELTA payloads. Some earlier
writers could lose float or mixed numeric distinctions before the archive was stored, and those
original values cannot be recovered by a newer reader.

`StreamingCompressor` currently buffers its column data until `finalize()`, which emits the same v3
format as `compress`. It is useful for incremental ingestion, but it is not yet a bounded-memory file
writer; see [ROADMAP.md](ROADMAP.md).
`add_batch` is not atomic: after a validation failure it may retain a prefix of valid records.
Call `clear()` or discard that compressor before restarting a failed ingestion.

## How It Works

1. **Schema grouping** — records grouped by field structure
2. **Columnar storage** — fields stored as columns
3. **Smart encoding** — RLE, Delta, Dictionary per column type
4. **MessagePack + Zstandard** — binary serialization + compression

## Format and compatibility

JZPK version 3 is the sole reader and writer format. It includes deterministic schema identifiers,
explicit row counts, collision-safe nested paths, chunk integrity checks, and bounded sequential
iteration. The format is documented in [FORMAT.md](FORMAT.md).

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
