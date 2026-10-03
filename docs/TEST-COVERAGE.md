# Test coverage map

The [implementation plan](IMPLEMENTATION-PLAN.md) records milestone scope and acceptance gates.
This page connects current public contracts to their regression evidence and records behavior that
the suite does not establish. It is a map for review, not a claim that every input or machine failure
has been tested.

| Public contract | Test evidence | Coverage and limits |
|---|---|---|
| `compress` accepts one mapping or an iterable of mappings; keys are strings; `decompress` returns records in input order | `tests/test_public_contracts.py::test_mapping_list_generator_class_file_and_iterator_routes_agree`, `test_compress_rejects_non_mapping_records_and_non_string_keys`, `test_compress_does_not_mutate_mapping_inputs_or_consume_generator_twice` | Covers mapping, list, generator, rejection of malformed record/key shapes, and integration across class, convenience, file, stream, and iterator routes. |
| Empty archive differs from one empty mapping; nested and literal dotted keys, absent fields, explicit nulls, empty nested mappings, lists, Unicode, booleans, and bytes retain their structure | `tests/test_public_contracts.py::test_mapping_record_and_empty_iterable_have_distinct_row_counts`; `tests/test_schema_contracts.py::test_structural_paths_preserve_dotted_keys_nested_keys_missing_and_null`, `test_empty_mappings_and_alternating_shapes_keep_record_order`; `tests/test_jzpack.py::test_dotted_keys_are_not_interpreted_as_nested_paths` | The schema-contract cases check path tuples through the public container. The suite does not define identity, original JSON text, duplicate JSON object keys, or mapping insertion order as round-trip guarantees. |
| Exact-dict flatten fast path preserves schema and generic `Mapping` behavior | `tests/test_schema_contracts.py::test_builtin_dict_flatten_preserves_nested_empty_null_and_literal_keys`, `test_userdict_root_and_nested_mappings_keep_generic_mapping_behavior`, `test_flatten_rejects_non_string_keys_for_builtin_and_custom_mappings`, `test_flatten_keeps_non_mapping_root_error`, `test_late_invalid_record_keeps_existing_schema_state_unchanged`, `test_mapping_fast_path_keeps_missing_null_heterogeneous_and_archive_fidelity` | Only exact built-in dictionaries skip the `Mapping` ABC check. Two nine-sample paired runs preserve archive hashes and exact round trips: [run 1](../benchmarks/results/schema-flat-fastpath-2a7af-run1.json), [run 2](../benchmarks/results/schema-flat-fastpath-2a7af-run2.json). The separate [late-invalid memory probe](../benchmarks/results/schema-flat-fastpath-2a7af-late-invalid-memory.json) shows allocation deltas within measurement noise. These measurements are workload-specific. |
| Compression levels 1 through 22 are accepted; invalid levels and invalid decompression limit values are rejected | `tests/test_public_contracts.py::test_compression_level_inclusive_boundaries`, `test_compression_level_rejects_values_outside_the_documented_integer_range`, `test_decompress_limits_reject_bool_negative_and_non_integer_values` | Checks inclusive compression-level endpoints and invalid boolean, negative, floating-point, and out-of-range values. Other controls such as concurrency and thread safety have no documented contract here. |
| `decompress` enforces `max_records` and aggregate `max_output_size`; iterator APIs enforce per-chunk and aggregate limits | `tests/test_public_contracts.py::test_decompress_limits_accept_the_exact_boundary_and_reject_one_below`, `test_empty_archive_accepts_zero_decompression_limits`; `tests/test_chunks.py::test_v3_limits_accept_the_exact_boundary_and_reject_one_below`, `test_v3_limits_are_checked_before_chunk_reconstruction`; `tests/test_file_helpers.py::test_file_helpers_preserve_decompression_limits` | Covers exact-at and one-below boundaries for public list and iterator APIs, invalid limit types, and forwarding of list-returning file-helper limits. |
| `StreamingCompressor` accumulates records, can be finalized repeatedly, and can be cleared and reused | `tests/test_public_contracts.py::test_streaming_compressor_finalize_is_repeatable_and_clear_reuses_it`, `test_streaming_compressor_clear_recovers_after_failed_mixed_batch`; `tests/test_jzpack.py::TestStreamingCompressor` | Verifies observable lifecycle, decoded values, and clearing state after a failed heterogeneous batch. Failed batches may retain a valid prefix; they are not atomic. The class still buffers all column data until `finalize()`; these tests do not establish a bounded-memory writer. |
| Path helpers replace destinations atomically; caller-owned binary streams use their current position, remain open, and `compress_to_file` reports the payload byte count | `tests/test_file_helpers.py::test_existing_destination_is_replaced_atomically`, `test_atomic_replacement_failure_preserves_destination_and_cleans_temp_file`, `test_atomic_write_failure_during_temp_write_preserves_destination_and_cleans_temp_file`, `test_atomic_flush_or_sync_failure_preserves_destination_and_cleans_temp_file`, `test_file_like_compression_rejects_zero_and_short_writes`, `test_file_like_compression_rejects_non_integer_write_results`, `test_file_like_compression_rejects_none_write_result_and_keeps_stream_open`, `test_file_like_compression_uses_current_position_and_leaves_stream_open`, `test_file_like_decompression_uses_current_position_and_leaves_stream_open` | Uses temporary directories and deterministic write, flush, sync, and replace failures to check destination preservation and temporary-file cleanup. Stream tests cover zero/short/invalid write counts, rejected `None` write results (including sinks that accept no bytes), positions, and ownership. |
| `iter_decompress` reads sequentially from paths and binary streams, supports short reads without unbounded `read()`, and validates the footer only when iteration is exhausted | `tests/test_chunks.py::test_path_stream_current_position_partial_reads_and_stream_ownership`, `test_iter_decompress_requires_footer_completion_for_full_container_validation` | Includes partial-read sources, current-position/ownership checks, and a truncated-footer test showing that already-yielded records precede the final completion error. `decompress_from_file` and the file helpers buffer the complete archive in memory. |
| Recovery reports a typed `ChunkError` for a skippable damaged payload, yields no records from that chunk, and continues at the next framed chunk | `tests/test_chunks.py::test_v3_recovery_reports_a_corrupt_middle_chunk_and_keeps_later_chunks`, `test_v3_recovery_stops_when_chunk_framing_is_corrupt` | Uses a damaged two-record middle chunk and asserts that only records from good chunks are yielded. Header, length, CRC, footer, and limit failures remain fatal. |
| v3 framing rejects invalid tags, flags, sizes, checksums, sequences, counts, lengths, inner frames, footer totals, and trailing data with typed errors | `tests/test_chunks.py::test_v3_rejects_header_crc_flags_sizes_reserved_fields_and_sequences`, `test_v3_rejects_bad_chunk_crc_bad_footer_crc_and_trailing_bytes`, `test_v3_rejects_zero_record_and_invalid_payload_lengths`, `test_v3_rejects_malformed_messagepack_row_count_and_body_size_mismatches`, `test_v3_rejects_invalid_inner_versions`, `test_v3_fails_with_typed_errors_for_arbitrary_bytes_and_unknown_versions`; `tests/test_reliability.py::test_truncated_payloads_raise_controlled_errors`, `test_arbitrary_bytes_do_not_leak_parser_exceptions` | Includes independently composed framing, hand-mutated fields, truncation at structural positions, malformed MessagePack, bad inner versions, and arbitrary-byte/truncated-prefix probes. |
| Structural schema paths and per-schema row counts must be well formed and agree with the archive order | `tests/test_schema_contracts.py::test_malformed_structural_paths_are_reported_as_invalid_format`, `test_malformed_schema_row_counts_are_reported_as_invalid_format`, `test_schema_order_counts_and_ids_must_match_schema_rows` | Covers empty, non-string, duplicate, and prefix-colliding paths; bad row counts; and mismatched or unknown schema-order entries. These malformed payloads use valid outer framing so assertions target schema validation. |
| Column codecs and compressor behavior round-trip common values and exercise selected RLE, delta, dictionary, and raw paths | `tests/test_jzpack.py::test_rle_triggered`, `test_delta_triggered`, `test_dictionary_triggered`, `test_raw_fallback`; `tests/test_properties.py::test_generated_records_round_trip`; `tests/test_codec_fidelity.py` with `tests/fidelity_oracle.py::is_faithful` | The dedicated fidelity corpus uses an independent oracle for exact built-in types, recursive structure, and float64 bit patterns. The generated property test remains broad example coverage, not exhaustive proof of every MessagePack value or codec boundary. |
| Benchmark CLI output, options, budgets, and redaction behavior | `tests/test_benchmark.py::test_small_json_execution_has_stable_typed_fields`, `test_generous_budgets_pass`, `test_budget_failure_is_non_zero_and_identifies_budget`, `test_json_output_excludes_paths_environment_values_and_raw_records` | Covers the local benchmark contract. Performance and memory numbers remain workload- and environment-dependent; Python allocation tracing is not total process RSS. |
| `write_records` emits independent bounded v3 chunks while preserving supported values and generator snapshots | `tests/test_writer.py::test_single_chunk_writer_matches_existing_v3_output_and_exact_values`, `test_generated_writer_records_preserve_exact_values_across_small_chunks`, `test_reused_generator_record_is_snapshotted_before_the_next_yield`, `test_array_of_objects_and_nested_empty_maps_round_trip` | Includes whole-list type/float-bit assertions, nested arrays of objects, schema changes, deterministic output, and compatibility with current readers. |
| Writer input/body/payload, row, node, schema, path, and depth bounds | `tests/test_writer.py::test_record_messagepack_size_boundaries_match_msgpack`, `test_encoded_body_and_payload_limits_accept_exact_boundary_and_reject_minus_one`, `test_rle_cannot_bypass_per_chunk_row_cap`, `test_path_count_and_utf8_byte_budgets_count_paths_across_schema_definitions`, `test_container_depth_limit_counts_root_and_nested_dict_or_list_only`; integer/string/binary/UTF-8/map/array width cases in the same file | Tests MessagePack size boundaries against the dependency, aggregate limits, oversized singletons, and rejection. Hard encoded-output violations fail the write; they do not repartition an encoded body. |
| Writer fused validation, sizing, flattening, and snapshot preserve error ordering and exact values | `tests/test_writer.py::test_fused_preflight_accepts_exact_nested_utf8_limits_and_rejects_one_less`, `test_fused_preflight_preserves_signed_zero_and_nan_payload_bits`, `test_late_type_error_keeps_precedence_after_chunk_input_crossing`, `test_late_depth_error_keeps_precedence_after_chunk_input_crossing`, `test_late_cycle_error_keeps_precedence_after_chunk_input_crossing` | Checks exact/one-below byte, node, depth, and path budgets, float64 bits, and late validation errors after the chunk-input cap. Failed preflight leaves only the outer header and does not flush the pending chunk. |
| Writer backpressure, partial writes, caller ownership, cancellation, and atomic path failure | `tests/test_writer.py::test_sink_backpressure_allows_only_one_bounded_lookahead_record`, `test_nonseekable_sink_retries_positive_short_writes_without_closing`, `test_invalid_sink_write_counts_are_rejected`, `test_stream_cancellation_after_an_emitted_chunk_leaves_no_footer`, `test_path_failures_preserve_existing_destination_and_remove_temp` | Distinct write/generator/flush/sync/replace/cancellation failures are injected. Partial caller streams may remain incomplete; path failures before replacement preserve the destination. |
| Comparative corpus CLI configuration, reproducibility, optional dependencies, and diagnostics | `tests/test_benchmark_contracts.py`; [contract map](BENCHMARK-TESTS.md) | Fourteen small tests use actual subprocesses, golden input fingerprints, literal error statuses, and exception-message redaction. No timing/RSS thresholds. |
| Shallow schema reconstruction preserves schema-path order and nested paths keep the existing builder | `tests/test_schema_reconstruction_fastpath.py::test_flat_schema_preserves_path_order_and_duplicate_overwrite_behavior`, `test_flat_schema_preserves_empty_rows_zero_rows_and_column_validation`, `test_flat_public_round_trip_preserves_missing_null_types_bits_and_record_order`, `test_any_nested_path_keeps_the_existing_row_builder`; `tests/test_schema_reconstruction_performance.py::test_nested_schema_fast_path_preserves_exact_values_and_record_order` | Covers direct path order and overwrite semantics, empty and zero-row schemas, column-length rejection, missing versus null, exact primitive types and float64 bits, and nested fallback. Schema-path order is an internal reconstruction contract; original input mapping key order is not a round-trip guarantee. |
| Critical fidelity assertions detect deliberate faulty implementations | `tests/test_fidelity_mutation_harness.py`; [mutation evidence](FIDELITY-MUTATIONS.md) | Source anchors fail closed, only disposable copies are mutated, and only explicit selected failure IDs count. Three opt-in mutants must fail separate direct and public-container tests; not general mutation coverage. |
| Expanded corpus fidelity, comparator fairness, and CLI contracts | `tests/test_benchmark_corpus_wave3.py`; [corpus evidence](CORPUS-WAVE-3.md) | Golden fingerprints, exact UTF-8 sizes, missing/null and float-bit oracles, full-input Parquet eligibility, complete first chunks, equivalent list decode materialization, isolated probes, and redacted CLI failures. No noisy performance thresholds in unit CI. |
| ASCII sizing preserves writer byte limits and Unicode failures | `tests/test_writer_ascii_size.py` | Fifteen cases cover empty/long ASCII, UTF-8 block boundaries, multibyte characters, surrogates, and exact public MessagePack record limits. |
| Validated single-run RLE preserves value identity and allocation guards | `tests/test_rle_single_run.py` | Fifteen cases cover exact types/float bits, legacy nested aliases, host and caller output bounds before allocation, and public constant-column decoding. |
| Bounded RLE run reuse preserves encoder choices and wire bytes | `tests/test_rle_run_reuse.py` | Covers the 32/33-run cache boundary, run-ratio fallback, unsupported values and list subclasses, exact float-bit fidelity, and byte-identical round trips. Separate benchmark evidence measures traced Python allocations, not RSS. |
| Writer diagnostics use their own checkout and reject changed frozen sources | `tests/test_writer_profile_contracts.py` | Seven cases include a real Git checkout with CRLF conversion enabled. Exact source-byte provenance remains a repository diagnostic contract. |
| Streamed row comparator validates completion and truthful timing boundaries | `tests/test_streamed_baseline.py`; [comparison scope](STREAMED-BASELINE.md) | Count/header/MessagePack/frame/checksum/trailing-data failures, early rows before final checksum, short writes, exact values, timer cleanup, safe CLI errors, isolated sinks, and full runtime source provenance. This comparator is not a production untrusted-input reader. |

