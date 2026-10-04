# jzpack

JSON-record compression using columnar encoding, MessagePack, and Zstandard.

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

## Workloads and performance

jzpack stores record-shaped JSON and NDJSON data in a columnar binary archive. Size and speed
depend on the data, runtime, and hardware; benchmark results do not predict every workload. See
[the benchmark guide](https://github.com/hasanzaibak/jzpack/blob/main/BENCHMARKS.md) for tested
workloads, reproduction steps, and limitations.

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

# Incremental in-memory ingestion
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

`write_records` accepts a built-in dictionary or iterable of them, emitting independent v3 chunks to
a binary sink or filesystem path without retaining the complete input. It returns the number of
archive bytes written.

```python
import json

from jzpack import write_records

with open("events.ndjson", "r", encoding="utf-8") as source:
    records = (json.loads(line) for line in source)
    written = write_records(records, "events.jzpk")
```

The writer applies finite row, byte, schema, path, node, and depth limits. It retries positive short
writes to a caller-owned binary sink, which remains open; a failure may leave a partial archive. For
a path, it writes to a same-directory temporary file and atomically replaces the destination after
success. Exact defaults, validation rules, and failure behavior are in the
[writer resource model](https://github.com/hasanzaibak/jzpack/blob/main/docs/WRITER.md).

### File helpers

`compress_to_file` and `decompress_from_file` accept paths and binary file-like objects. Path writes
use a temporary file and atomic replacement; streams use their current position and remain open.
These helpers buffer the complete JZPK payload and are not bounded-memory APIs. `compress_to_file`
returns the compressed byte count.

```python
import io

buffer = io.BytesIO()
compressor.compress_to_file(data, buffer)
buffer.seek(0)
assert compressor.decompress_from_file(buffer) == data
```

**Parameters:**
- `level`: Zstandard compression level 1-22 (default: 3)
- `fast`: skip column encoding analysis for speed (default: False)
- `max_output_size`: optional decompression limit in bytes
- `max_records`: optional decompression limit in records

Compression-level tradeoffs depend on the records being compressed. A higher level does not
guarantee a smaller archive for every input. Benchmark representative data before tuning; see the
[measured level tradeoffs](https://github.com/hasanzaibak/jzpack/blob/main/BENCHMARKS.md#tenth-wave-zstandard-compression-level-frontier).

`compress` accepts a mapping, a list of mappings, or an iterable of mappings; mapping keys must be
strings. Supported values include `None`, booleans, strings, bytes, MessagePack-range integers,
binary64 floats, lists, and nested mappings. It preserves missing versus explicit null and exact
float bits; mapping key order is not guaranteed. Tuples may be returned as lists, and jzpack does
not provide a custom conversion hook. See the
[format specification](https://github.com/hasanzaibak/jzpack/blob/main/FORMAT.md) for details.

Older v3 archives remain readable, including historical numeric DELTA payloads. Some earlier
writers could lose float or mixed numeric distinctions before the archive was stored, and those
original values cannot be recovered by a newer reader.

`StreamingCompressor` accumulates column data until `finalize()` and emits the same v3 format as
`compress`; it is not a bounded-memory archive writer. Use `write_records` above for bounded-memory
output to a binary sink or path. See the
[roadmap](https://github.com/hasanzaibak/jzpack/blob/main/ROADMAP.md).
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
iteration. See the [format specification](https://github.com/hasanzaibak/jzpack/blob/main/FORMAT.md).

Malformed payloads raise typed exceptions exported from the package, including
`InvalidFormatError`, `UnsupportedVersionError`, and `ResourceLimitError`.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
```

## License

MIT
