# Larger corpus comparison (wave 3)

Wave 3 adds a separate, seeded corpus and harness for larger and differently shaped records. It does not
change the v1 corpus or its goldens in `benchmarks/corpus.py`. The complete machine-readable sample set is
[`benchmarks/results/corpus-wave-3.json`](../benchmarks/results/corpus-wave-3.json).

The retained capture used runtime base `3e87f770be076cac166caf7fd35de22c81c3782b`
(the package metadata then reported 0.4.0), with the new benchmark sources now committed in
`748baf7`. It predates the shallow reconstruction optimization and is not a measurement of the
complete installed 0.5.1 distribution. The benchmark and generator SHA-256 fields pin the actual
measurement code. The 0.5.1 reconstruction change has a separate [paired report](CPU-WAVE-3.md);
the writer and encoder runtime paths are unchanged from this corpus capture.

## Corpus and methods

The harness uses corpus version 2, seed `20261001`, compression level 3, one warmup, and three timed
samples. It exercises these profiles at two sizes each:

| Profile | Counts | Shape |
|:---|:---:|:---|
| `schema-diverse` | 10,000 / 50,000 | Twelve optional fields with absent and explicit-null cases, several fixed primitive types, and signed-zero floats; the largest case has 665 distinct top-level key sets. |
| `nested-records` | 10,000 / 40,000 | Nested account and trace maps, varying span and label lists, nested attributes, and float arrays containing signed zero. |
| `large-strings` | 128 / 512 | Fixed-shape rows with a pseudo-random ASCII payload of exactly 16,384 bytes per record plus ID, category, checksum, and signed-zero score. |

Every input has a canonical JSON byte count and SHA-256 fingerprint. The wave-3 oracle recursively compares
complete record streams, exact built-in types, mapping keys, list lengths, scalar values, and binary64 float
bits. It checks the full output length, so an extra or missing trailing record fails the comparison.

The required comparisons are JZPack's list API (`compress` / `decompress`), its bounded writer
(`write_records` / `iter_decompress`), and a full-list MessagePack plus one Zstandard frame. PyArrow's
Parquet/Zstandard path is optional and is added to an individual workload only when a full-dataset write
and read passes the same exact oracle. In this run, Parquet is eligible for `nested-records` and
`large-strings`. It is excluded for `schema-diverse`: PyArrow materializes a missing optional key as a
null-valued key, first differing at `$[1].optional_06`. The preflight and its work are outside timed
samples. When included, Parquet's timed encode includes `Table.from_pylist`, the Parquet write, and copying
the resulting buffer to bytes.

