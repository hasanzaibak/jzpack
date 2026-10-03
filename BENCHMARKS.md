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
the performance-only candidate (`ffca027589cfc91815f4a2a8f1b380e86d6e64d6`), based on
`dc5a618370e98153cceb0aee7c5c8cccc55f9e83`. These figures exclude the separate fidelity repairs. Both runs used the same corpus version, seed, row count, compression level, warmups,
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

## Integrated fidelity checkpoint

The full first-wave implementation at `9b12fc2028afabb426600f487802681fc17db76f` combines the
performance changes with exact type/float-bit preservation and stricter codec validation. Its
raw result is `benchmarks/results/corpus-v1-wave-1.json`, using the same 3,000 records, three
samples, one warmup, seed, level, and dependency versions. Every archive checksum matches the
original corpus run, and each round trip passes the exact oracle.

| Profile | Full-package encode median ms | Full-package decode median ms |
|---|---:|---:|
| High entropy | 4.344 | 1.437 |
| Integer series | 6.181 | 1.926 |
| Mixed events | 10.707 | 3.795 |
| Nested arrays | 8.000 | 4.743 |
| Optional fields | 6.754 | 4.735 |

This checkpoint was run later than the component-only comparison, so timing differences include
run-to-run variation. It demonstrates why component improvements cannot be advertised as
full-package speedups: preserving values and rejecting malformed codecs adds Python validation
work. The second-wave codec optimization below profiles and reduces that cost while retaining
fidelity and reader safety checks. Neither timing series establishes general superiority.

## Second-wave integrated checkpoint

At code checkpoint `c7266ae7fb75c5155f865497e5961670fa518f89`, the codec guard optimization
(`b1be6a7`) and bounded writer (`042b3f7`) are integrated with the first wave. The raw result is
`benchmarks/results/corpus-v1-wave-2.json`. It uses the same 3,000-record corpus, seed 1729,
level 3, three samples, one warmup, and current dependency versions. This timing run skips RSS;
the bounded writer has a separate [memory probe](docs/WRITER.md).

| Profile | Reused encode median ms | Reused decode median ms |
|---|---:|---:|
| High entropy | 3.714 | 1.289 |
| Integer series | 4.943 | 1.506 |
| Mixed events | 9.696 | 3.411 |
| Nested arrays | 7.033 | 4.075 |
| Optional fields | 6.578 | 5.339 |

Every corpus input fingerprint, jzpack archive size/checksum, and exact round trip matches the
original run. These timings exercise the existing in-memory compressor/decompressor API; they
do not measure the new direct writer's throughput. They are a later host measurement, so they
cannot isolate causal improvements from run-to-run variation. Optional-field decoding is slower
than the first-wave checkpoint in this run; no across-the-board speedup is claimed. The
[codec cost report](docs/CODEC-COSTS.md) retains profiles, matched source comparisons, two final
candidate runs, and a pre-final timing outlier with its repeat.

## Third-wave evidence

The [paired reconstruction report](docs/CPU-WAVE-3.md) isolates a six-line schema reconstruction
change on five 10,000-record profiles, with 15 alternating baseline/candidate samples and exact
round-trip checks. Mixed-event and nested-array decode medians decreased about 8–9%; the other
profiles show small changes or regressions. The pinned raw report includes source hashes and
separates the final paired capture from earlier host measurements.

The [expanded corpus](docs/CORPUS-WAVE-3.md) retains a separate versioned generator and raw report
for larger schema-diverse, nested-record, and exact-size large-string inputs. It compares the list
API, bounded writer, full-list MessagePack/Zstd, and only those Parquet/Zstd cases passing a
full-dataset fidelity check. Decode timing materializes lists for every comparator. Separate
isolated memory probes use lazy writer input and a hashing discard sink; timed writer runs retain the
output archive and include hashing/chunk-observer overhead absent from the other encode timers;
the report discloses this limitation. The writer lowers traced allocations but costs more encode CPU and can produce
larger archives. At that historical checkpoint, a streamed MessagePack baseline, real public datasets, and additional host
measurements were still needed before claiming memory or throughput leadership. The fourth-wave
comparison below supplies the streamed synthetic baseline.

## Fourth-wave targeted improvements

The [writer CPU profile and paired comparison](docs/WRITER-CPU-PROFILE.md) isolate
the ASCII size helper on two larger synthetic profiles in prebuilt and lazy
input modes. Three alternating pairs measure encode median reductions of
6.2–15.8%, with byte-identical archives and exact round trips. The retained
capture contains uncommitted candidate source hashes; its checkout HEAD is
the baseline commit, and the unchanged writer was subsequently committed.
These results support that helper change on the measured host and shapes.

