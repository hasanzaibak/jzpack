# Changelog

## Unreleased
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
