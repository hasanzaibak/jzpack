# Delivery status

This page records implementation evidence for the [implementation plan](IMPLEMENTATION-PLAN.md).
The user authorized implementation, independent review, commits, merges, and subsequently remote
push and package publication. Real-user outreach is outside the current delivery scope.

## Release ledger

The reviewed implementation checkpoint `3e87f770be076cac166caf7fd35de22c81c3782b` was pushed
to main. Its [hosted CI run](https://github.com/hasanzaibak/jzpack/actions/runs/36896532437)
passed the Linux Python 3.10–3.13, exact-minimum dependency, macOS, Windows, and quality jobs.
Version [0.5.0](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.0) was released at
`267730398a1bd602efe4bb3324877655c1cdc204` after independent review and all eight jobs in its
[exact-commit CI run](https://github.com/hasanzaibak/jzpack/actions/runs/36898612858) passed.
The [trusted-publishing workflow](https://github.com/hasanzaibak/jzpack/actions/runs/36898799281)
completed successfully after normal approval through the configured reviewer account, under the
user's publication authorization; environment protection rules remained enabled.

[PyPI 0.5.0](https://pypi.org/project/jzpack/0.5.0/) was verified through the official JSON API.
Its wheel and source-distribution SHA-256 hashes both match the downloaded workflow artifacts.
A fresh environment installed `jzpack==0.5.0` from the official PyPI index outside the source
checkout and passed exact nested scalar/float-bit and multi-chunk writer smoke checks. The
[release verification record](evidence/release-0.5.0.json) retains hashes and commands.
See [RELEASES.md](RELEASES.md) for the repeatable publication gates.

The third wave is prepared on `codex/jzpack-wave-3`: reviewed reconstruction CPU improvements,
larger/schema-diverse benchmark comparisons, and selective fidelity mutation verification.
These changes are not included in 0.5.0. Version 0.5.1 is a release candidate until its exact
hosted checks and trusted publication complete; the 0.5.0 evidence does not clear this release.

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
| Data fidelity and codec boundaries (JZ-01–04) | `codex/jzpack-fidelity` | Merged locally to main | `758c486`; independent GO; 199 candidate tests; seven pre-fix regressions; legacy float DELTA fixture |
| Existing public and file contracts | `codex/jzpack-test-contracts` | Merged locally to main | `1038976`; independent GO after fixing false-success None writes; 185 candidate tests; failed-batch clear recovery added separately in `3f71753` |
| Benchmark corpus and focused performance (partial JZ-05–08) | `codex/jzpack-performance` | Merged locally to main | `ffca027`; independent GO; 136 candidate tests under both Zstd versions; raw comparisons and allocation tradeoffs in [BENCHMARKS.md](../BENCHMARKS.md) |
| Dependency and platform CI | `codex/jzpack-ci-contracts` | Merged and pushed to main | `3843316`; independent GO; exact minimum pins plus Linux/macOS/Windows lanes; hosted execution passed on `3e87f77` |
| Durable delivery plan | `codex/jzpack-delivery-plan` | Merged locally to main | `43bbe47`; independent GO after correcting coverage interpretation |
| Bounded v3 writer | `codex/jzpack-bounded-writer` | Reviewed, committed, integrated; combined checks pass | `042b3f7`; independent GO after nested-array defect repair; 352 candidate tests in current/minimum environments; direct sinks, atomic paths, failure/property tests, and scoped [RSS evidence](WRITER.md) |
| Fidelity guard costs | `codex/jzpack-codec-costs` | Reviewed, committed, integrated; combined checks pass | `b1be6a7`; independent GO; 265 candidate tests in current/minimum environments; unchanged corpus archive bytes; [profile and raw results](CODEC-COSTS.md) |
| Comparative benchmark contracts | `codex/jzpack-benchmark-tests` | Reviewed, committed, integrated; combined checks pass | `90dc33c`; independent GO after pinning documented error status; 14 distinct tests; [contract map](BENCHMARK-TESTS.md) |
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
coverage from the hosted Python 3.10 and platform matrix. Those hosted jobs were unexecuted at
this historical first-wave checkpoint and subsequently passed as recorded in the release ledger. The complete-package
benchmark preserves every corpus archive checksum, but fidelity guards add CPU work; see the
integrated checkpoint in [BENCHMARKS.md](../BENCHMARKS.md). No universal performance claim is made.

The repository has no architecture diagram index. README and FORMAT remain its canonical API
and wire-contract sources and were reviewed/updated for the changed boundaries.

## Second-wave combined verification

The first wave was committed and fast-forwarded into local main at `472cfc1`. The second wave
integrates reviewed implementation branches on `codex/jzpack-wave-2`, with code checkpoint
`6b23466a53a6774ac02d8f1f3754a95ecd4dcaf6`. All 383 tests pass on Python 3.12.13 with
msgpack 1.2.3 native/zstandard 0.25.0 and Python 3.11.15 with exact runtime minimums
(msgpack 1.0.0 fallback/zstandard 0.21.0). Ruff, compilation, wheel+sdist build, and whitespace
checks pass. The final documentation and combined branch receive an additional read-only review
before the local main fast-forward. At that historical delivery checkpoint, no push or package
publication had occurred and the changelog was Unreleased. Subsequent publication is recorded
in the release ledger above; worktrees and branches are retained.

Commands executed from the integration checkout:

```text
PYTHONPATH=. COVERAGE_FILE=/tmp/jzpack-research/wave-2.coverage /tmp/jzpack-research-env/bin/python -m coverage run --branch --source=jzpack -m pytest -q
PYTHONPATH=. /tmp/jzpack-ci-20261001-minimum-py311/bin/python -m pytest -q
/tmp/jzpack-research-env/bin/python -m ruff check .
/tmp/jzpack-research-env/bin/python -m compileall -q jzpack tests benchmarks
/tmp/jzpack-research-env/bin/python -m build --outdir /tmp/jzpack-research/wave-2-build
PYTHONPATH=. /tmp/jzpack-research-env/bin/python benchmarks/benchmark_corpus.py --records 3000 --iterations 3 --warmups 1 --seed 1729 --level 3 --skip-rss --json
git diff --check
```

The [coverage summary](evidence/wave-2-coverage-summary.json) reports 90.4% statement coverage,
82.5% branch coverage, and coverage.py's combined figure of 88.0%. These are gap-finding tools,
not proof that every assertion is effective or
that every input is safe. Named public contracts and residual gaps are in [TEST-COVERAGE.md](TEST-COVERAGE.md).

The writer's 25K/100K/400K-row repeated-schema probe uses three isolated samples at each size;
median process peaks are approximately 25 MB, including startup and allocation-tracing overhead.
It establishes a measured checkpoint for those small records, not a general RSS ceiling. Larger
sources, schema diversity, large records, and other dependency/platform combinations remain to
be measured. The final in-memory corpus run preserves every original archive checksum but does
not show uniformly faster decoding; see [BENCHMARKS.md](../BENCHMARKS.md).

The access-architecture comparison remains the next design gate. A new format, indexes,
projection, a user-facing archive CLI, native implementations, and adapters are not implemented by
these branches. They remain conditional on the [implementation plan](IMPLEMENTATION-PLAN.md),
with tests required for each accepted feature. Hosted CI was subsequently verified for the
pushed integration commit in the release ledger above.

## Third-wave integration and release candidate

The reviewed components are integrated on `codex/jzpack-wave-3` at code checkpoint
`3f2e37e1db3c3b9653fc52bc02330128d8157440`. Root release metadata and documentation prepare 0.5.1.
This checkpoint retains the v3 grammar, dependencies, supported values, and reader limits.

| Component | Reviewed commit | Acceptance evidence |
|---|---|---|
| Direct shallow nested reconstruction | `78c766fef1207cc2dceb0de29325c31234eae9f6` | Different-author GO; one public exact-value regression, 46,914 differential cases, and source-verified 15-pair [decode evidence](CPU-WAVE-3.md) |
| Selective fidelity mutations | `3a487571e652a4ab321a4847502ae189e2a13c3e` | Different-author GO; three codec faults each killed by a distinct unit/public pair in current and minimum runtimes; [retained evidence](FIDELITY-MUTATIONS.md) |
| Larger corpus and writer comparisons | `748baf7` | Different-author GO after correcting documentation to match raw medians; 12 benchmark-contract tests; [full report and limitations](CORPUS-WAVE-3.md) |

The reconstruction fast path changes only record construction, leaving deeper paths on the generic
setter. The final paired capture improves two profiles about 8–9%, while other profiles show small
changes or regressions. Larger corpus probes reveal a writer CPU tradeoff despite lower traced
allocation peaks than full-list APIs. Timed writes retain the archive; encode-only memory probes
use lazy inputs and a hashing discard sink. RSS includes native allocations and active tracing
overhead; neither a general memory ceiling nor leadership over a streamed row baseline is proved.

Combined local validation on that checkpoint plus the 0.5.1 version/documentation delta passed
404 tests on Python 3.12.13 with current dependencies and 404 on Python 3.11.15 with exact runtime
minimums. Ruff, compilation, wheel/source build, relative documentation links, and whitespace
checks passed. Both environments reran the three mutation checks successfully. The final built
wheel matched the outside-checkout smoke-tested wheel by SHA-256; that smoke checks installed
metadata, shallow/deep nested and literal dotted keys, exact scalar/float bits, and multi-chunk
writer output. Exact-release hosted CI and official PyPI verification remain release gates.

Commands executed:

```text
PYTHONPATH=. /tmp/jzpack-research-env/bin/python -m pytest -q
PYTHONPATH=. /tmp/jzpack-ci-20261001-minimum-py311/bin/python -m pytest -q
/tmp/jzpack-research-env/bin/python -m ruff check .
/tmp/jzpack-research-env/bin/python -m compileall -q jzpack tests benchmarks tools
/tmp/jzpack-research-env/bin/python -m build --outdir /tmp/jzpack-research/release-0.5.1-final-build
PYTHONPATH=. /tmp/jzpack-research-env/bin/python tools/verify_fidelity_mutations.py --json-out /tmp/jzpack-research/wave-3-final-mutations.json
PYTHONPATH=. /tmp/jzpack-ci-20261001-minimum-py311/bin/python tools/verify_fidelity_mutations.py --json-out /tmp/jzpack-research/wave-3-final-minimum-mutations.json
/tmp/jzpack-release-0.5.1-wheel-env/bin/python /tmp/jzpack-release-0.5.1-smoke.py
git diff --check
```

The next implementation priorities are a validated single-run RLE decode experiment, profiled
writer CPU reduction without weakening snapshots/limits, and a streamed MessagePack comparator.
Native work and new wire formats remain conditional on those measurements. The repository still
has no architecture diagram index; README and FORMAT supply the unchanged API/wire boundaries.
