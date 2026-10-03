# Bounded v3 writer

`write_records(records, sink, **limits)` writes JZPK v3 chunks as records arrive. It accepts a
single built-in dictionary or an iterable of built-in dictionaries. `sink` is either a filesystem
path (`str` or `os.PathLike[str]`) or a caller-owned binary object with a synchronous `write`
method. The function returns the total number of archive bytes written.

```python
from jzpack import write_records

with open("events.ndjson", "rb") as source:
    records = (decode_event(line) for line in source)
    written = write_records(records, "events.jzpk")
```

## Bounds and chunk policy

Every limit has a finite positive default:

| Keyword | Default | Meaning |
|:---|---:|:---|
| `target_chunk_input_bytes` | 1 MiB | Soft chunk target: sum of exact MessagePack byte sizes of input records |
| `max_chunk_records` | 4,096 | Maximum input records per chunk |
| `max_record_bytes` | 1 MiB | Maximum exact MessagePack size of one input record |
| `max_chunk_input_bytes` | 4 MiB | Hard sum of input MessagePack bytes retained for a chunk |
| `max_chunk_uncompressed_bytes` | 8 MiB | Hard exact uncompressed inner MessagePack body size |
| `max_chunk_payload_bytes` | 8 MiB | Hard compressed inner payload size, including the frame prefix |
| `max_schemas` | 256 | Distinct schema definitions in one chunk |
| `max_paths` | 8,192 | Aggregate flattened path entries across schema definitions |
| `max_path_bytes` | 1 MiB | Aggregate UTF-8 path-component bytes across those entries |
| `max_depth` | 64 | Maximum nested built-in dict/list container depth; root record depth is one; configurable from 1 through 128 |
| `max_nodes` | 65,536 | Aggregate input nodes retained for one chunk |

`target_chunk_input_bytes` counts `msgpack.packb(record, use_bin_type=True)` bytes exactly. Before
adding a valid next record, the writer flushes a non-empty chunk if the target, row, hard input-byte,
schema, path, path-byte, or node bound would be crossed. A singleton may exceed the soft target,
but must fit the record, hard chunk-input, and singleton metadata bounds. The exact final encoded
body size is checked before allocating the body; the compressed payload size is checked after
compression. If either encoded-output hard limit is exceeded, writing fails with
`ResourceLimitError`. The writer does not split an encoded body or emit a footer after such a
failure. This policy preserves the v3 grammar and avoids rebuilding the growing candidate body for
each new input row.

The API also accepts `compression_level` from 1 through 22 (default 3) and `fast` (default `False`).
The input-byte boundary policy does not depend on compression analysis or compressed-size
variability. With the same ordered inputs, limits, and codec implementation, chunk boundaries are
deterministic.

## Input and snapshot behavior

Records may contain built-in dictionaries with string keys, built-in lists, `None`, booleans,
strings, bytes, binary64 floats, and integers in MessagePack's signed-64/unsigned-64 range. The
writer rejects tuples, custom values, container subclasses, non-string keys, cycles, and integers
outside that range. `max_depth` counts only nested built-in dictionaries and lists. For example,
`{"a": 1}` has depth one, and `{"a": {"b": 1}}` has depth two.

Each record is validated, measured for its exact input MessagePack size and structural complexity,
and defensively snapshotted in one bounded traversal. The writer checks byte, node, depth, and path
limits before retaining the corresponding value or flattened path. A successful record's snapshot
is complete before the writer advances the input iterator, so generators may safely reuse and mutate
a yielded container after the next call to `next()`. At most one such prepared record is held as
lookahead while a full chunk is synchronously written.

A late validation failure can occur after part of that record has been copied into temporary
snapshot and flattened-value structures; those structures are discarded when the write fails. Their
growth remains subject to the record and structural limits. If `max_chunk_input_bytes` is crossed,
the writer stops retaining more of the snapshot, completes validation under the other limits, and
then reports the chunk-input error if no later validation error takes precedence. This preserves the
validation-before-flush behavior without retaining content beyond the byte cap. The encoded chunk
body size is still checked before the final MessagePack body is allocated.

## Sink ownership and failures

For caller-owned binary streams, writes are synchronous and provide backpressure. Positive short
writes are retried using a view of the remaining buffer. A `None`, boolean, non-integer, zero,
negative, or overlarge write count is rejected. The writer neither closes nor flushes the caller's
stream. A stream error, generator exception, cancellation, validation error, or encoding-limit
failure can leave a partial archive without a valid terminal footer; the caller owns that stream's
recovery.

For filesystem paths, the writer creates a temporary file in the destination directory, writes the
complete archive, flushes and syncs it, then atomically replaces the destination. If iteration,
encoding, writing, syncing, or replacement fails before that final replace, an existing destination
remains unchanged and the temporary file is removed.

