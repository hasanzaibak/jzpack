# Bounded-writer flatten reuse

The writer already preflights each record before allocating its defensive
snapshot. During that snapshot pass, it now also retains the flattened
schema values and sorted path signature; schema insertion reuses them instead
of recursively flattening the snapshot again. Record validation, snapshot
ownership, chunk/body/payload limits, serialization, and wire bytes are
unchanged. The candidate is source based on `1d4b355bef658d87c74a400d4c1c35f8764272ff`
with `jzpack/writer.py` SHA-256
`74fd13652d8563bf7827d1e71a85bcc92d3552256c7865c79263ca70036d5b78`.
This is a local source experiment, not a measurement of the published 0.5.3
wheel.

On macOS arm64 with Python 3.12.13, msgpack 1.2.3, and zstandard 0.25.0, nine
alternating pairs compared the frozen writer module at the base commit with the
candidate. The same prebuilt corpus was used per case; only encoding to
`BytesIO` was timed. Archive hashing and exact round-trip checks were outside
the timer. Baseline and candidate archives were byte-identical in every case.

| Case | Baseline median | Candidate median | Paired median change | JZPack archive bytes |
|:---|---:|---:|---:|---:|
| Schema-diverse, 50,000 rows | 1,010.73 ms | 856.86 ms | −14.214% | 1,440,234 |
| Nested, 40,000 rows | 1,498.79 ms | 1,424.77 ms | −4.848% | 1,283,701 |
| Large strings, 512 × 16 KiB | 27.45 ms | 26.79 ms | −2.257% | 4,426,106 |

The large-string change is within run noise. Separate before/after lazy-input
memory probes found no measured increase: median traced peaks were 3.231→3.071
MiB (schema-diverse), 2.660→2.660 MiB (nested), and 3.541→3.539 MiB (large
strings). RSS peak deltas were 7.469→7.375 MiB, 6.469→6.125 MiB, and
0→0 MiB respectively. These are three isolated samples per method, not an
interleaved memory comparison; small differences do not establish a memory
reduction.

The same wave-4 suite still showed the writer at about 23× the streamed
MessagePack encode time for schema-diverse rows and 25× for nested rows. The
optimization removes the measured duplicate flatten traversal; it does not
address the larger preflight and body-sizing walks. On the pinned public GH
Archive, parsing the 11,351 source records was outside the timer. Seven rotating
order pairs measured 1,127.05 ms for the frozen writer, 959.10 ms for the
candidate, and 48.66 ms for streamed MessagePack/Zstandard. Both writers
produced the same 3,583,000-byte archive; the streamed archive was 3,160,740
bytes. All three outputs passed the exact round-trip oracle. GH Archive memory
was not measured.

Source-pinned raw captures are retained in [`docs/evidence/performance`](evidence/performance):
[`writer-wave5-cpu-pairs.json`](evidence/performance/writer-wave5-cpu-pairs.json) (SHA-256
`bf3868a0ca8530dc1f7541de3b4e95861e07051b02be21d433a648324e4dd0e0`),
[`writer-wave5-gharchive.json`](evidence/performance/writer-wave5-gharchive.json)
(`dcf57f4f69b0007db786dd4dde436c0406af075d1aca2e3cf1156030e0ae95cd`), and the
paired wave-4 memory probes
([before](evidence/performance/writer-wave4-memory-before.json), SHA-256
`8326678d6c7c50aa6957dee239c25ad3f9c904b18269cd4aa44f5b7197073da9`; [after](evidence/performance/writer-wave4-memory-after.json),
`93fbc5d82c14c494c287acc5d2f6e7f354c16c6f3e8fbba0d6abce696c757369`).
The profile inputs are pinned by `benchmarks/corpus_wave3.py` SHA-256
`596960e45e66cf63e13b47ea08f010dd97e65d0669d1301e13e47f78f912d221`; the
public source file and decompressed NDJSON hashes are recorded in the GH
capture. Results apply to these shapes and this host only.
