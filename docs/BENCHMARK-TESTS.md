# Comparative benchmark test coverage

This note maps the contract tests for `benchmarks/benchmark_corpus.py`. The tests exercise the real
CLI in subprocesses with `sys.executable`, `shell=False`, and the repository as the working
directory; they do not impose timing or RSS budgets.

| Contract | Evidence |
|:---|:---|
| Corpus v1 content and profile distinction remain stable | `test_corpus_v1_golden_fingerprints_distinguish_profiles` pins canonical sizes and SHA-256 fingerprints for seeded mixed-event and high-entropy records |
| JSON output reports the requested configuration and exact corpus identity | `test_comparative_cli_json_schema_matches_seeded_corpus_and_sample_counts` checks the CLI schema, profile order, golden input hashes/sizes, required baselines, exact-round-trip flag, compressed hash shape, and cold/reused sample counts |
| Human-readable output reports measurements without exposing input values | `test_human_cli_output_contains_metrics_without_record_values` checks headings and encode/decode statistics while excluding seeded digest and payload values |
| Level endpoints are accepted and invalid arguments have a stable exit code | `test_cli_accepts_compression_level_endpoints` and `test_invalid_cli_arguments_have_stable_argparse_exit` check levels 1/22, positive count/iteration limits, non-negative warmups, valid profiles, and argparse status 2 |
| The optional `orjson` import can be absent | `test_optional_orjson_import_absence_keeps_required_baselines_available` blocks its import in the child process and verifies the JZPack and MessagePack baselines still complete |
| Benchmark failures do not expose exception messages or record contents | `test_controlled_cli_failure_discloses_only_exception_class` injects a sentinel exception before corpus creation and checks empty stdout, status 3, and class-only stderr |
| Exact result comparison rejects shape and representation mismatches | `test_exact_validator_rejects_nested_sequence_length_mismatch` covers list-length mismatches; `tests/test_performance_contracts.py::test_exact_oracle_distinguishes_types_and_float_bits` covers bool-versus-int and signed-zero distinctions |

Run the focused contracts with:

```bash
PYTHONPATH=. python -m pytest tests/test_benchmark_contracts.py tests/test_performance_contracts.py
```

These tests cover the comparative corpus CLI only. The isolated bounded-writer RSS probe CLI is not
present at this branch's base revision and is outside this test mapping.
