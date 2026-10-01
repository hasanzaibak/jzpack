# jzpack: proposed path to an exceptional package

[Likely] The product should make one demanding job easy: turn messy JSON event records into a compact, portable archive, then retrieve the useful records with predictable memory and exact supported-value fidelity. Begin with Python developers archiving API exports and application events; treat broader adoption as an expansion strategy.

[Certain] This plan follows the repository and comparative research. It records proposed work and acceptance gates, not performance guarantees. See [delivery status](DELIVERY-STATUS.md) for implementation evidence.

## Product promise and measurable objectives

[Likely] A useful public promise would be: “Compact archives for changing JSON records. Stream them in, inspect what you need, and preserve what you put in.” Qualify preservation with a precise supported-data contract rather than promising byte-identical JSON source text.

[Certain] The current mapping API receives parsed records, so it cannot reconstruct original whitespace, number spelling, duplicate textual object keys, or original JSON bytes. These are distinct from preserving record order, missing fields, nulls, types, nested structure, Unicode values, and supported numeric representations.

[Likely] Prioritize these objectives in this order:

1. Exact supported-value fidelity and correctly rejected malformed input.
2. Predictable working memory and latency to the first output/result.
3. End-to-end CPU time, including parsing and object materialization.
4. Compressed size on representative target workloads.
5. Integration and installation friction.
6. Cross-language portability and durable format compatibility.

[Likely] Offer `balanced`, `speed`, and `size` objectives only after a benchmark corpus supports their policies. Add explicit memory, record-size, row-count, and complexity limits. Use deterministic sampling and bounded adaptation; never run an unbounded search through codecs in an online writer.

[Likely] Evaluate a best-tradeoff curve instead of declaring one winner: bytes, encode CPU, decode CPU, working memory, first-result latency, selective-read bytes, and installation requirements. Compare at equal settings and equivalent correctness/integrity contracts. Report excluded costs.

## Required throughout implementation: package-wide test coverage

[Certain] The user explicitly requires unit and integration tests across the package during later implementation. This requirement applies to correctness repairs, existing public behavior, and every subsequent feature. Each implementation must record its tests and remaining gaps in the delivery status.

Required test matrix:

| Area | Unit and boundary tests | Integration and failure tests |
|---|---|---|
| Public API and compressor lifecycle | Mapping/list/generator inputs; supported and rejected types; empty inputs; argument validation; repeated use; clear/finalize behavior; input non-mutation | Equivalent results across convenience, class, file, and incremental APIs; state after failed operations; documented ownership and thread-safety behavior |
| Schema handling and reconstruction | Every record validated; outlier and alternating schemas; missing/null; empty objects; dotted/nested keys; Unicode; deeply nested values; row order; schema count/path limits when introduced | Schema changes within and across chunks; large sparse inputs; exact structural restoration |
| Analyzer and every codec | RAW/RLE/delta/dictionary explicitly exercised; selection thresholds immediately below/at/above boundaries; exact types; float bits; integer limits; invalid run counts/indices/types; safe fallback | Each selected encoding round-trips through the actual public container; mixed and adversarial columns; default and fast modes |
| MessagePack/Zstd serialization | Valid supported values; bad MessagePack; truncated frames; extra frames/data; checksums; declared and unknown frame sizes; limit boundaries | Enforce limits before excessive allocation; preserve typed errors; dependency compatibility for strict-frame decoding |
| v3 container and iterators | Header/footer/chunk grammar; CRCs; lengths; overflow; flags/versions; sequences; counts; truncation at structural boundaries | Multi-chunk streams; short reads; non-seekable sources; no unbounded reads; footer completion; cancellation; early stop; corruption recovery with no records from a failed chunk |
| File helpers | Paths and binary streams; current position; invalid destinations; partial/zero writes | Real temporary-directory round trips; injected write/flush/sync/replace failures; destination preservation; temporary-file cleanup; caller-owned streams remain open |
| Errors and resource limits | Error hierarchy and documented exception types; invalid limits; just-below/at/above each limit | Row-expansion bombs, oversized singleton records, schema/path diversity, depth, bytes, and native-memory constraints as each limit is implemented |
| Benchmarks and diagnostics | CLI arguments; output schema; budget boundaries; exit codes; statistics; no record or secret leakage | Machine-readable output and failure behavior through the actual CLI; independently verified round trips; memory probes in isolated processes |
| Future native core, indexes, projection, and adapters | Shared fixtures; type/precision mapping; index identity and bounds; missing/null predicate semantics | Differential readers/writers; compatible old fixtures; projected/full-read equivalence; safe chunk skipping; range I/O; Python/native and cross-language parity |