## Visible gaps

- The current `StreamingCompressor` and file helpers are not direct-to-sink bounded-memory writers. `decompress` returns a list, and `decompress_from_file` reads the complete source before decoding. Use `iter_decompress` for sequential chunk reads.
- `write_records` exposes schema/path/depth/record/row/node and byte limits; legacy accumulating APIs do not gain those bounds. No API promises a fixed native-memory budget.
- The base format has no random-access index, field projection, query adapter, native core, or cross-language implementation. Index and projection behavior therefore have no current tests.
- Thread safety and concurrent reuse are not documented or tested. This suite makes no concurrency guarantee.
- `tests/test_codec_fidelity.py` checks a deliberate type- and bit-sensitive corpus; it does not prove every possible MessagePack value or codec boundary. The property and example suite remains non-exhaustive.
- The local suite does not cover every supported Python/dependency combination or operating system. Atomic replacement behavior is fault-injected locally, but cross-platform filesystem semantics require platform CI.
- The [writer probe](WRITER.md) measures 25K/100K/400K small repeated-schema rows with three isolated samples per size. RSS includes tracing overhead. The [expanded corpus](CORPUS-WAVE-3.md) adds schema-diverse, nested, and large-string isolated probes. The [streamed comparator](STREAMED-BASELINE.md) uses separate encode-memory probes; it does not establish bounded decoder/source-I/O memory. Larger real sources, writer-probe CLI tests, and broader mutation/fuzz campaigns remain separate verification work.

