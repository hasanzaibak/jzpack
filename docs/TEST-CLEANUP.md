# Test cleanup evidence

Branch: `codex/remove-redundant-tests`. Base: `61177584cc0f2478a385a178546b88e2cd2343ba`. Merged locally as `dc5a618370e98153cceb0aee7c5c8cccc55f9e83`; change to `tests/test_jzpack.py` only. No runtime code or format change; the existing README/FORMAT architecture is unaffected.

## Removed tests and retained protection

| Removed test | Retained test covering its behavior |
|---|---|
| `TestBasicCompression.test_empty_list` | `tests/test_chunks.py::test_empty_v3_container_and_list_adapter` verifies exact empty container bytes and empty list adapter behavior |
| `TestCompressorClass.test_file_operations` | `tests/test_file_helpers.py::test_path_based_round_trip_accepts_strings_and_returns_byte_count` verifies the same string-path class file round trip plus actual byte count; pathlib and failure-path tests remain |
| `TestHeaderValidation.test_magic_header` | `tests/test_chunks.py::test_writer_emits_a_single_valid_v3_chunk` asserts the magic and version together with valid chunk/footer structure and round trip |
| `TestHeaderValidation.test_version` | The same retained writer test asserts the version; streaming writer and determinism tests also retain version checks |
| `TestHeaderValidation.test_retired_version_one_payload_is_rejected` | `tests/test_reliability.py::test_retired_standalone_versions_are_rejected[1]` uses the same rejected bytes and requires the specific UnsupportedVersionError; generic unsupported-version message coverage remains |

Unused tempfile and pathlib imports were removed from the changed test file. Other unit, property, malformed-input, recovery, resource-limit, scale, and file integration tests were retained.

## Verification

Baseline: 133 passed in 4.14s. Candidate: 128 passed in 4.32s. Times are instrumented local runs and do not establish a speed improvement. Both use PYTHONHASHSEED=0 and --hypothesis-seed=20260930. Exact package line/branch coverage sets were compared: no lost executed lines or branches. Statement coverage remains 816/943 (86.53%); branch coverage remains 280/374 (74.87%). Coverage measures exercised code and is additional evidence, not proof of behavioral completeness.

The [recorded comparison](evidence/test-cleanup-coverage-comparison.json) contains the totals and lost-coverage result. To reproduce, create clean worktrees at the base commit above and the cleanup commit, install the development dependencies plus `coverage`, and run the same command below in each worktree with different data-file names. The temporary environment path is specific to the original local run.

Commands used:

```text
PYTHONHASHSEED=0 PYTHONPATH=. /tmp/jzpack-research-env/bin/python -m coverage run --branch --source=jzpack --data-file=/tmp/jzpack-research/coverage-before -m pytest -q --hypothesis-seed=20260930
PYTHONHASHSEED=0 PYTHONPATH=. /tmp/jzpack-research-env/bin/python -m coverage run --branch --source=jzpack --data-file=/tmp/jzpack-research/coverage-after -m pytest -q --hypothesis-seed=20260930
/tmp/jzpack-research-env/bin/python -m ruff check .
/tmp/jzpack-research-env/bin/python -m build --no-isolation --outdir /tmp/jzpack-test-cleanup-dist
git diff --check
```

Lint and diff checks passed. Source distribution and wheel built successfully. These checks preceded the authorized local commit and merge. No remote publication was performed. Checks ran locally on Python 3.12.13/macOS ARM64; full supported-platform CI was not run. Data-corruption repairs are separate from this test-deletion change; see [delivery status](DELIVERY-STATUS.md).

Independent read-only review: GO, no actionable findings. The reviewer verified retained contracts against source, README, FORMAT, CONTRIBUTING, and CI; independently ran the affected suites (111 passed), the complete candidate suite (128 passed in 2.28s), and git diff --check. Candidate test file SHA-256: 563a07d6439a994c5d2da6b512742013b7d4a8c57712dca766bdcf716c561a48. Approval covers this test-cleanup scope only.

Diff SHA-256: `57224298ae80fff3e3cec606c345423c1f4fe05ce13600294b1ebcc0c593e220`.
