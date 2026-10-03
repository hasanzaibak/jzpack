# Changelog

## Unreleased

## 0.5.4 — 2026-10-03

- Reuse the flattened values from the bounded writer's defensive snapshot instead of walking each prepared record again for schema insertion.
- Add regressions for reused generator records, nested empty mappings, exact float bit patterns, and validation failure ordering.
- Retain source-pinned writer CPU and public GH Archive captures, with timings and comparator limits stated explicitly.
- Add hosted stable Python 3.14 coverage and classifiers.

## 0.5.3 — 2026-10-03

- Fix README links for PyPI by using absolute repository URLs and shorten the package introduction.
- Exclude generated test/tool caches from source distributions.
- Consolidate duplicated delivery and implementation prose into current, usable guidance.
- Update GitHub build/test/artifact actions to supported Node.js 24 releases while retaining publication gates.

## 0.5.2 — 2026-10-02

- Size bounded ASCII strings without temporary UTF-8 encoding; preserve multibyte, surrogate, and hard-limit behavior.
- Decode validated single-run RLE columns with list repetition after all count and output-limit checks.
- Retain paired measurements, exact-value and boundary regressions, portable diagnostic source checks, and Git line-ending contracts.
- Compare full-list and streamed MessagePack/Zstd with symmetric encode timing, exact round trips, isolated encode-memory probes, and explicit integrity/first-record caveats.

## 0.5.1 — 2026-10-01

- Reconstruct two-segment nested schema paths directly, with exact-value regression coverage and retained paired decode measurements.
- Add larger schema-diverse, nested-record, and large-string corpus comparisons with isolated memory probes and explicit streaming tradeoffs.
- Verify three critical codec fault mutations in disposable copies; require separate unit and public-container regressions to detect each fault.

## 0.5.0 — 2026-10-01
- Add `write_records` for bounded v3 chunks, synchronous short-write handling, defensive record snapshots, and atomic path output.
- Reduce redundant codec validation while retaining exact types, float bits, integer bounds, and legacy float DELTA reads.
- Add bounded-writer properties, resource boundaries, cancellation/backpressure/fault tests, and comparative benchmark CLI contracts.
- Preserve exact scalar types and binary64 bits when selecting RLE; keep nested values RAW.
- Restrict DELTA writes to supported integer values/deltas while retaining historical float DELTA reads.
- Reject malformed column fields, run counts, numeric deltas, and dictionary indices with controlled errors.
- Reject sink writes that return None instead of a byte count; document caller recovery.
- Reuse flattened batch records and decode strict Zstandard frames in one pass.
- Add meaningful codec, schema, file-failure, resource-boundary, and lifecycle tests; remove five duplicates.
- Add deterministic comparative workloads with raw evidence, explicit tradeoffs, and dependency/platform CI.

## 0.4.0
- Consolidated the public format on the JZPK version 3 chunked container.
- Added chunk-aware iteration and explicit recoverable-chunk error reporting.
- Added strict container framing, CRC-32C integrity checks, footer validation, and decode resource limits.
- Rejected standalone legacy JZPK versions 1 and 2; version 2 remains an internal chunk payload detail.

## 0.3.0
- Fixed silent data loss for outlier schemas in batches.
- Preserved empty records and dotted keys during round trips.
- Added deterministic schema identifiers and explicit row counts.
- Added JZPK format version 2 with a version 1 reader path.
- Added malformed-payload validation, checksums, and decompression limits.
- Added typed format exceptions, regression tests, CI, a format specification, and benchmarks.

## 0.2.0
- 2x compression speed improvement
- Added `fast` mode (skip encoding analysis)
- Refactored internals for maintainability

## 0.1.2
- Initial stable release
