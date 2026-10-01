# Delivery status

This page records implementation evidence for the [implementation plan](IMPLEMENTATION-PLAN.md).
The user authorized implementation, independent review, commits, and local merges. Remote publication
and real-user outreach are outside the current delivery scope.

## Review and integration procedure

1. Give each implementer an isolated worktree and an explicit file ownership boundary.
2. Record the base commit, behavior change, regression evidence, compatibility implications, and checks.
3. Have a fresh reviewer inspect the complete diff and independently verify critical behavior.
4. Repair findings and repeat affected checks before accepting the candidate.
5. Integrate reviewed candidates on a branch, run the complete combined checks, and merge locally.
6. Retain named branches and worktrees for audit. Record unresolved gaps instead of claiming completion.

Implementation and review agents use Luna at maximum effort, as requested. The orchestrator owns
scope, conflicts, verification, and acceptance. An agent's report alone is not an approval.

## Current delivery

| Work | Branch | State | Evidence |
|---|---|---|---|
| Remove five redundant tests | `codex/remove-redundant-tests` | Merged locally | `dc5a618`; 128 passing tests; identical executed source lines and branches before/after; independent review passed |
| Data fidelity and codec boundaries (JZ-01–04) | `codex/jzpack-fidelity` | Reviewed, committed, integrated for final verification | `758c486`; independent GO; 199 candidate tests; seven pre-fix regressions; legacy float DELTA fixture |
| Existing public and file contracts | `codex/jzpack-test-contracts` | Reviewed, committed, integrated for final verification | `1038976`; independent GO after fixing false-success None writes; 185 candidate tests; failed-batch clear recovery added separately in `3f71753` |
| Benchmark corpus and focused performance (partial JZ-05–08) | `codex/jzpack-performance` | Reviewed, committed, integrated for final verification | `ffca027`; independent GO; 136 candidate tests under both Zstd versions; raw comparisons and allocation tradeoffs in [BENCHMARKS.md](../BENCHMARKS.md) |
| Dependency and platform CI | `codex/jzpack-ci-contracts` | Reviewed, committed, integrated for final verification | `3843316`; independent GO; exact minimum pins plus Linux/macOS/Windows lanes; hosted execution remains unverified |
| Durable delivery plan | `codex/jzpack-delivery-plan` | Reviewed and committed | `43bbe47`; independent GO after correcting coverage interpretation |
| Bounded v3 writer | `codex/jzpack-bounded-writer` | In implementation | Independent chunks, bounded input/rows/nodes/schema/path bytes, direct sinks, atomic paths, fault tests, and isolated RSS probes |
| Fidelity guard costs | `codex/jzpack-codec-costs` | In implementation | Profile and reduce redundant safe checks; exact bytes and malformed-input protections must remain |
| Access architecture experiments | Pending | After bounded writer | Compare v3 sidecar index, typed pages, and existing backend; do not select a new format without evidence |
| CLI, projection, and append segments | Pending | After primitive and architecture gates | Public integration tests and complete/incomplete archive semantics required |
| Native implementation and adapters | Pending | Conditional | Profile and pilot evidence must justify maintenance and compatibility costs |

## Test cleanup evidence

The five removed tests duplicated stronger retained checks for empty archives, file round trips,
magic/version bytes, and retired version rejection. Removal preserved exactly 816 executed source lines
and 280 executed branches under the same deterministic run (133 tests before, 128 after).
This shows coverage parity for that run; it does not establish assertion equivalence or complete
behavioral coverage. The [cleanup evidence](TEST-CLEANUP.md) maps each removed assertion to a
retained stronger test and records the commands and coverage report.

## Acceptance boundaries

No claim of universal performance, complete input coverage, a memory plateau, a new standard, or
broad adoption is established by this delivery. Older archives that already lost values cannot
normally recover those originals. Larger format changes remain experiments until their gates pass.

## First-wave combined verification

At integration code checkpoint `9b12fc2028afabb426600f487802681fc17db76f`, all 265 tests pass
on Python 3.12.13 with current dependencies and on Python 3.11.15 with exact runtime minimums
(msgpack 1.0.0 fallback, zstandard 0.21.0). Ruff, source/test/benchmark compilation, wheel+sdist
build, and whitespace checks pass. The component changes each received a different-author
read-only Luna review. The orchestrator performs the combined second pass as self-review;
new reviewer creation was unavailable because the tool's thread limit was reached.

Commands run from the integration checkout:

```text
PYTHONPATH=. /tmp/jzpack-research-env/bin/python -m pytest -q
PYTHONPATH=. /tmp/jzpack-ci-20261001-minimum-py311/bin/python -m pytest -q
/tmp/jzpack-research-env/bin/python -m ruff check .
/tmp/jzpack-research-env/bin/python -m compileall -q jzpack tests benchmarks
/tmp/jzpack-research-env/bin/python -m build --outdir /tmp/jzpack-research/wave-1-build
git diff --check
PYTHONPATH=. /tmp/jzpack-research-env/bin/python benchmarks/benchmark_corpus.py --records 3000 --iterations 3 --warmups 1 --seed 1729 --level 3 --json
```

The local environment paths identify the executed runs; use equivalent installed development
environments to reproduce them. [CI validation](CI-VALIDATION.md) distinguishes local minimum
coverage from the unexecuted hosted Python 3.10 and platform matrix. The complete-package
benchmark preserves every corpus archive checksum, but fidelity guards add CPU work; see the
integrated checkpoint in [BENCHMARKS.md](../BENCHMARKS.md). No universal performance claim is made.

The repository has no architecture diagram index. README and FORMAT remain its canonical API
and wire-contract sources and were reviewed/updated for the changed boundaries.
