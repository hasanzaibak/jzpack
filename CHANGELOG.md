# Changelog

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