Implementation and release requirements:

- Write a regression that demonstrates each reproduced defect before repairing it, then show it passes after the repair.
- Use an independent recursive comparison oracle for supported types and IEEE-754 bits. Ordinary equality cannot certify fidelity. Tests must state explicitly which identity, ordering, or textual properties are outside the supported contract.
- Generate homogeneous columns long enough to trigger every codec, and test nested heterogeneous values, extreme numbers, schema changes, and encoder boundaries deliberately.
- Retain malformed-input mutation/fuzz corpora and independently constructed format fixtures. Avoid relying exclusively on the package's writer to generate inputs for its reader.
- Require both unit evidence for local behavior and integration evidence for public workflows. Use fault injection for rare I/O failures instead of requiring destructive real-machine failures.
- Run supported Python versions and minimum/current dependency configurations in CI; include OS-specific file behavior on supported platforms. Future cross-language implementations must consume the same conformance corpus.
- Measure branch coverage to identify untested behavior, and selectively use mutation testing to verify critical assertions actually detect corrupted values, bypassed checks, and off-by-one limits. A coverage percentage alone is not acceptance evidence.
- Keep fast correctness checks suitable for each change; run larger fuzz, RSS, and scale checks as explicitly configured jobs. Separate noisy performance measurements from deterministic correctness gates.
- Before each milestone is accepted, map every public feature, documented failure contract, and new limit to a named test or verification result. Record unsupported scenarios and remaining gaps explicitly.
- Audit test usefulness at each milestone. Remove clear duplicates only after mapping their behavior and assertions to retained coverage; consolidate repeated setup without losing distinct cases. Replace weak assertions when a meaningful contract needs protection. Retain adversarial, integration, and scale tests when they catch distinct failures; neither test count nor runtime alone is a reason to keep or delete a test.

[Certain] No finite suite proves correctness for every possible input. The acceptance standard is demonstrated contract coverage, strong independent assertions, adversarial testing, and visible limitations; no release should claim total invulnerability.

## Milestone 1: earn the right to claim lossless

[Likely] Complete this before new codecs or a native rewrite.

| Ticket | Work | Acceptance evidence |
|---|---|---|
| JZ-01: supported-value contract | Specify scalar and container types, integer bounds, float bit policy, nonfinite values, missing/null, key-order policy, tuple/custom-object rejection or normalization | Examples and explicit rejection rules; no ambiguous losslessness claim |
| JZ-02: safe delta | Only select arithmetic delta for reversible supported integer columns; validate every delta range; RAW fallback for float/mixed/out-of-range columns | All reproduced cancellation, mixed-type, and integer-range cases preserve values; malformed old payloads remain typed errors |
| JZ-03: safe RLE | Use wire-equivalence rather than ordinary Python equality; retain RAW for types without a validated comparator | Boolean/integer/float, signed-zero, and nested-value probes preserve types and bits |
| JZ-04: conformance corpus | Add targeted homogeneous-column generators and type/bit-sensitive property checks; retain v3 framing, recovery, determinism, and safety tests | All existing tests plus codec boundaries and independent fixtures pass across supported Python/dependency configurations |

[Certain] Existing corrupted archives cannot generally be repaired if distinct original values have already been collapsed. The repair must document its scope honestly. Changing future encoder choices can remain compatible with existing v3 readers when only existing encoding types are emitted.

[Likely] Milestone gate: every reproduced defect is fixed; no silent coercion; compatibility fixtures pass; and tests exercise every default-mode encoder. Passing ordinary Python equality is insufficient.

