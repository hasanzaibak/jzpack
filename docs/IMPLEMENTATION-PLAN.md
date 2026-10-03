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

The merged 0.5.7 release candidate adds identity-based dispatch for built-in
leaf values in `SchemaManager._flatten`, while retaining the recursive path for dict
and generic `Mapping` subclasses. The first capture shows 13.4–20.7% public encode
median-time reductions on selected synthetic profiles; the fresh-process nested
repeat shows 15.6–18.3% reductions, with timing outliers. Exact archive bytes and
corpus-oracle round trips match, but memory was not measured. This candidate is
separate from published 0.5.6, with 0.5.7 publication pending; see the
[schema-ingestion measurements](../BENCHMARKS.md#seventh-wave-schema-ingestion-dispatch-candidate).

## Planned work

1. **Planned: profile encoded-body sizing and schema reconstruction.** After publishing
   the schema-ingestion change, profile the remaining writer body-sizing and decoder
   reconstruction passes as the next distinct costs. Accept changes only when exact-value
   and failure tests pass, resource limits remain enforced before allocation, and paired
   measurements include CPU, memory, and archive size.
2. **Planned: expand scale evidence.** Repeat representative measurements on larger real
   inputs and additional supported environments. Keep parsing, archive work, memory, and
   integrity boundaries explicit; distinguish fidelity checks from performance results.
3. **Conditional: choose an access design from demonstrated needs.** Compare a version 3
   sidecar index, a new wire format, and an existing columnar backend only against
   concrete read workloads. Preserve missing-versus-null, scalar types, float bits,
   bounds, compatibility, and install cost in that comparison. Defer a new format until
   evidence justifies its maintenance and migration cost.
4. **Conditional: defer ecosystem expansion until usage supports it.** Cross-language
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
