# jzpack roadmap

This roadmap tracks the work needed to turn jzpack from a promising Python beta into a durable,
internationally usable data format and ecosystem.

See the [implementation plan](docs/IMPLEMENTATION-PLAN.md) for acceptance gates and
[delivery status](docs/DELIVERY-STATUS.md) for reviewed changes and verification evidence.

## Completed

- [x] Validate every record in a batch before using the uniform-schema fast path.
- [x] Preserve empty records with explicit schema row counts.
- [x] Represent nested paths structurally so dotted keys remain lossless.
- [x] Use deterministic, collision-free schema identifiers.
- [x] Consolidate the public format on the version 3 chunked container.
- [x] Add typed format and resource-limit exceptions.
- [x] Validate headers, column encodings, RLE counts, dictionary indices, and row counts.
- [x] Add regression tests for the known silent-corruption cases.
- [x] Add an initial format specification in `FORMAT.md`.
- [x] Add a reproducible local benchmark entry point.
- [x] Add continuous integration for the supported Python versions.

## Next: exact fidelity and measured performance

- [ ] Specify the supported value contract and verify scalar types and float bits.
- [ ] Repair unsafe delta and RLE selection with regression tests.
- [ ] Map public behavior and failure contracts to meaningful tests; audit duplicate tests.
- [ ] Expand deterministic workload comparisons and publish tradeoffs.
- [ ] Remove repeated schema work and redundant decompression after compatibility checks.

## Next: production reliability

- [x] Add Hypothesis property-based round-trip tests.
- [x] Add malformed-payload fuzzing and version 3 conformance tests.
- [x] Add deterministic-output tests across separate Python processes.
- [x] Add explicit memory and CPU budgets to the benchmark suite.
- [x] Add atomic file writes and file-like object support.

## Next: true streaming and scale

- [x] Design and adopt the chunked JZPK container as the sole public format.
- [x] Add iterator-based decompression and chunk-level recovery.
- [ ] Add a bounded writer that sends independent chunks directly to sinks.
- [ ] Prove row, byte, complexity, and backpressure limits with failure and memory tests.
- [ ] Compare access architectures before selecting a new wire format.
- [ ] Add optional schema evolution and projection/selective decoding.
- [ ] Add random-access metadata for large archives.

## Next: international ecosystem

- [ ] Publish the format specification as a standard.
- [ ] Create conformance fixtures independent of Python.
- [ ] Build reference readers/writers for Rust, Go, JavaScript, or Java.
- [ ] Add API documentation, examples, and a format-stability policy.
- [x] Add `CONTRIBUTING.md` and `SECURITY.md`.
- [x] Add a code of conduct.
- [ ] Evaluate integrations with Arrow, pandas, log pipelines, and object storage.

The project should stay focused on lossless records, predictable performance, and interoperability
before adding more encoding strategies.