## Milestone 2: measure and remove wasted work

| Ticket | Work | Acceptance evidence |
|---|---|---|
| JZ-05: benchmark corpus | Version synthetic and public inputs; expand to real event/log exports, optional fields, nested arrays, numeric limits, skewed entropy, tiny/large batches | Dataset hashes, pinned versions, scripts, raw samples, warmups, medians/tails, and exact validation |
| JZ-06: schema ingestion | Prototype flatten-once ingestion, cached path plans, preallocated columns where beneficial, direct homogeneous-column input, and reduced list copying | Improved target workloads with unchanged fidelity; publish regressions as well as gains |
| JZ-07: decode and memory | Remove duplicate full-frame decompression if the dependency contract permits it; compile reconstruction plans; consume order runs without unnecessary expansion | Trailing-frame/truncation/checksum/resource-limit tests pass; measured decode and RSS improvements |
| JZ-08: benchmark enforcement | Separate microbenchmarks from full archive/write/read workloads; measure process RSS in isolated workers and time both cold and reused contexts | Stable CPU regression checks, noisy wall-time tolerances, and scheduled larger runs |

[Likely] Begin with changes supported by profiling. The temporary one-pass experiment's approximately 25% encode-time reduction on regular input justifies a focused implementation experiment, not a claim that all workloads will improve.

[Likely] Compare orjson + Zstd, MessagePack + Zstd, efficient JSON + gzip, tuned Parquet, Vortex, and CLP on relevant overlapping scenarios. Include raw-NDJSON-to-archive and archive-to-NDJSON measurements separately from Python-object round trips. Arrow-native inputs deserve a separate path so they are not converted into Python dictionaries simply to enter the compressor.

[Likely] Proposed gates, to adjust after the corpus expands:

- At least a 20% archive-size advantage against the smaller fast row baseline on most nominated event/archive datasets.
- At least 80% of baseline encode and decode throughput on the common API at the selected operating point, or a documented end-to-end workload advantage that justifies the CPU tradeoff.
- Difficult inputs have a bounded analysis overhead and an explicitly reported fallback policy.
- Every accepted optimization preserves the milestone-1 contract.

[Certain] These thresholds have not been achieved. Existing mixed-event encoding is far below the proposed throughput target. If the goals conflict, expose separate operating points rather than weakening correctness or hiding the tradeoff.

## Measured priorities after the third wave

[Certain] The retained [paired reconstruction report](CPU-WAVE-3.md) measures an approximately
8–9% decode-time reduction on mixed events and nested arrays; the other profiles have small
changes or regressions. This is a workload-specific improvement, not a throughput leadership claim.

[Certain] The [larger corpus report](CORPUS-WAVE-3.md) exposes a stronger constraint: the bounded
writer trades substantially higher CPU time for lower measured Python allocation peaks than the
full-list APIs. Its first output measurement ends at a complete nonempty chunk. The retained
MessagePack baseline materializes all rows, so a streamed MessagePack comparator is still needed.

[Likely] Prioritize these experiments before a native rewrite or new wire format:

1. Profile validation, snapshotting, schema handling, and chunk encoding on the larger corpus;
   remove repeated work while retaining every resource check and generator snapshot contract.
2. Remove asymmetric archive-hashing instrumentation from timed writer comparisons, then compare
   a genuinely streamed MessagePack/Zstd writer and iterator at equal chunk/row bounds,
   full-dataset fidelity, equivalent materialization, and explicitly reported integrity semantics.
3. Evaluate a validated single-run RLE decode shortcut and bounded reconstruction plans;
   include setup time, small/schema-diverse fallbacks, malformed input, and allocation costs.
4. Repeat accepted improvements on real public inputs and additional platforms before revising
   performance promises. Reject optimizations whose target benefit does not justify regressions.

[Certain] The [selective mutation checks](FIDELITY-MUTATIONS.md) kill three known fidelity faults
through separate unit and public-container tests. They strengthen those specific assertions;
package-wide mutation coverage and correctness for all inputs remain unproved.

## Milestone 3: genuinely bounded writes using v3

