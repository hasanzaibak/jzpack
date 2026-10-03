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
| Schema-diverse | 89.695 | 75.971 | −15.30% |
| Nested records | 395.589 | 395.357 | −0.06% |
| Homogeneous | 55.983 | 41.654 | −25.60% |

Three fresh processes per variant and shape each take five timed samples after
one warmup. Exact values, types, float64 bits, record order, and decoded dict key
order are checked outside the timer. The nested result is within process-level
run variation and does not establish a nested-path speedup. Traced peak memory
rose by under 1 KB per shape; median RSS high-water deltas changed by at most
192 KiB. These memory probes do not establish a memory guarantee. The complete raw
samples, fixed input/archive fingerprints, baseline/candidate source and patch
hashes, harness hash, and runtime details are in the
[schema reconstruction report](benchmarks/results/schema-flat-fastpath-d5653-candidate.json).
This is evidence for the measured synthetic shapes on one macOS ARM64 host, not
a general package speed claim.

## Sixth-wave Delta decoder append path

`DeltaEncoder.decode` now uses an append loop for exact built-in `list` payloads.
List subclasses retain the preallocated indexed path so custom `__len__` and
`__iter__` implementations cannot make reconstruction exceed the prechecked
output size. Existing bounds, malformed-value, integer-range, and float-transition
checks remain in both paths. The focused subclass regression is listed in the
[test coverage map](docs/TEST-COVERAGE.md).

The [comparison harness](benchmarks/compare_delta_decoder_append.py) pins the
indexed baseline to `cfdce1d7f2afe1e251ca26753b06a74fe6fa8aa4`, uses the versioned
corpus at 10,000 and 50,000 records, and records 15 alternating pairs per case.
Two separate process captures retain raw paired samples, paired medians, the
ratio of sample medians, input/archive/output hashes, source and harness hashes,
exact-output checks, runtime versions, and traced Python-allocation peaks:
[capture 1](benchmarks/results/delta-decoder-append-paired.json) and
[capture 2](benchmarks/results/delta-decoder-append-paired-repeat.json). Both
captures used CPython 3.12.13 on macOS 27.2 arm64, jzpack 0.5.5, msgpack 1.2.3,
python-zstandard 0.25.0 (native Zstandard 1.5.7), and orjson 3.12.0.

Paired-median changes for whole-package decompression were consistent across the
two captures on high-entropy, integer-series, and mixed-event records. Values are
percent changes (negative is faster); each cell lists capture 1 / capture 2.

| Profile | 10,000 rows | 50,000 rows | Delta decode calls |
|---|---:|---:|---:|
| High entropy | −3.05% / −4.10% | −3.77% / −2.85% | 1 |
| Integer series | −8.81% / −8.99% | −8.44% / −10.59% | 3 |
| Mixed events | −4.29% / −3.88% | −5.89% / −3.65% | 15 |
| Nested arrays | −2.81% / −0.29% | +0.48% / +2.46% | 2 |
| Optional fields | +0.93% / +2.63% | −0.86% / +17.92% | 0 |

The direct integer-column cases from the integer-series corpus were 18.90–22.70%
faster at 10,000 values and 18.75–24.51% faster at 50,000 values across the two
captures, with all 15 pairs faster in each case. The larger direct gains are
component timings; the table above shows their smaller package-level effect.
Nested-array results vary by size and run, while optional-fields does not call
the delta decoder, so neither supports a shape-level speed claim.

For the delta-bearing package profiles, traced Python peaks rose by 1,440–15,360
bytes at 10,000 records and 23,520–132,960 bytes at 50,000 records across the
measured cases. Direct list-only peaks rose by 5,008 bytes at 10,000 values and
44,208 bytes at 50,000 values. These measurements include reconstructed Python
objects, exclude native allocations, and are not RSS or a memory guarantee. RSS
was not measured. Optional-fields does not exercise this decoder path and showed
run-sensitive allocation peaks; it is not evidence of an allocation change.

These fixed synthetic cases support a targeted decode improvement on this host;
they do not establish universal leadership, cross-platform speed, or general
memory behavior.

## Seventh-wave schema-ingestion dispatch candidate

