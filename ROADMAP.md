# jzpack roadmap

This roadmap tracks the work needed to turn jzpack from a promising Python beta into a durable,
internationally usable data format and ecosystem.

## Completed in 0.3.0

- [x] Validate every record in a batch before using the uniform-schema fast path.
- [x] Preserve empty records with explicit schema row counts.
- [x] Represent nested paths structurally so dotted keys remain lossless.
- [x] Use deterministic, collision-free schema identifiers.
- [x] Add version 2 payloads while retaining a version 1 reader path.
- [x] Add typed format and resource-limit exceptions.
- [x] Validate headers, column encodings, RLE counts, dictionary indices, and row counts.
- [x] Add regression tests for the known silent-corruption cases.
- [x] Add an initial format specification in `FORMAT.md`.
- [x] Add a reproducible local benchmark entry point.
- [x] Add continuous integration for the supported Python versions.

## Next: production reliability

- [x] Add Hypothesis property-based round-trip tests.
- [x] Add malformed-payload fuzzing and cross-version fixture tests.
- [x] Add deterministic-output tests across separate Python processes.
- [x] Add explicit memory and CPU budgets to the benchmark suite.
- [x] Add atomic file writes and file-like object support.

## Next: true streaming and scale

- [ ] Design a chunked JZPK container for bounded-memory compression.
- [ ] Add iterator-based decompression and chunk-level recovery.
- [ ] Add optional schema evolution and projection/selective decoding.
- [ ] Add random-access metadata for large archives.

## Next: international ecosystem

- [ ] Publish the format specification as a versioned standard.
- [ ] Create conformance fixtures independent of Python.
- [ ] Build reference readers/writers for Rust, Go, JavaScript, or Java.
- [ ] Add API documentation, examples, migration guides, and compatibility policy.
- [ ] Add `CONTRIBUTING.md`, `SECURITY.md`, and a code of conduct.
- [ ] Evaluate integrations with Arrow, pandas, log pipelines, and object storage.

The project should stay focused on lossless records, predictable performance, and interoperability
before adding more encoding strategies.
