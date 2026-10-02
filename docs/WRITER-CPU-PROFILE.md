# Writer CPU profile

This profile locates Python work in the bounded v3 writer. Its cProfile wall
times are diagnostic only: instrumentation changes timings, native extension
work is not broken into internal frames, and `compress()` and `write_records()`
use different chunk policies. The archive sizes and hashes are retained
independently in the report; equality between those APIs is not expected.

The profiled package version was 0.5.0 at source commit
`3a487571e652a4ab321a4847502ae189e2a13c3e`. The `writer.py` bytes match the
writer at the comparison base `3f2e37e1db3c3b9653fc52bc02330128d8157440`.
That later base includes a decode-only change in `schema.py`; the writer and
encoding sources were unchanged. Exact runtime, corpus, and profiling-tool
hashes are recorded in
[`writer-cpu-wave3-profile.json`](../benchmarks/results/writer-cpu-wave3-profile.json).
The exact historical profiling source as run is preserved in
[`writer-cpu-wave3-profile-harness.py.txt`](../benchmarks/results/writer-cpu-wave3-profile-harness.py.txt)
(SHA-256 `324731b4a680e3d42a0c9bf04ed7db1fa60fade8a25c25bf7341da30ca5629f7`).
That historical capture used a separate local wave-3 worktree. Since then, the
live profile harness changed its corpus root to the current checkout and
replaced abbreviated source checks with full SHA-256 checks. The retained
source artifact contains the original absolute worktree path and is retained
only as capture evidence, not as a portable executable; the profile JSON also
remains the original capture. New runs record the current harness hash. Both
live harnesses now read the frozen corpus generator and comparison harness
from this repository and verify their full SHA-256 values.

The run used Python 3.12.13, msgpack 1.2.3, zstandard 0.25.0, compression
level 3, and one cProfile pass per method. It profiled schema-diverse 50,000
records and nested-records 40,000 records from the frozen wave-3 generator.
For the comparable in-memory versus writer views, both calls received the same
prebuilt list and corpus construction was outside the profile. A third writer
view used `iter_records` and includes generator work; it is reported separately.
All writer calls wrote to plain `io.BytesIO` without the benchmark's per-write
hashing or first-chunk timer. After each profile, the archive was decoded and
checked against the wave-3 recursive built-in-type and float64-bit oracle.

| Input | `compress(list)` profile wall | `write_records(list)` profile wall | Writer inspector cumulative | MessagePack size visitor cumulative | UTF-8 sizing calls / cumulative | Snapshot cumulative |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Schema-diverse, 50k | 0.581 s | 3.830 s | 1.584 s | 1.058 s | 1,257,667 / 0.607 s | 0.323 s |
| Nested records, 40k | 0.368 s | 6.612 s | 3.189 s | 2.046 s | 2,201,638 / 1.050 s | 0.744 s |

The largest writer costs in both workloads are the bounded input inspection
and the bounded encoded-body size walk. Nested input also spends time in the
recursive value inspector and defensive snapshot. The UTF-8 helper was called
over one million times in each workload; its self time was 0.405 s and 0.692 s
respectively. These counts identify a low-risk optimization candidate, not a
measured speedup.

The experiment keeps the hard byte checks and all non-ASCII behavior: after the
existing character-count upper-bound check, ASCII strings return their
character count because ASCII UTF-8 uses one byte per character. Other strings
continue through the existing bounded UTF-8 encoder, preserving multibyte
accounting and `UnicodeEncodeError` behavior for surrogates.

An alternating paired run compared the unmodified writer at base
`3f2e37e1db3c3b9653fc52bc02330128d8157440` with the candidate. Each of three
pairs timed both variants, alternating which variant ran first across pairs.
The same deterministic input was used on each side; the prebuilt-list mode
excluded corpus construction, while the lazy mode includes generator work.
Imports, archive checks, and hashes were outside the timer. All 24 timed calls
produced byte-identical before/after archives and passed the recursive
type/float64-bit round-trip oracle.

| Input | Mode | Baseline median | Candidate median | Candidate / baseline | Archive bytes and SHA-256 |
| --- | --- | ---: | ---: | ---: | --- |
| Schema-diverse, 50k | Prebuilt list | 1.082 s | 0.937 s | 0.867 | 1,440,234 / `efa68fde9810d7ace59ea08461abd14246c1cd4fff11a73c1bec83434ecd74bb` |
| Schema-diverse, 50k | Lazy generator | 1.378 s | 1.292 s | 0.938 | 1,440,234 / `efa68fde9810d7ace59ea08461abd14246c1cd4fff11a73c1bec83434ecd74bb` |
| Nested records, 40k | Prebuilt list | 1.691 s | 1.425 s | 0.842 | 1,283,701 / `6f57de7c47d8a955b780df481496ddd7eb811002f5850f0f225e77383a1a6701` |
| Nested records, 40k | Lazy generator | 1.768 s | 1.492 s | 0.844 | 1,283,701 / `6f57de7c47d8a955b780df481496ddd7eb811002f5850f0f225e77383a1a6701` |

These are three paired samples on one Python 3.12.13/macOS arm64 environment,
so they support this narrow change on these workloads only. Raw samples and
exact before/after source hashes are retained in
[`writer-ascii-paired-wave3.json`](../benchmarks/results/writer-ascii-paired-wave3.json).
The baseline writer hash is `12298714f7180385d75646946fcf5b10585966a5654bb90afaf02584632378a0`;
the candidate hash is `01f5613e83027cf90c9f81d62dc866f759deec94fa4be235eb1366a07f88661d`.
The candidate was uncommitted during capture. The raw report's
`candidate_revision` records the measurement checkout HEAD (`3f2e37e…`), which
is also the baseline revision; it does not identify the patched writer bytes.
The complete runtime source hashes identify those bytes. The unchanged
candidate was subsequently committed at `d7dd1d9`.
Combining validation with the defensive snapshot is a broader future
experiment and was not included here.

Run a fresh profile from a checkout containing the frozen wave-3 source files:

```sh
PYTHONPATH=. python benchmarks/profile_writer_cpu.py \
  --output /tmp/writer-cpu-profile.json
```

Both commands use source files in their own checkout, so a separate corpus
worktree is not required. The live profile command checks both frozen source
files against their full hashes before profiling. The historical retained
report is not recreated by this command; a new report describes the checkout
and runtime sources used for that run. The paired comparison also requires the
pinned base Git object, so a shallow clone without it cannot reproduce the
baseline. Run it from a checkout containing that object and the frozen wave-3
source files:

```sh
PYTHONPATH=. python benchmarks/compare_writer_ascii.py --samples 3 \
  --output /tmp/writer-ascii-paired.json
```

For this historical paired comparison, use the candidate checkout at
`d7dd1d9` with the other runtime sources matching the pinned baseline. The tool
fails closed if any of those sources changed, so a later integration containing
additional runtime changes cannot reproduce this isolated ASCII comparison
directly. The live profiling tool can describe a later checkout independently.
`tests/test_writer_profile_contracts.py` checks loading from an ordinary checkout
and rejection of altered frozen corpus sources for both tools. Git attributes
retain LF bytes for Python sources and JSON/historical source evidence; a real
checkout test with CRLF conversion enabled verifies the pinned source hashes.

The profile report contains sanitized function names, source paths, call
counts, and times; the paired report contains input fingerprints, exact
archive hashes, raw samples, and source hashes. Neither contains corpus record
contents.