The writer does not retain the complete input or output. Its retained input is limited by chunk row,
input-byte, node, schema, path, path-byte, and depth limits, plus one prepared lookahead record.
Encoding also needs Python objects and temporary buffers; Zstandard uses native workspace. Thus
these limits make working memory finite for bounded records but do not promise one fixed RSS value
across Python, platform, allocator, or Zstandard versions. The measurement below reports Python
`tracemalloc` allocations separately from process peak RSS and labels the startup/warmup contribution.

## Memory probe

Run the isolated-process probe from the repository root:

```bash
PYTHONPATH=. python -m benchmarks.writer_memory \
  --rows 25000 100000 400000 --samples 3 --warmup-rows 1000
```

The source is a deterministic generator and the sink discards output, so neither retains all rows or
archive bytes. Every size uses the same default limits. Each sample runs in a fresh subprocess,
warms up the writer first, and reports the archive byte count, peak Python-traced allocation, process
peak RSS including startup/warmup, and the change above the pre-measurement RSS high-water mark.
The measurement runs with `tracemalloc` enabled, so the RSS high-water mark includes tracing
overhead; it is not production RSS or an isolated measurement of native workspace. The RSS delta
is only a lower-bound signal when warmup already established a larger high-water mark. The probe
does not make a 10 GB or universal RSS claim.

### Recorded run

Command: `PYTHONPATH=. /tmp/jzpack-research-env/bin/python -m benchmarks.writer_memory --rows 25000 100000 400000 --samples 3 --warmup-rows 1000`.
The [complete raw JSON](../benchmarks/results/writer-memory-20261001.json) is checked in with this
documentation. Values below are the raw three samples in order, with the median shown first; byte
counts are decimal.

| Rows | Discarded archive bytes, median (samples) | Python peak bytes, median (samples) | Process RSS peak bytes incl. startup, median (samples) | RSS high-water delta after warmup, median (samples) |
|---:|---:|---:|---:|---:|
| 25,000 | 1,870 (1,870; 1,870; 1,870) | 884,435 (884,435; 884,435; 884,435) | 24,985,600 (24,985,600; 25,231,360; 24,969,216) | 2,949,120 (2,932,736; 3,194,880; 2,949,120) |
| 100,000 | 7,107 (7,107; 7,107; 7,107) | 904,405 (904,405; 904,405; 904,405) | 25,526,272 (25,640,960; 25,247,744; 25,526,272) | 3,473,408 (3,653,632; 3,211,264; 3,473,408) |
| 400,000 | 30,920 (30,920; 30,920; 30,920) | 945,093 (945,093; 945,093; 945,093) | 25,296,896 (25,296,896; 26,017,792; 25,133,056) | 3,244,032 (3,194,880; 3,817,472; 3,244,032) |

Environment: Python 3.12.13, jzpack 0.4.0, msgpack 1.2.3, zstandard 0.25.0, macOS 27.2 arm64.
This is one repeated-schema, small-record synthetic profile; it does not measure schema-diverse,
large-record, high-entropy, or larger-source workloads.
Python-traced peak rose from about 0.88 MB to 0.95 MB as total rows increased 16-fold, while RSS
including process startup varied between about 25.0 MB and 25.5 MB at the medians. RSS high-water
deltas were noisy and non-monotonic. This small synthetic run is consistent with the configured
chunk bounds, but does not establish a general RSS ceiling; it does not represent arbitrary records,
allocator states, operating systems, or Zstandard workspaces.

## Encoding-time comparison

The bounded preflight also avoids rebuilding the candidate chunk body for each record. Two repeated,
alternating five-sample comparisons against revision `2a7af898` measured 50,000-record schema-diverse
inputs and 40,000-record nested inputs. The same deterministic corpus was used on both sides; exact
archive bytes and round trips matched in every sample. Lazy-input timing includes record generation,
while prebuilt-list timing excludes corpus creation. Both runs used Python 3.12.13 on macOS arm64.

| Records and input | Median wall time saved | Median CPU time saved |
|:---|---:|---:|
| 50,000, schema-diverse, prebuilt list | 4.40–4.47% | 4.40–4.45% |
| 50,000, schema-diverse, lazy iterator | 3.12–3.37% | 3.12–3.37% |
| 40,000, nested records, prebuilt list | 10.03–10.30% | 10.03–10.30% |
| 40,000, nested records, lazy iterator | 8.28–9.47% | 8.28–9.52% |

The raw paired measurements, hashes, and environment are available in [run 2](../benchmarks/results/writer-preflight-2a7af-run2.json)
and [run 3](../benchmarks/results/writer-preflight-2a7af-run3.json). These are workload-specific results
from one machine and do not establish a universal speedup.