The unpublished `SchemaManager._flatten` candidate uses identity checks to
avoid `Mapping` ABC checks for built-in leaf values while retaining recursive
handling for dict and generic `Mapping` subclasses. The baseline method was
loaded from commit `900911fa2060578f9a1dce9c73c0c48dcceae036`; only `_flatten`
differs between timed variants. Each case and `fast` mode uses prebuilt fixed
inputs, one warmup, and nine alternating public `compress(..., level=3)` pairs.
Archive-byte equality and the corpus fidelity oracle are checked outside the
timer. All paired archives matched byte-for-byte and all round trips passed.

The first capture's medians are milliseconds, baseline → candidate; parentheses
show median time reduction and candidate wins among nine pairs.

| Profile | Rows | `fast=False` | `fast=True` |
|---|---:|---:|---:|
| Schema-diverse | 10,000 | 45.513 → 38.685 (15.0%; 9/9) | 40.271 → 32.754 (18.7%; 9/9) |
| Schema-diverse | 50,000 | 231.388 → 192.368 (16.9%; 9/9) | 207.644 → 169.416 (18.4%; 9/9) |
| Nested records | 10,000 | 39.793 → 33.289 (16.3%; 8/9) | 33.714 → 27.389 (18.8%; 8/9) |
| Nested records | 50,000 | 198.071 → 167.285 (15.5%; 7/9) | 174.831 → 143.943 (17.7%; 7/9) |
| Integer series | 50,000 | 89.007 → 77.080 (13.4%; 9/9) | 62.406 → 49.503 (20.7%; 9/9) |

A fresh-process capture repeated the nested cases. Its 10,000-row median
encode-time reductions were 16.1% (8/9 pairs) with `fast=False` and 18.3% (8/9)
with `fast=True`. At 50,000 rows the reductions were 15.6% (7/9) and 17.3%
(7/9), respectively. Both captures contain outliers, so these paired medians are
a workload-specific signal, not a performance guarantee. Memory was not measured.

The source-pinned raw [first capture](benchmarks/results/schema-flatten-dispatch-candidate.json)
and [nested repeat](benchmarks/results/schema-flatten-dispatch-nested-repeat.json)
retain every pair, archive and corpus hashes, and capture-time source, patch,
harness, and runtime provenance. Each report keeps the original measurement
script hash as `capture_harness_sha256` and records the current replay-capable
script hash as `replay_harness_sha256`; the timed-loop logic did not change.
The current [first harness](benchmarks/compare_schema_flatten_dispatch.py) and
[repeat harness](benchmarks/compare_schema_flatten_dispatch_repeat.py) accept a
clean descendant of the baseline and derive provenance from committed,
staged, unstaged, and untracked changes. A temporary committed-descendant smoke
at `5f5c3f327c107df65e17858f483a01b8ed610fc8` passed through both harnesses,
including exact archive and fidelity checks; its smoke timings are excluded
from the table. The replay-script SHA-256 values are
`fb634bdb00a7d773c239c1ae18f9e678deca8ef326e92e00d0d3a7b77048ab24` and
`593d6821261f82045c2eb3183bf37b7dae527ec34484474b768587bed280d6a2`.
The candidate schema source hash is
`363678543b32a41998ebc116dbef4827c08c7f67419f73955bfc8dcb0c87c045`; the test
source hash is `e27671e2231de90991d175fec83727797ceeb42860838be19baa49bd4e7649e2`.
Both captures used CPython 3.12.13 on macOS 27.2 arm64 with msgpack 1.2.3 and
python-zstandard 0.25.0.

This branch candidate is separate from published 0.5.6. It has not been shown to
improve other runtimes or workloads, and no universal performance leadership is
claimed.

## Limits of these results

These are small, in-memory synthetic workloads on one macOS ARM64 machine. They show behavior for
these exact generated shapes and dependency versions; they do not establish general compression,
throughput, or memory guarantees. The high-repetition integer, event, and nested-array profiles are
especially compressible. The slower MessagePack/orjson baselines on jzpack decode do not imply that
jzpack is a faster serialization format overall; their archive size and CPU tradeoffs differ by
workload. Public-data, tuned Parquet/Vortex, log-specific CLP, and streaming file-I/O comparisons
remain outside this run.