[Likely] Add a new writer that emits v3 chunks directly to a sink while retaining existing convenience APIs. A possible interface is `write_records(records, sink, *, target_chunk_bytes, max_chunk_records, limits, objective)`; final names and defaults require implementation design.

[Likely] The writer should bound record count, schema count, paths, nesting, individual values, serialized body size, compressed payload size, and queued chunks. A configurable row limit is essential even when serialized columns become tiny after delta/RLE. Flush a current chunk before ingesting an unbounded amount of more input. Reject oversized records with a useful typed error or an explicitly chosen singleton policy.

[Likely] Use independent chunk state; release records, schema metadata, encoder work buffers, and compressed output after each sink write. Provide backpressure and propagate short writes, sink errors, and cancellation. Keep atomic path-write semantics, and document caller-managed stream semantics.

[Certain] The present format specification proposes testing the exact encoded MessagePack body size after each candidate record. Naively rebuilding and serializing the full candidate at every insertion repeats work proportional to the growing chunk.

[Likely] Before implementing that boundary policy, prototype incremental accounting or an amortized strategy. If the chosen encodings make exact incremental accounting impractical, revise the documented writer policy deliberately, retaining hard post-encoding limits. Do not silently implement a different policy or advertise a bounded writer that has quadratic CPU behavior. The byte grammar and the chosen deterministic chunk-boundary policy are separate design decisions.

[Likely] Suggested validation: 10 MB, 100 MB, 1 GB, and later 10 GB sources with the same configured limits, both highly repetitive and schema-diverse. Source generators and sinks must not retain the entire input/output. Demonstrate a memory plateau after startup; measure native RSS and Python allocations separately. Test pipes, early cancellation, oversized values, absent footer, and sink failures.

[Likely] An aspirational demo is archiving a 10 GB event stream on a laptop under a documented 128 MiB RSS budget for bounded records, then retrieving the first useful result promptly. This is a proposed demo, not an executed result. The final bound must depend on configured row/record/complexity limits and native workspace.

## Milestone 4: choose the access architecture before designing v4

[Certain] Current v3 chunks contain a whole MessagePack body inside one Zstd frame. Selecting fields after decompression can avoid some column expansion and record construction, but it cannot avoid decompressing the entire frame or initially unpacking the complete MessagePack body. Claims of per-column compressed-byte access require a different physical layout.

[Likely] Compare three prototypes:

| Option | Capability | Reason to choose it | Cost or limitation |
|---|---|---|---|
| A: v3 plus external index | Bounded writer; row/time chunk lookup; schema summaries; chunk-level skipping; logical projection | Fastest route to useful archives without a new wire format | Cannot independently decompress column pages; sidecar/object identity must be verified |
| B: new version with typed column pages | Independent metadata/predicate/column reads; row ranges; per-page codecs and statistics | Use if measured fidelity/access/size benefits justify ownership of a format | Larger design, migration, conformance, and language-reader burden |
| C: existing columnar backend | jzpack provides faithful record mapping and developer-facing APIs over Parquet Variant or Vortex | Use if it satisfies the contract with lower maintenance and strong integrations | Must prove fidelity mapping, installation cost, and schema/type behavior; defaults alone are inadequate |

[Likely] Run the same query and fidelity corpus against all three. Choose B only after it proves a useful advantage over A and C. Originality should come from better outcomes; owning another binary format is not itself an adoption advantage.

[Likely] For A, bind the index to an immutable archive identity and verify every offset/count within configured limits. Index timestamps and record ranges, with small per-chunk statistics for a bounded set of selected paths. Distinguish “skip whole chunks” from “read only selected columns.” An index can be rebuilt and treated as untrusted until validated.

[Certain] Appending an index into the current v3 footer or setting new v3 flags would violate the current specification. Unknown encodings are rejected. New chunk kinds, bit-packed columns, embedded indexes, or changed footer layouts require a deliberate new version and migration policy; an external sidecar can remain separate.