The [single-run RLE comparison](docs/RLE-SINGLE-RUN.md) shows a 7.9% public decode
median reduction for a 10,000-row constant-column archive across 15 pairs.
Nested and schema-diverse median shifts are small and inconclusive. The much
larger direct-codec reductions do not describe whole-package throughput.

The [streamed row comparison](docs/STREAMED-BASELINE.md) removes per-write hashing
from every encode timer and uses an incremental MessagePack-array/Zstd baseline.
Decode timing materializes lists for all methods; separate lazy-source memory
probes use the same hashing discard sink. First-record probes distinguish
validated JZ chunks from a forced-flush row available before final frame
validation. That forced-flush probe is a separate policy from the normal
streamed archive-size/encode measurements.

On the schema-diverse and nested profiles, the JZPack writer takes about 27–28
times longer to encode than the streamed baseline and has higher measured encode
memory, while the baseline archives are 17.1% and 26.8% larger. Large-string
encode times are similar. These measurements justify investigating repeated
Python record walks and a bounded batch prototype; they do not establish native
implementation benefits in advance.

The [bounded-writer flatten-reuse profile](docs/WRITER-CPU-PROFILE-WAVE5.md)
removes one repeated schema-flattening walk by building the flat values during
the existing defensive snapshot pass. Nine alternating pairs show a 14.214%
median encode-time reduction for 50,000 schema-diverse records and 4.848% for
40,000 nested records; the large-string result is within run noise. All writer
archives matched byte-for-byte. The profile still measures about 23× and 25×
the streamed comparator time for the schema-diverse and nested shapes, and it
does not eliminate the separate preflight and body-sizing passes.

A separate [public GitHub event check](docs/evidence/public-gharchive-fidelity-wave4.json)
round-trips all 11,351 parsed records through both list and lazy writer APIs in
current and minimum runtimes. The [source record](docs/evidence/public-gharchive-source.json)
pins the official GH Archive download. A companion encoding-only capture in the
flatten-reuse profile reports a 14.9% median improvement over the prior writer
on these parsed records; parsing is outside the timer, and the capture includes
no memory measurement. Its streamed comparator is about 20× faster and writes a
different, smaller archive. These results are one source and one host, not a
cross-platform ranking; compressed bytes can also differ across dependency versions.

## Fifth-wave shallow schema reconstruction

The shallow-path specialization in `SchemaReconstructor.reconstruct_records`
binds flat fields and columns once per schema, then builds rows with a dict
comprehension. Schemas containing any nested path retain the existing row
builder. The fixed-archive comparison against base `d5653b8` uses 50,000
schema-diverse rows, 40,000 nested rows, and a 50,000-row homogeneous control;
each case uses the same pre-change archive hash. Decode medians are milliseconds.

| Shape | Baseline | Candidate | Change |
|---|---:|---:|---:|
| Schema-diverse | 89.508 | 75.783 | −15.33% |
| Nested records | 394.642 | 379.653 | −3.80% |
| Homogeneous | 54.225 | 41.196 | −24.03% |

Three fresh processes per variant and shape each take five timed samples after
one warmup. Exact values, types, float64 bits, record order, and decoded dict key
order are checked outside the timer. The nested result is within process-level
run variation and does not establish a nested-path speedup. Traced peak memory
rose by under 1 KB per shape; median RSS high-water deltas were unchanged. These
memory probes do not establish a memory guarantee. The complete raw
samples, fixed input/archive fingerprints, baseline/candidate source and patch
hashes, harness hash, and runtime details are in the
[schema reconstruction report](benchmarks/results/schema-flat-fastpath-d5653-candidate.json).
This is evidence for the measured synthetic shapes on one macOS ARM64 host, not
a general package speed claim.

## Limits of these results

These are small, in-memory synthetic workloads on one macOS ARM64 machine. They show behavior for
these exact generated shapes and dependency versions; they do not establish general compression,
throughput, or memory guarantees. The high-repetition integer, event, and nested-array profiles are
especially compressible. The slower MessagePack/orjson baselines on jzpack decode do not imply that
jzpack is a faster serialization format overall; their archive size and CPU tradeoffs differ by
workload. Public-data, tuned Parquet/Vortex, log-specific CLP, and streaming file-I/O comparisons
remain outside this run.