PyArrow is not a project dependency. Its Parquet API is documented in the [PyArrow Parquet guide](https://arrow.apache.org/docs/python/parquet.html).
If PyArrow is absent, the result records it as unavailable and skips the Parquet mode. The [Vortex Python
API](https://docs.vortex.dev/api/python/) was not available in the measurement environment and is not
included as a comparator in this wave.

## Results

The following rows report the largest case of each profile. Times are medians of three samples on macOS
27.2 arm64 with Python 3.12.13, JZPack 0.4.0, msgpack 1.2.3, Zstandard 0.25.0, and PyArrow 25.0.1. The
raw file includes every timing, output hash, sample count, environment field, and memory probe.
It also records the SHA-256 of the exact benchmark script and corpus generator used for this run. All
timed samples produced a stable archive hash per method and passed the exact-output check.

| Profile (rows; canonical input bytes) | Mode | Archive bytes | Encode ms | Decode ms | First output |
|:---|:---|---:|---:|---:|:---|
| `schema-diverse` (50,000; 14,363,207) | JZPack list | 1,266,330 | 234.6 | 78.3 | Full archive, 234.6 ms |
| | JZPack writer | 1,440,234 | 1,090.3 | 132.3 | First complete chunk, 48.4 ms (2,247 rows) |
| | MessagePack + Zstandard | 1,674,766 | 29.3 | 55.6 | Full archive, 29.3 ms |
| `nested-records` (40,000; 19,567,513) | JZPack list | 913,354 | 160.6 | 260.1 | Full archive, 160.6 ms |
| | JZPack writer | 1,283,701 | 1,636.1 | 261.4 | First complete chunk, 38.9 ms (957 rows) |
| | MessagePack + Zstandard | 1,653,286 | 49.2 | 119.8 | Full archive, 49.2 ms |
| | Parquet + Zstandard | 959,575 | 87.1 | 74.2 | Full archive, 87.1 ms |
| `large-strings` (512; 8,462,482) | JZPack list | 4,434,767 | 24.4 | 6.7 | Full archive, 24.4 ms |
| | JZPack writer | 4,426,106 | 29.4 | 6.4 | First complete chunk, 3.4 ms (63 rows) |
| | MessagePack + Zstandard | 4,440,797 | 23.1 | 6.1 | Full archive, 23.1 ms |
| | Parquet + Zstandard | 4,435,162 | 23.2 | 6.5 | Full archive, 23.2 ms |

For non-writer modes, “first output” means the entire archive has been returned. Those values are not
first-row latency and are not directly comparable to the writer's first complete nonempty chunk. The
writer's end-to-end encode time includes all chunks and a sink that retains and hashes the in-memory
archive. The separately reported first chunk time uses that same sink and starts with a prebuilt input
list; it excludes corpus generation and downstream consumption. The writer observer hashes bytes and
checks first-chunk completion inside its encode timer; the other timed methods fingerprint their
returned archives outside the timer. The reported encode ratios therefore include this asymmetric
instrumentation cost and do not isolate the core encoder difference. A subsequent comparison should
remove that hashing from retained-output timing or apply equal instrumentation to every method.

Each largest-case mode also ran three isolated encode-only memory probes. `tracemalloc` records Python
allocations made during the encode after a 128-row warmup. RSS is the process high-water mark; the
reported delta is the high-water increase over the post-warmup mark. This process-level RSS includes
native allocations and the overhead of active `tracemalloc`. Absolute peaks include interpreter startup
and warmup. A `0.0` delta means the later high-water mark did not exceed warmup; it does not mean that
the operation used no memory. The JSON field `elapsed_seconds_with_tracemalloc` is recorded for
reproducibility, but is not comparable with the ordinary timed encode medians because allocation tracing
is active. The writer probe reads a lazy corpus generator and hashes output to a discard sink. The other
current baselines materialize all rows and retain a serialized output buffer.

| Largest profile | Mode | Python traced peak (MiB) | RSS high-water increase after warmup (MiB) |
|:---|:---|---:|---:|
| `schema-diverse` (50,000) | JZPack list | 92.0 | 228.2 |
| | JZPack writer | 3.2 | 7.3 |
| | MessagePack + Zstandard | 94.0 | 199.9 |
| `nested-records` (40,000) | JZPack list | 126.1 | 284.7 |
| | JZPack writer | 2.7 | 6.6 |
| | MessagePack + Zstandard | 125.6 | 251.1 |
| | Parquet + Zstandard | 95.0 | 273.8 |
| `large-strings` (512) | JZPack list | 29.0 | 35.0 |
| | JZPack writer | 3.5 | 0.0* |
| | MessagePack + Zstandard | 32.4 | 22.0 |
| | Parquet + Zstandard | 12.4 | 49.6 |

These results show a concrete tradeoff for this implementation and these inputs: compared with the JZPack
list API, the writer lowered traced peak allocation by about 96.5% for schema-diverse rows, 97.9% for
nested rows, and 87.8% for large strings. Its encode medians were about 4.6×, 10.2×, and 1.2× higher,
respectively; its archives were about 13.7% and 40.6% larger for the first two profiles and 0.2% smaller
for large strings. The writer produced its first complete chunk in 48.4 ms, 38.9 ms, and 3.4 ms for those
same workloads. These measurements are workload- and machine-specific, not a general ranking.

## Limits and reproduction

The MessagePack comparator serializes the complete row list into one Zstandard frame. This wave does not
measure a record-by-record MessagePack/Zstandard writer and reader, so it does not establish a memory
advantage over a streaming MessagePack implementation. It also does not measure file-system throughput,
streaming decode memory, other machines, production-shaped datasets, or Vortex. Parquet results cover only
the exact-round-trip-eligible types and records in this generated corpus; they do not imply that arbitrary
JZPack values fit Parquet's type model.

Run the benchmark and focused contracts from the repository root:

```bash
PYTHONPATH=. python benchmarks/benchmark_corpus_wave3.py \
  --suite wave-3 --samples 3 --warmups 1 --memory-samples 3
PYTHONPATH=. python -m pytest -q tests/test_benchmark_corpus_wave3.py
```

The benchmark suite has no timing or RSS thresholds. Its tests pin small profile fingerprints and check
the CLI schema, exact round trips, full-dataset Parquet eligibility, deterministic hashes, first-chunk
framing, and isolated memory-probe output identity. A short smoke run is available with
`--suite smoke --samples 1 --warmups 0 --skip-memory`.
