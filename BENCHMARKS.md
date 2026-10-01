# Benchmark corpus and method

The existing `benchmarks/benchmark.py` command remains the small, deterministic jzpack round-trip
benchmark. Its JSON fields, stdout behavior, exit codes, and Python-allocation budget are unchanged.
Use `benchmarks/benchmark_corpus.py` for reproducible comparisons across workload shapes and row
oriented baselines:

```bash
PYTHONPATH=. python benchmarks/benchmark_corpus.py \
  --records 3000 --iterations 3 --warmups 1 --seed 1729 --level 3 --json \
  > /tmp/jzpack-corpus.json
```

The comparison command reports raw timing and output-size samples, medians, nearest-rank p95, source
and compressed-output SHA-256 checksums, exact round-trip status, dependency versions, Python
allocation peaks, and an isolated-process RSS measurement for jzpack. Use `--skip-rss` to skip the
separate process measurement. The comparison CLI has no CPU or memory budgets; the existing
benchmark CLI continues to own its original budget contract.

## Corpus v1

All inputs are synthetic JSON-compatible records generated in memory. No public dataset, user
records, environment variables, or credentials are read. `corpus.py` defines the versioned
generators and a fixed SplitMix64 generator for seed-dependent fields. The default seed is `1729`.
The ordered corpus fingerprint hashes canonical JSON records joined by newlines; the reference byte
count is the sum of the canonical record bytes without newlines. Canonical JSON uses sorted keys,
compact separators, and UTF-8.

| Profile | Shape represented |
|---|---|
| `mixed-events` | Four event families with distinct nested paths and event-specific fields |
| `optional-fields` | Twelve sparse integer fields over a common record shape |
| `high-entropy` | Seeded, 64-character high-cardinality text values |
| `nested-arrays` | Nested account maps, item maps, and arrays of arrays |
| `integer-series` | Monotonic sequence and nanosecond timestamps plus repeated device IDs |

The benchmark's recursive oracle checks exact primitive types and recursively checks list order and
mapping contents. It compares float IEEE-754 bits, including signed zero, even though corpus v1 has
no float workload. Mapping key order is not treated as semantic. Baseline compressed sizes are raw
Zstandard frames; jzpack sizes include its complete v3 container. No file or network I/O is timed.

`msgpack-zstd` is always available with jzpack's required runtime dependencies. `orjson-zstd` uses
the optional `orjson` package and is omitted when it is not installed. Both baselines use Zstandard
level 3 and checksums. `msgpack-zstd` times MessagePack packing with Zstandard compression;
`orjson-zstd` times orjson serialization with Zstandard compression. Decode timing includes
decompression and deserialization.

Cold timings include construction of the jzpack compressor or baseline Zstandard context inside the
measured operation. Reused timings construct that context before measurement and reuse it across
the measured samples. Each mode receives one warmup by default. The result stores every sample and
reports its median, minimum, maximum, and nearest-rank p95; with three samples, p95 is the maximum.
The checksum and exact oracle are checked outside timed intervals.

For jzpack decode, “reused” means the same `JZPackCompressor` API object is used for each sample;
the v3 reader still creates its chunk decompression state for each decode call. The measurement does
not imply reuse of the underlying Zstandard decode context.

Python allocation peaks come from `tracemalloc` around one isolated encode or decode operation and
exclude native allocations. RSS uses `resource.getrusage(RUSAGE_SELF).ru_maxrss` in one fresh
subprocess per profile, with generated input, compressed bytes, and restored records all in scope.
The RSS number is a process high-water mark, not a per-operation delta; it includes interpreter and
input/output object overhead. Platforms without the standard-library `resource` module report RSS as
unavailable. Neither memory figure is a stable regression gate.

## Recorded before-and-after run

The files under `benchmarks/results/` contain the raw JSON outputs for the base implementation and
this candidate. Both runs used the same corpus version, seed, row count, compression level, warmups,
iterations, Python process, and optional dependencies. Runtime: Python 3.12.13, macOS 27.2 arm64,
jzpack 0.4.0, msgpack 1.2.3, python-zstandard 0.25.0 (native Zstandard 1.5.7), and orjson 3.12.0.

Each profile contained 3,000 records, with one warmup and three measured samples at level 3. The
timing cells are median milliseconds, `base → candidate`. Sizes are from the candidate; exact
checksums and all raw timing samples are in the result files.

| Profile | Canonical JSON bytes | MessagePack + Zstd bytes | orjson + Zstd bytes | jzpack bytes | jzpack encode ms | jzpack decode ms | jzpack process peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| High entropy | 511,890 | 313,090 | 303,679 | 301,285 | 4.529 → 3.370 | 1.598 → 1.256 | 34.12 → 33.27 |
| Integer series | 264,301 | 15,121 | 16,150 | 406 | 5.900 → 3.979 | 1.466 → 1.250 | 30.34 → 30.58 |
| Mixed events | 402,321 | 25,882 | 29,301 | 928 | 7.821 → 7.731 | 2.926 → 2.920 | 32.61 → 32.58 |
| Nested arrays | 537,718 | 27,075 | 34,235 | 9,536 | 9.556 → 6.585 | 4.320 → 4.337 | 38.25 → 37.72 |
| Optional fields | 206,664 | 32,093 | 30,941 | 31,151 | 6.192 → 6.091 | 4.453 → 4.178 | 33.36 → 33.31 |

The candidate produced byte-for-byte identical archives for every profile and implementation; every
round trip passed the type-sensitive oracle. Flatten-once ingestion improved the measured jzpack
encode median for high entropy by 26%, integer series by 33%, and nested arrays by 31%; mixed events
and optional fields were about 1% faster. The strict single-pass frame decoder improved decode
medians by 21% on high entropy, 15% on integer series, and 6% on optional fields, was effectively
flat on mixed events, and was 0.4% slower on nested arrays. That small nested-array change is within
the noise expected from three timing samples; all values are retained in the raw result files.

The uniform-batch optimization retains flattened records until validation completes, trading
additional temporary Python allocations for less repeated work. Reused-encode allocation peaks
rose from 0.578 to 1.279 MiB on integer series and from 0.707 to 1.379 MiB on nested arrays in
these runs. These are traced allocations, separate from total process RSS.

RSS differences stayed within 0.9 MiB across these small isolated runs. At 3,000 rows, this is not
evidence of a large-scale memory bound. Re-run with representative local workloads before using the
numbers for capacity planning.

For repeatable dependency versions in a Python 3.12 environment, install:

```bash
python -m pip install "msgpack==1.2.3" "zstandard==0.25.0" "orjson==3.12.0"
```

To compare the strict decompression API against the declared minimum python-zstandard version,
install `zstandard==0.21.0` in a separate environment and run the performance-contract and chunk
tests. The strict single-frame behavior uses the documented `allow_extra_data=False` option, present
in the [python-zstandard 0.21.0 decompression API](https://python-zstandard.readthedocs.io/en/0.21.0/decompressor.html).

## Limits of these results

These are small, in-memory synthetic workloads on one macOS ARM64 machine. They show behavior for
these exact generated shapes and dependency versions; they do not establish general compression,
throughput, or memory guarantees. The high-repetition integer, event, and nested-array profiles are
especially compressible. The slower MessagePack/orjson baselines on jzpack decode do not imply that
jzpack is a faster serialization format overall; their archive size and CPU tradeoffs differ by
workload. Public-data, tuned Parquet/Vortex, log-specific CLP, and streaming file-I/O comparisons
remain outside this run.
