# CPU wave 3: nested record reconstruction

This change optimizes one decode path in `SchemaReconstructor._build_record`.
For a two-segment schema path it now creates the parent mapping only when absent
and assigns the leaf directly. One-segment paths are unchanged; paths with three
or more segments still use `_set_nested_value`. Encoding, archive framing,
compression settings, and wire bytes are unchanged.

The compared `jzpack/schema.py` SHA-256 values are base
`31d875e4cbc48b4c2f5b1a1fb6f9350ef7e46cf11f6fbf4f54f5d33f17952971` and
candidate `75787c6f54badc28ddf5c1eab8742cc64ae30b3dda0d5ef42b7cba00c547761e`.

## Profile finding

The base commit was `3e87f770be076cac166caf7fd35de22c81c3782b`. The deterministic
corpus profiles were generated with seed `1729`, 10,000 records per profile,
and `JZPackCompressor(fast=False)`. cProfile self time was summed across the
five profiles. Before the change, `_build_record` used the generic nested setter
for every multi-segment path:

| Decode profile | Base | Candidate |
| --- | ---: | ---: |
| Total Python self time | 0.103485 s | 0.098075 s |
| `_set_nested_value` calls | 35,000 | 0 |

The measured corpus paths all fit the two-segment fast path. The fidelity test
also covers a deeper path, which continues through the generic helper. A
separate compression profile showed `_flatten` at 0.043147 s (23.1% of Python
self time), but a flattening prototype regressed four of five corpus profiles
and was discarded.

## Interleaved decode comparison

For each 10,000-record profile, the archive was created once, then the base
implementation and candidate were each warmed once and timed in 15 alternating
order pairs. Exact round trips were checked with `tests.fidelity_oracle` outside
the timer. The base behavior is the implementation at the commit above: assign
one-segment paths directly and send every longer path to
`_set_nested_value`. These measurements include decode only; archive setup and
exact comparison are excluded.

| Profile | Base median | Candidate median | Change |
| --- | ---: | ---: | ---: |
| mixed-events | 10.9432 ms | 10.0721 ms | -8.0% |
| optional-fields | 12.5883 ms | 12.1978 ms | -3.1% |
| high-entropy | 4.3610 ms | 4.3372 ms | -0.5% |
| nested-arrays | 14.9863 ms | 13.7615 ms | -8.2% |
| integer-series | 5.1205 ms | 5.1155 ms | -0.1% |

Raw samples in milliseconds, in measurement order:

| Profile | Base samples | Candidate samples |
| --- | --- | --- |
| mixed-events | 12.5971, 10.9025, 10.7014, 10.8279, 10.8360, 12.8614, 11.4234, 10.7259, 12.8407, 10.8173, 10.9914, 10.4775, 10.9432, 13.0131, 18.2124 | 10.4330, 10.0721, 9.6697, 11.6917, 9.8293, 9.9830, 10.0171, 10.7650, 10.0691, 10.1115, 12.4505, 10.2572, 10.2589, 9.9317, 9.8797 |
| optional-fields | 11.6706, 12.3501, 12.6426, 11.9252, 12.5883, 12.7879, 11.4238, 13.2779, 12.6629, 12.4112, 12.0194, 14.2732, 13.2554, 12.7448, 12.2275 | 12.1695, 12.1735, 12.0479, 14.4465, 11.6575, 12.0657, 12.3999, 12.2141, 11.8819, 12.4567, 12.2244, 12.3185, 12.0225, 12.8195, 12.1978 |
| high-entropy | 4.7187, 4.4747, 5.2654, 4.2446, 4.2253, 4.2995, 4.3610, 4.4813, 4.3175, 4.1612, 4.4926, 6.0724, 4.2955, 4.3332, 4.7371 | 4.3832, 4.5718, 4.3070, 4.3295, 4.3418, 4.2173, 4.2177, 4.2702, 4.4774, 4.3888, 4.2502, 4.2157, 4.5990, 4.3996, 4.3372 |
| nested-arrays | 17.8128, 14.9863, 23.2757, 14.6365, 14.1047, 15.0584, 14.4746, 15.4930, 14.8414, 18.8193, 14.2813, 22.5999, 14.6657, 23.6034, 14.2540 | 14.1127, 13.7646, 13.9396, 14.0203, 21.6724, 13.5936, 13.4286, 21.4421, 13.5437, 31.3290, 13.5337, 13.4255, 13.4680, 13.7058, 13.7615 |
| integer-series | 6.3350, 5.0012, 5.0185, 5.4190, 5.0927, 5.1205, 5.2213, 5.2296, 5.2738, 5.0613, 4.8969, 5.0169, 5.1560, 5.7090, 5.0805 | 5.2625, 5.3635, 5.1405, 5.3732, 5.1052, 5.0735, 4.9240, 4.9538, 5.4877, 5.0232, 5.0434, 5.6009, 5.0903, 5.1515, 5.1155 |