## Combined local checkpoint

At `6b23466a53a6774ac02d8f1f3754a95ecd4dcaf6`, all 383 tests pass on current Python 3.12 and
Python 3.11 with exact minimum runtime dependencies. Statement coverage is 90.4% and branch
coverage is 82.5% (coverage.py's combined figure is 88.0%); the
[retained summary](evidence/wave-2-coverage-summary.json) includes module-level counts.
Coverage identifies gaps rather than proving assertion quality or exhaustiveness. Hosted platform
and Python-version coverage remains subject to [CI validation](CI-VALIDATION.md).

## Third-wave checkpoint

At code checkpoint `3f2e37e1db3c3b9653fc52bc02330128d8157440` plus the 0.5.1 release metadata and
documentation delta, all 404 tests pass on current Python 3.12 and minimum-dependency Python 3.11.
The named shallow-path, mutation-harness, and expanded corpus contracts above are included.
Three opt-in fidelity mutants are also killed by separate unit/public tests in both environments.
The second-wave coverage percentages above remain historical; coverage was not remeasured for
this checkpoint. The [exact-release hosted matrix](https://github.com/hasanzaibak/jzpack/actions/runs/36940128021)
subsequently passed for the 0.5.1 commit, including Python 3.10 minimum dependencies and
macOS/Windows. Later source snapshots require their own platform evidence.

## Fourth-wave checkpoint

At core checkpoint `070cbf5` plus the 0.5.2 metadata/documentation delta, all 468
tests pass in current Python 3.12 and minimum-dependency Python 3.11 environments.
The four new groups add 64 distinct cases: ASCII sizing (15), RLE (15), diagnostic
provenance (7), and streamed-comparator contracts (27). Ruff and compilation pass.
The retained fourth-wave mutation reruns kill the same three selected fidelity
faults through separate unit/public tests in both environments. No new coverage
percentage or package-wide mutation claim is made.

The separate public GH Archive check preserves all 11,351 parsed records through
list and lazy writer APIs in both environments. It does not measure performance
or establish JSON-text preservation. Dependency-version archive bytes may differ.
Final release platform evidence requires its own hosted matrix.

The [0.5.2 exact-release matrix](https://github.com/hasanzaibak/jzpack/actions/runs/36955599304)
subsequently passed all 468 tests in every one of seven hosted test lanes. The
[release evidence](evidence/release-0.5.2.json) records official artifact hashes
and the separate fresh-installed smoke scope. This does not clear future source
changes or establish exhaustive assertion coverage.

## Packaging checkpoint: 0.5.3

`tests/test_readme_links.py` rejects relative README documentation links and checks
the canonical GitHub file destinations locally. It fails against the previous README
and passes with the fix. The CI quality build creates cache sentinels and rejects
them in the source archive while requiring essential package files. That guard
fails before the exclusion fix and passes afterward.

The [0.5.3 release record](evidence/release-0.5.3.json) records 469 passing tests in
both local dependency environments and all seven hosted test lanes. No new coverage
percentage, mutation score, or exhaustive assertion claim is made.

## Bounded-writer snapshot reuse: 0.5.4

The writer regressions cover reused generator records that are mutated before the
next yield, nested empty mappings and lists, distinct NaN payloads, signed zero,
multichunk record order, and invalid-record failure before a prepared chunk is
flushed. The integrated candidate passes all 470 tests in local Python 3.12,
minimum-dependency Python 3.11, and Python 3.14 environments. Its exact hosted
release run passed all 470 tests in each of eight test lanes. The
[release record](evidence/release-0.5.4.json) links the exact CI and publishing runs, published hashes,
and fresh installed-package smoke check. No coverage percentage or exhaustive test
claim is made.
