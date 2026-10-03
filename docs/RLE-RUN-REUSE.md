# RLE run reuse experiment

`ColumnEncoder` now builds final RLE run pairs during suitability analysis only
while the column has at most 32 runs. On the 33rd run it releases the partial
cache, completes the same suitability scan, and uses the existing encoder if
RLE is ultimately selected. This bounds speculative allocations for rejected
columns. The selection threshold, full support scan, exact-type and float-bit
comparisons, list-subclass fallback, and wire representation remain unchanged.
The measurements support a workload-specific result, not a general speedup.

The base is `995db735be80649945d10024238f2578e04c14e1`. The baseline
`jzpack/analyzer.py` SHA-256 is
`5a045774ce99c53ef5c373d42a1f7dd0693a36920afa1a311f34fd88932f01ae`; the
candidate SHA-256 is
`310a87e4c6be83bf76caeaf10d992e6a9b1c7595a290409f68ec2330ceabca8d`, and the
candidate package diff SHA-256 is
`812ca645b3988f8e2e4eb0f90e705b2be46107fd19697e0fd234fa9ced1263a4`. The
benchmark harness SHA-256 is
`facb028ea8cebf89dacda71ac47c16719db826cf81fe4fa2f3edbbed9bfd8d99`.

Fifteen alternating pairs with two warmups measured 3,000 repeated strings in
batches of 25 column encodes. The median fell from 14.6830 ms to 7.4694 ms
(-49.1%), with 15 candidate wins. Public compression on 3,000 records with one
repeated string field fell from 2.1767 ms to 1.8794 ms (-13.7%), also with 15
wins. Column wire bytes and public archive bytes matched the baseline. The
earlier raw column baseline was 14.910958 ms; the prior note's 14.7698 ms text
was a typo.

The five deterministic corpus-v1 profiles, each with 3,000 records, ranged
from -2.4% to +2.4%, with mixed pair outcomes. These corpus differences are
small and workload-specific. Every profile archive matched byte-for-byte and
passed exact round-trip checks outside timed intervals.

Three separate samples measured peak traced Python allocation for each of four
columns. The metric is peak traced bytes above pre-call traced current bytes;
input generation, serialization, and round trips are outside the measurement.
It does not measure RSS or native allocations. At 100,000 rows, constant input
measured 180 B baseline versus 196 B candidate (+16 B); 1%-run input measured
76,408 B on both; and the inclusive 10%-run threshold measured 800,728 B on
both. Immediately above the threshold, 100,001 rows with 10,001 distinct
integer runs selected RAW and measured 3,900 B on both. The integer values are
chosen so the existing delta suitability check also rejects them.

Validation passed with 476 tests on CPython 3.12.13, msgpack 1.2.3, and
zstandard 0.25.0, and 476 tests on CPython 3.11.15 with minimum runtime pins
msgpack 1.0.0 and zstandard 0.21.0. Ruff, compilation on both runtimes, and
`git diff --check` passed. The report contains raw timing pairs, allocation
samples, input fingerprints, exact output hashes, source hashes, and runtime
details: [`rle-run-reuse.json`](../benchmarks/results/rle-run-reuse.json),
SHA-256 `c2c53b7208f88cfc9f27d9f76bf303f8477eba26d562bab876a6a4466fb3693e`.

Repeat the measurements with:

```bash
PYTHONPATH=. python benchmarks/compare_rle_run_reuse.py --samples 15 --warmups 2
```
