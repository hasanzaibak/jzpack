# Codec guard cost measurements

This change reduces repeated validation work in RLE analysis, integer delta
encoding, and delta decoding. The wire format and encoding choices remain
unchanged. The tests also keep the full-column guard behavior explicit: an
unsupported nested value after the analyzer's sample still forces RAW, and a
mixed numeric tail still cannot enter the integer-delta path.

## Source and benchmark setup

The baseline source is commit `3f717531174edf75c2672b005291ceae79935b76`.
The compared analyzer and encoder Git blob IDs were:

| File | Baseline blob | Measured candidate blob |
| --- | --- | --- |
| `jzpack/analyzer.py` | `792820001405d95e7cdb713335c7a663557a77c1` | `a77e0b8245abc5e97408d000d2f44cc367b85714` |
| `jzpack/encoders.py` | `923b1c02cc83a43951602708941b43ecd1cfa04f` | `2c3ee9522db41d44041b4e1c8dda7698909df20a` |

The comparative run used the deterministic corpus v1 with seed 1729, 3,000
records in each of five profiles, compression level 3, one warmup, and three
timed iterations. It measured reused compressor/decompressor contexts and
skipped process RSS. The same Python 3.12.13 environment was used for both
source versions: msgpack 1.2.3 with its native extension, zstandard 0.25.0,
and native zstd 1.5.7 on macOS 27.2 arm64. Every input fingerprint,
compressed size, and compressed-output SHA-256 matches between the base and
final candidate; the raw JSON files contain the full values and all timing
samples. Each round trip passed the benchmark's recursive type and float-bit
comparison.

| Profile | Reused encode median, base / candidate runs (ms) | Reused decode median, base / candidate runs (ms) | Output SHA-256 prefix |
| --- | ---: | ---: | --- |
| High entropy | 5.632 / 5.511 / 4.950 | 1.779 / 1.784 / 1.608 | `826a9e5f0348` |
| Integer series | 7.548 / 6.535 / 6.457 | 1.938 / 1.517 / 1.533 | `63ed7513f1f9` |
| Mixed events | 10.712 / 9.343 / 9.233 | 3.809 / 3.233 / 3.242 | `5aa588913553` |
| Nested arrays | 10.452 / 9.789 / 9.904 | 4.435 / 4.354 / 4.186 | `d3b7688be87c` |
| Optional fields | 6.773 / 6.075 / 6.072 | 4.760 / 5.008 / 4.805 | `c55cf702c7ca` |

The two candidate values are independent runs of the final source, not
aggregated samples. Results support a local improvement for integer, mixed,
and nested workloads; changes of a few tenths of a millisecond are within the
variation visible in these short runs. Optional-field decoding did not show a
consistent improvement. These measurements describe this host and corpus,
not a general performance guarantee.

An earlier candidate run produced a 10.58 ms nested-array decode median, with
samples from 7.57 to 12.41 ms. The same pre-final candidate's repeat measured
4.76 ms. Both raw runs are retained as diagnostic evidence. That candidate
made the public RLE equality helper call a second Python helper; the final
source inlines that equality check and was benchmarked again twice above. The
diagnostic timing is not used in the table or in a performance claim.

## Profile evidence

The same Python 3.12.13 interpreter profiled each source version for 25 calls
per workload. The counts explain which guard work was removed; cProfile's
instrumented durations are useful for comparing these functions, while the
unprofiled corpus measurements above are the user-facing timing evidence.

| Workload | Base profile | Candidate profile |
| --- | --- | --- |
| High-entropy RLE analysis | 82,500 support checks; 18.390 ms in suitability / 26.594 ms total encode | 7,525 support checks; 2.636 ms in suitability / 10.465 ms total encode |
| Repeated-string RLE | 224,950 support checks; 65.807 ms total encode | 149,975 support checks; 51.130 ms total encode |
| Integer-series encoding | 50 `can_encode` calls; 382,450 integer checks; 66.105 ms total encode | 25 `can_encode` calls; 307,475 integer checks; 37.437 ms total encode |
| Integer delta decoding | 149,975 `_is_supported_number` calls; 30.250 ms total | no per-value helper calls; 6.524 ms total |
| Float delta decoding | 149,975 `_is_supported_number` calls; 32.229 ms total | no per-value helper calls; 4.610 ms total |

RLE suitability now validates each value while counting runs, so high-entropy
columns stop once the run threshold makes RLE impossible. A column is still
selected for RLE only after every value has passed the supported-value check;
the exact-type and IEEE-754-bit comparator remains in use. Delta eligibility
validates the full column in one loop without a `values[1:]` copy. Direct
delta encoding validates each value and delta as it constructs the output,
preserving its `ValueError` contract. Delta decoding keeps strict exact-type
and integer-range checks inline and tracks when arithmetic transitions from
integers to floats, so legacy float-delta payloads remain readable.

## Verification

The eight new behavioral checks cover valid RLE and delta payload shapes,
unsupported and mixed-type tails beyond the analyzer sample, direct malformed
delta encoder inputs, and a legacy numeric type transition. They complement
the existing fidelity tests rather than asserting operation counts.

| Check | Runtime | Result |
| --- | --- | --- |
| `PYTHONPATH=. /tmp/jzpack-ci-20261001-py312/bin/python -m pytest -q` | Python 3.12.13; msgpack 1.2.3 native; zstandard 0.25.0 | 265 passed |
| `PYTHONPATH=. /tmp/jzpack-ci-20261001-minimum-py311/bin/python -m pytest -q` | Python 3.11.15; msgpack 1.0.0 pure-Python fallback; zstandard 0.21.0 | 265 passed |
| `/tmp/jzpack-ci-20261001-py312/bin/ruff check .` | Python 3.12.13 | passed |
| `/tmp/jzpack-ci-20261001-py312/bin/python -m build --outdir /tmp/jzpack-codec-costs-dist-20261001` | Python 3.12.13 | sdist and wheel built |
| `git diff --check` | worktree | passed |

Python 3.10 was not available for local verification. The minimum local
runtime was Python 3.11.15.

The retained evidence files are:

- [Baseline corpus run](evidence/codec-costs-before.json)
- [Final candidate corpus run 1](evidence/codec-costs-after.json)
- [Final candidate corpus run 2](evidence/codec-costs-after-repeat.json)
- [Pre-final diagnostic outlier run](evidence/codec-costs-after-pre-inline-outlier.json)
- [Pre-final diagnostic repeat](evidence/codec-costs-after-pre-inline-repeat.json)
- [Baseline cProfile output](evidence/codec-profile-before.txt)
- [Final candidate cProfile output](evidence/codec-profile-after.txt)
