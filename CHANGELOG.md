# Changelog

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