Only the mixed-event and nested-array profiles show a material improvement in
this isolated comparison. The other changes are small relative to their sample
spread and are treated as inconclusive.

`benchmarks/compare_reconstruction.py` is the reproducible capture harness. It
loads `_build_record` from the pinned base commit with `git show`, verifies the
full baseline `schema.py` SHA-256 before timing, imports the candidate from the
working tree, and records both source hashes, the harness hash, safe runtime
versions, archive/input fingerprints, and every timing sample. It restores the
candidate method in a `finally` block. The exact oracle runs after each timer
stops.

```sh
PYTHONPATH=. /tmp/jzpack-research-env/bin/python benchmarks/compare_reconstruction.py \
  --profiles mixed-events optional-fields high-entropy nested-arrays integer-series \
  --records 10000 --samples 15 --warmups 1 --seed 1729 \
  --output benchmarks/results/cpu-wave3-decode-compare.json
```

The comparison excludes corpus generation, reference fingerprinting, archive
compression, and exact-value validation from decode timings. Each profile's
archive is compressed once before warmups. It reuses one decoder context per
profile and alternates which implementation runs first for each sample pair.
The retained [machine-readable report](../benchmarks/results/cpu-wave3-decode-compare.json)
preserves all integer-nanosecond samples and metadata; its SHA-256 is
`66f2f5f63b6f45d08b5c86e643e0bf3e945fddf902d527a0abdd201a5c62ab1f`. Its
matched medians were:

| Profile | Baseline | Candidate | Change |
| --- | ---: | ---: | ---: |
| mixed-events | 10.2653 ms | 9.4466 ms | -8.0% |
| optional-fields | 11.1789 ms | 11.2595 ms | +0.7% |
| high-entropy | 3.9078 ms | 3.9574 ms | +1.3% |
| nested-arrays | 12.2377 ms | 11.1709 ms | -8.7% |
| integer-series | 4.8776 ms | 4.9025 ms | +0.5% |

Only mixed-events and nested-arrays show a material improvement in this new
capture. The other three profiles are within small differences and should be
treated as inconclusive. The historical inline 15-pair samples above remain
separate evidence, not a replacement for this source-verified report.
This harness is repository-only: it imports the in-tree corpus and fidelity
oracle, and `git show` must be able to read the pinned base object locally. A
shallow clone that lacks `3e87f770be076cac166caf7fd35de22c81c3782b` and an
installed wheel are not supported reproduction environments.

## Public corpus CLI check

The full comparative corpus CLI was run serially at base and candidate with the
same command. The timing table lists medians; decode samples are preserved below
to show the noise and the optional-fields regression.

```sh
PYTHONPATH=. /tmp/jzpack-research-env/bin/python benchmarks/benchmark_corpus.py \
  --profiles mixed-events optional-fields high-entropy nested-arrays integer-series \
  --records 3000 --iterations 3 --warmups 1 --seed 1729 --level 3 --skip-rss --json
```

Runtime for both runs: Python 3.12.13, macOS 27.2 arm64, jzpack 0.4.0,
msgpack 1.2.3, orjson 3.12.0, zstandard 0.25.0, native zstd 1.5.7. The base
and candidate runs were serialized, not concurrent. The first run used a
detached checkout at the base commit; the second used the candidate worktree.

