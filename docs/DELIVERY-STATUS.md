# Delivery status

This page summarizes release state and current evidence gaps. The per-release
records preserve exact commits, CI and publishing results, review scope, artifact hashes,
and installation checks.

## Published releases

| Version | State | Records |
|---|---|---|
| 0.5.0 | Published | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.0) · [PyPI](https://pypi.org/project/jzpack/0.5.0/) · [verification record](evidence/release-0.5.0.json) |
| 0.5.1 | Published | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.1) · [PyPI](https://pypi.org/project/jzpack/0.5.1/) · [verification record](evidence/release-0.5.1.json) |
| 0.5.2 | Published | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.2) · [PyPI](https://pypi.org/project/jzpack/0.5.2/) · [verification record](evidence/release-0.5.2.json) |
| 0.5.3 | Published | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.3) · [PyPI](https://pypi.org/project/jzpack/0.5.3/) · [verification record](evidence/release-0.5.3.json) |
| 0.5.4 | Published | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.4) · [PyPI](https://pypi.org/project/jzpack/0.5.4/) · [verification record](evidence/release-0.5.4.json) |
| 0.5.5 | Published; latest recorded release | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.5) · [PyPI](https://pypi.org/project/jzpack/0.5.5/) · [delivery ledger](RELEASES.md#verified-deliveries) |

Version 0.5.3 passed its exact-commit hosted checks and trusted-publishing
workflow. PyPI artifact hashes were matched to workflow artifacts, and an install from
the official index passed the recorded smoke checks. The verification record is
authoritative for the full scope and limitations.

Version 0.5.4 passed all nine exact-commit CI jobs, including eight full-suite lanes
with 470 tests each, and was published through PyPI Trusted Publishing. The official
wheel and source distribution match the GitHub Actions artifacts byte-for-byte; a
fresh Python 3.12 install from PyPI passed the installed-package smoke check. See the
[0.5.4 record](evidence/release-0.5.4.json) for hashes, provenance, and limits.

Version 0.5.5 passed exact-commit CI and PyPI Trusted Publishing. Its official artifact
hashes match the recorded build, and fresh Python 3.12 installs from the PyPI index and
the hash-verified wheel passed compression/decompression and bounded-writer round trips
outside the checkout. The [0.5.5 delivery ledger](RELEASES.md#verified-deliveries)
records the release, workflow runs, hashes, and verification details.

## Review and packaging caveats

Different-author review cleared the 0.5.2 release metadata and notes, the RLE
and ASCII changes, and diagnostic contracts. After two reviewers reached the account
usage limit, the streamed comparator received an orchestrator cold second-pass
self-review after corrections; that review was not a different-author approval. The
release record preserves this distinction.

Version 0.5.3 fixes the PyPI documentation links and excludes generated caches from
source archives. Both official artifact hashes match the local and workflow builds.
The refreshed build/test/artifact actions and cache-sentinel guard passed hosted
execution; publication protections remained enabled.

Version 0.5.4 reuses flattened schema values from the bounded writer's defensive
snapshot, removing a repeated schema walk. The measured writer preparation change
reduces encoding medians by 14.214% on the
schema-diverse case and 4.848% on the nested case; its large-string result remains
within run noise. One public GH Archive sample improved 14.9% in an encoding-only
comparison. These workload measurements do not establish universal leadership.

Version 0.5.5's bounded-writer preflight change reduced writer latency by 3.1–10.3%
across two paired five-sample runs on the recorded 50k schema-diverse and 40k nested
workloads. Archive hashes and round trips matched. These results remain workload- and
environment-specific; see the [writer benchmark records](WRITER.md#encoding-time-comparison).

## Remaining evidence gaps

- **Measured scope:** The streamed MessagePack comparison is a benchmark experiment, not a
  production reader. It requires a known row count, may yield rows before final frame
  checksum verification, accepts the full compressed archive as bytes, and does not cap
  native decompressor output. See [the full comparison and limits](STREAMED-BASELINE.md).
- **Measured:** The source-tree bounded-writer update removes a repeated schema flattening
  walk and reduces encoding medians by 14.214% on the schema-diverse case and 4.848% on
  the nested case; its large-string result is within run noise. The writer remains about
  23× and 25× slower than the streamed comparator on those cases. See the
  [source-pinned profile and raw captures](WRITER-CPU-PROFILE-WAVE5.md).
- **Measured:** The 0.5.5 writer preflight change reduces latency by 3.1–10.3% on two
  recorded workloads across two paired runs, with matching archive hashes and round trips.
  See the [raw writer benchmark records](WRITER.md#encoding-time-comparison).
- **Measured scope:** A single public GH Archive sample adds an encoding-only comparison
  with parsing outside the timer and no memory measurement. It shows a 14.9% median
  improvement over the prior writer on that host; it is not a cross-platform ranking.
- **Unestablished:** Universal performance leadership, complete input coverage, and broad adoption have not been established. The [test coverage map](TEST-COVERAGE.md) records visible verification gaps.
- **Planned:** Profile the remaining encoded-body and decoder reconstruction passes on
  representative inputs, then change them only when paired measurements show a useful
  gain and the full fidelity/resource-limit suite still passes. Larger real inputs and
  additional environments remain needed before broad performance claims.
- **Conditional:** Access-format and ecosystem work should stay conditional on concrete workload
  and adoption evidence; a new wire format, cross-language implementation, or adapter
  adds compatibility and maintenance costs.

## Source documents

- [Implementation plan](IMPLEMENTATION-PLAN.md) — the single editable plan and its acceptance gates.
- [Test coverage map](TEST-COVERAGE.md) — public-contract tests, retained fixtures, and remaining coverage gaps.
- [Benchmark index](../BENCHMARKS.md) — measured results with links to
  detailed methods and raw reports.
- [Release procedure](RELEASES.md) — repeatable publication gates.
