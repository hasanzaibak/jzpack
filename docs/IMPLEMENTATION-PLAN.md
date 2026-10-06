# jzpack implementation plan

This is the single editable plan for upcoming work.

Published releases and open verification gaps belong in [delivery
status](DELIVERY-STATUS.md). Public API and wire behavior belong in [README](../README.md)
and [FORMAT](../FORMAT.md). Test contracts and remaining coverage gaps belong in the
[test coverage map](TEST-COVERAGE.md).

## Product boundary

Keep public claims within the supported-value contract and version 3 format
documented in [FORMAT](../FORMAT.md).

Treat the test coverage map and independent regression fixtures as acceptance
evidence, not a test-count target.

Keep the current Python package, format, and dependencies stable while measurements
guide the next optimization. The bounded writer reuses flattened values from its
defensive snapshot and preflights records before chunking, removing repeated schema
work and candidate chunk-body rebuilding; measurements show gains on selected shapes
while a sizable gap to the streamed comparator remains. Its limits, exact-value
behavior, and output integrity remain required contracts; see [the writer profile](WRITER-CPU-PROFILE-WAVE5.md),
[writer resource model and measurements](WRITER.md), [the streamed comparison](STREAMED-BASELINE.md),
and the [benchmark index](../BENCHMARKS.md).

Published 0.5.6 appends reconstructed DELTA values for exact built-in lists;
list subclasses retain indexed reconstruction and its prior error behavior under
inconsistent length and iteration methods. Two source-pinned captures show
repeatable package-level median-time reductions for the measured high-entropy,
integer-series, and mixed-event profiles, with small traced-allocation increases.
Nested-array results are inconclusive, optional-fields does not invoke delta
decoding, and RSS was not measured. See the
[paired Delta decoder evidence](../BENCHMARKS.md#sixth-wave-delta-decoder-append-path)
and [0.5.6 delivery ledger](RELEASES.md#verified-deliveries).

Published 0.5.7 includes identity-based dispatch for built-in leaf values in
`SchemaManager._flatten`, while retaining the recursive path for dict and generic
`Mapping` subclasses. The first capture shows 13.4–20.7% public encode median-time
reductions on selected synthetic profiles; the fresh-process nested repeat shows
15.6–18.3% reductions, with timing outliers. Exact archive bytes and corpus-oracle
round trips match, but memory was not measured. See the
[schema-ingestion measurements](../BENCHMARKS.md#seventh-wave-schema-ingestion-dispatch-candidate).

## Planned work

1. **Completed: profile writer body sizing and decoder reconstruction.** The built-in
   MessagePack scalar-size candidate showed 3.26–6.15% median encode reductions on three
   scalar-heavy synthetic profiles and was effectively flat for large strings, with exact
   archive bytes and round trips. The decoder sibling-group candidate was rejected after it regressed
   four tested profiles. See the [eighth-wave capture](../BENCHMARKS.md#eighth-wave-messagepack-scalar-size-fast-path)
   and [decoder no-go evidence](../BENCHMARKS.md#decoder-sibling-group-reconstruction-no-go).
   Keep limits and exact failure behavior as release gates.
2. **Completed: reduce nested schema-ingestion work in public `compress()`.** Uniform
   exact built-in dict batches now stage values directly into columns, with the existing
   flattening path retained for custom mappings and unsupported values. This applies the
   column-oriented staging idea used for scan locality without adding Arrow or changing
   JZPack's wire format; see the [Apache Arrow columnar overview](https://arrow.apache.org/docs/format/Columnar.html).
   Two independent nine-pair captures show 28.64% and 29.00% median speedups on 40,000
   nested records.
   Large-string and schema-diverse controls stayed within the 2% median-regression limit;
   every archive remained byte-identical and every exact round trip passed. The first
   all-target gate also required an 8% large-string gain; the candidate did not meet that
   part, so the accepted scope is explicitly nested-record ingestion, not general
   `compress()` acceleration. The suite passed 524 tests. A read-only differential check
   matched schema groups, order, and exception/state outcomes on 1,200 generated batches.
   Memory was not measured. See the [paired captures](../BENCHMARKS.md#ninth-wave-direct-column-nested-batch-ingestion).
3. **Completed diagnostic: sweep Zstandard levels for large-string `compress()`.**
   A public-call level sweep found that large-string results, nested records, and schema-diverse
   inputs favor different speed/size tradeoffs. Native macOS ARM64 and a pinned Linux/ARM64
   container produced the same archive sizes at all levels and the same tradeoff direction.
   Keep the level-3 default and do not add an automatic level policy from three synthetic
   workloads. See the
   [source and results](../BENCHMARKS.md#tenth-wave-zstandard-compression-level-frontier).
4. **Completed diagnostic: test the level frontier on held-out realistic data.** Nine
   balanced-order timing samples across synthetic FHIR bundles, country features, and earthquake
   events show that level 1 is faster but produces larger archives, while level 9 is smaller but
   slower. Isolated process RSS includes input and runtime overhead, so it does not establish a
   codec-only memory ranking. Keep level 3 as the general default and do not add an automatic
   policy from these three corpora. See the
   [source, method, and results](../BENCHMARKS.md#eleventh-wave-held-out-realistic-compression-level-frontier).
5. **Conditional: reduce peak memory in large `compress()` calls.** Measure memory separately,
   then prototype shorter MessagePack-body lifetimes or incremental MessagePack emission into
   the Zstandard streaming writer. Preserve v3 framing, checksums, and error behavior; require
   exact serialized payload and corpus round trips. The official
   [python-zstandard streaming API](https://python-zstandard.readthedocs.io/en/0.25.0/compressor.html)
   supports chunked input/output, while the [MessagePack Packer API](https://msgpack-python.readthedocs.io/en/stable/api.html)
   exposes array/map header and repeated-pack operations. A prototype must verify exact bytes;
   no CPU speedup is claimed. Native Zstandard threads remain a large-payload experiment:
   the [binding documents per-operation overhead, extra memory work, and a possible small
   output-size cost](https://python-zstandard.readthedocs.io/en/0.25.0/multithreaded.html).
6. **Conditional: choose an access design from demonstrated needs.** Compare a version 3
   sidecar index, a new wire format, and an existing columnar backend only against
   concrete read workloads. Preserve missing-versus-null, scalar types, float bits,
   bounds, compatibility, and install cost in that comparison. Defer a new format until
   evidence justifies its maintenance and migration cost.
7. **Conditional: defer ecosystem expansion until usage supports it.** Cross-language
   readers, adapters, and broader APIs should follow demonstrated adoption needs and a
   shared conformance corpus, rather than lead the package roadmap.

## Decision gate

Keep each accepted change inside the documented API and format contract,
protect it with meaningful regression or conformance checks, and publish only claims
directly supported by retained evidence.

A faster measurement does not justify weaker limits. A successful round trip on
one input does not establish universal fidelity, bounded memory, broad performance
leadership, or adoption.

## Evidence and source documents

The top-level roadmap remains a pointer instead of a second checklist. This
plan covers future work; delivery status summarizes the current release state; release
records and benchmark reports retain detailed publication and measurement provenance.

- [README](../README.md) — installation and public API guidance.
- [FORMAT](../FORMAT.md) — supported values, wire grammar, compatibility,
  and reader limits.
- [Test coverage map](TEST-COVERAGE.md) — named tests, fixtures, and visible gaps.
- [Delivery status](DELIVERY-STATUS.md) — published release records and current caveats.
- [Benchmark index](../BENCHMARKS.md) — measured comparisons and links to
  source-pinned reports and raw results.
- [Release procedure](RELEASES.md) — publication gates.