| Profile | Reference input bytes / SHA-256 | Archive bytes / SHA-256 |
| --- | --- | --- |
| mixed-events | 402321 / `dba43c81f5054143781b69c43e4315f5560375b07a92347115dad198253a0bd7` | 928 / `5aa588913553663cea475f5f439d4e6f51f5bf7472b77d4d5e37df8a1a5a3b6b` |
| optional-fields | 206664 / `3073cdb197027192c6660ed3ad6ac48c0a96afceeaa8d4f65fbf98a70e96ea28` | 31151 / `c55cf702c7ca46e1f163d9759690c650e1c1286d345da18426b94c4a652bc490` |
| high-entropy | 511890 / `9370a523cd3466a5e10c06d7e68408240e94f764c263b5c91333fe2546d6eae9` | 301285 / `826a9e5f034873415619d7f1cc8977009800e1cd0da4d9e6e3cdad4af6defa0e` |
| nested-arrays | 537718 / `209dc3e870bcddcaf1675229f95763ec5eb3d7c2a6aef95deb510a4ca0517fb0` | 9536 / `d3b7688be87cf0d61371f0194505bbb5181cd2cb9242d74a29ec33eaa61d4ded` |
| integer-series | 264301 / `768261daf5456537c930410ad0ce7db710397c2d3b792d8c5a8047a977ed386b` | 406 / `63ed7513f1f931cfb9676697d2c028c8884e4df44d08a755c49daee030a5bf48` |

Each base/candidate input fingerprint, archive SHA-256, and archive size match;
the CLI also validated exact round trips. The source change is decode-only, so
compression timing movement is not attributed to it.

| Profile | Compress cold | Compress reused | Decode cold | Decode reused |
| --- | ---: | ---: | ---: | ---: |
| mixed-events | 9.534 → 9.362 ms (-1.8%) | 9.370 → 9.319 ms (-0.5%) | 3.234 → 3.012 ms (-6.9%) | 3.216 → 3.336 ms (+3.7%) |
| optional-fields | 6.382 → 6.579 ms (+3.1%) | 6.310 → 6.371 ms (+1.0%) | 4.619 → 4.871 ms (+5.4%) | 4.722 → 5.009 ms (+6.1%) |
| high-entropy | 3.803 → 3.778 ms (-0.6%) | 3.811 → 3.915 ms (+2.7%) | 1.317 → 1.293 ms (-1.9%) | 1.296 → 1.315 ms (+1.5%) |
| nested-arrays | 7.408 → 7.205 ms (-2.7%) | 7.515 → 7.275 ms (-3.2%) | 4.258 → 4.047 ms (-4.9%) | 4.642 → 4.393 ms (-5.4%) |
| integer-series | 5.117 → 5.275 ms (+3.1%) | 5.158 → 5.085 ms (-1.4%) | 1.567 → 1.496 ms (-4.5%) | 1.547 → 1.561 ms (+0.9%) |

The CLI comparison has three timed samples per cell and one warmup. Raw decode
samples, milliseconds in order:

| Profile | Cold base → candidate | Reused base → candidate |
| --- | --- | --- |
| mixed-events | 3.120, 3.239, 3.234 → 2.883, 3.145, 3.012 | 3.301, 3.216, 3.140 → 3.336, 3.300, 4.169 |
| optional-fields | 4.502, 4.896, 4.619 → 4.692, 5.247, 4.871 | 4.722, 4.617, 6.960 → 5.009, 4.918, 6.851 |
| high-entropy | 1.249, 1.317, 1.743 → 1.279, 1.346, 1.293 | 1.284, 1.299, 1.296 → 1.386, 1.282, 1.315 |
| nested-arrays | 3.970, 4.258, 7.302 → 3.445, 4.047, 6.758 | 7.187, 3.910, 4.642 → 7.008, 3.519, 4.393 |
| integer-series | 1.435, 1.567, 1.748 → 1.466, 1.713, 1.496 | 1.539, 3.050, 1.547 → 1.561, 2.927, 1.558 |

The matched CLI data is noisy: optional-fields is slower in both decode modes,
mixed-events regresses in reused mode, and several samples have clear outliers.
These results do not support a broad speedup claim. The isolated interleaved
comparison and reduced generic-helper calls support retaining this small
workload-specific fast path; run larger repeated measurements before making
product-level throughput claims.

## Limits

The corpus and tests cover synthetic records, not every application workload.
The CLI used three timing samples per cell and `--skip-rss`; this change has no
RSS claim. The 10,000-record interleaved comparison is a decode-only microbench
with setup excluded. Neither dataset demonstrates an end-user throughput or
memory guarantee.
