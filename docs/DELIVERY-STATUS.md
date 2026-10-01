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
| Data fidelity and codec boundaries (JZ-01–04) | `codex/jzpack-fidelity` | Awaiting independent review | Seven reproduced corruption/overflow cases fixed; 199 passing tests; exact type/float-bit assertions; regressions failed before repairs |
| Existing public and file contracts | `codex/jzpack-test-contracts` | In independent review | 184 passing tests; distinct failure, ownership, resource-boundary, recovery, and schema tests; remaining gaps documented |
| Benchmark corpus and focused performance (JZ-05–08) | `codex/jzpack-performance` | In implementation | Measure flatten-once ingestion and strict single-pass Zstd decoding; verify minimum dependency compatibility |
| Durable delivery plan | `codex/jzpack-delivery-plan` | In preparation | This status page and the draft implementation plan |
| Bounded v3 writer | Pending | After first-wave integration | Row/byte/complexity bounds, sink backpressure, fault tests, and isolated RSS probes required |
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
