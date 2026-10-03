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
| 0.5.3 | Published; latest recorded release | [GitHub](https://github.com/hasanzaibak/jzpack/releases/tag/v0.5.3) · [PyPI](https://pypi.org/project/jzpack/0.5.3/) · [verification record](evidence/release-0.5.3.json) |

Version 0.5.3 passed its exact-commit hosted checks and trusted-publishing
workflow. PyPI artifact hashes were matched to workflow artifacts, and an install from
the official index passed the recorded smoke checks. The verification record is
authoritative for the full scope and limitations.

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
- **Measured scope:** A single public GH Archive sample adds an encoding-only comparison
  with parsing outside the timer and no memory measurement. It shows a 14.9% median
  improvement over the prior writer on that host; it is not a cross-platform ranking.
- **Unestablished:** Universal performance leadership, complete input coverage, and broad adoption have not been established. The [test coverage map](TEST-COVERAGE.md) records visible verification gaps.
- **Planned:** Profile the remaining bounded-writer preflight and encoded-body passes on
  representative inputs, then remove work only when paired measurements show a useful
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