[Likely] For B, prototype a bounded chunk path/type catalog, validity and presence metadata, compact integer IDs, raw-row fallback for irregular data, independently compressed column pages, and a small seekable index. Preserve missing versus null, row order, arrays, and scalar types. Start with integers, booleans, dictionary strings, and RAW; evaluate bit-preserving float/string techniques after the layout earns its complexity.

[Likely] Sparse optional fields should not require one table for every exact subset indefinitely. Evaluate presence bitmaps, bounded schema families, and shared/raw overflow against exact-shape grouping. Keep per-chunk limits and make fallback deterministic. A row-wise payload fallback is a new-format feature, not a silent extension of existing v3 column encoding.

## Milestone 5: make a useful slice easy to retrieve

[Likely] Add APIs and CLI commands around proven primitives:

- `pack` and `unpack` for NDJSON streams.
- `inspect` for schema, row/chunk counts, sizes, checksum coverage, and supported format version.
- `verify` for complete validation with explicitly configured resource limits.
- `recover` for named skipped chunks and clearly incomplete output.
- `scan` for selected structural paths and simple bounded predicates.
- `explain` for the codec/fallback decisions, avoiding record contents in diagnostics by default.

[Likely] Structural path tuples should remain available so literal dotted keys cannot be confused with nesting. A predicate API should distinguish absent values from explicit nulls, avoid implicit coercion, and limit complexity. Initially implement equality/range/time predicates; delegate arbitrary SQL to existing engines.

[Likely] With independently stored predicate pages, decode those first, identify matching rows, then read requested fields. With v3, retain honest whole-frame accounting. Statistics may conservatively skip impossible matches but must never create false negatives.

[Certain] In a forward-only iterator, some records may already have been yielded before the required footer is checked. Consumers needing complete-archive validity must exhaust verification successfully; recovery output is explicitly incomplete. CLI/export completion indicators must reflect this behavior.

[Likely] Continuous append/replay should initially use immutable archive segments plus a manifest. A finalized v3 file contains a mandatory terminal footer, so naïvely concatenating new records is not a valid append operation. Stable segment/checkpoint identities can enable retries without making the codec a transaction engine.

## Milestone 6: grow an ecosystem from proven usage

[Likely] If Python-object processing remains the main CPU cost after targeted repairs, prototype a Rust core that processes records/column buffers in batches and accepts raw NDJSON where appropriate. Minimize per-cell Python/native calls; compare ingestion, decode-to-objects, and Arrow-to-Arrow paths separately. Keep a reference implementation and shared fixtures. A native rewrite alone does not guarantee fast dictionary materialization.

[Likely] Prioritize an independent Rust reader/writer and Python bindings, then Node/WASM or Go according to actual pilot needs. Define signed/unsigned integer and float semantics before crossing language boundaries. Provide wheels and clear fallback behavior for supported platforms; measure binary size and installation success.

[Likely] Integrate Arrow/Polars/pandas, object-store range reads, and standard logging/export workflows through optional adapters. Avoid pulling databases, web servers, query engines, or heavyweight cloud SDKs into the basic installation.

[Likely] Run three opt-in pilots before making a broad adoption claim: an API-export workflow, an event-log archive, and a dataset with substantial schema drift. Record time to first successful archive, size/CPU/RSS, ease of selective reads, migration effort, defects, and whether users continue using it. Research here did not contact users or establish demand.

[Likely] Publish reproducible comparisons, limitations, compatibility policy, and independently generated fixtures. Call the format documented and open; do not declare it a standard solely because its specification is published. Update the existing roadmap after implementation scope is agreed, including its missing bounded-writer item and already-present contribution/security files.

## Delivery order

[Likely] Implement JZ-01 through JZ-04 first. Develop the existing public-contract tests and benchmark corpus on isolated branches alongside it. Preserve the v3 wire grammar. Independently review each candidate, repair findings, run the combined checks, then commit and merge locally. Implement bounded writing after those gates pass. Keep access architecture and native implementation conditional on their measured experiments.

[Likely] The decisive product demonstration is: changing JSON structures go in without a schema ceremony; supported values come back precisely; huge sources do not exhaust memory; selected records are accessible without reconstructing the archive; and a developer can install and use it immediately. Each part must be measured before it becomes a public promise.
