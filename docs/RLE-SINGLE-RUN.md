# Single-run RLE decode

The decoder now uses Python list repetition for an already validated single-run payload. It first
checks each run's shape and positive supported count, checked total against `sys.maxsize`, and
applied `max_output_size`. Only then does the exact built-in-list fast path return
`[value] * total`. This preserves the existing nested-value aliasing behavior and makes no wire
format change. Other payloads continue through the existing fill loop.

## Validation

`tests/test_rle_single_run.py` covers exact scalar types and float bits, repeated nested-object
identity, the host list-size and caller output-limit checks, a public constant-column round trip,
and a legacy nested value inside an RLE payload.

## Paired decode measurement

`benchmarks/compare_rle_single_run.py` loads the old `RLEEncoder.decode` implementation from the
pinned repository object `3f2e37e1db3c3b9653fc52bc02330128d8157440`. Reproduction therefore requires
that Git object and the repository checkout; an installed wheel or shallow clone without the object
cannot reproduce the baseline. The harness records the baseline and candidate source hashes, runtime
metadata, input fingerprints, archive checksums, every paired sample, and the order in each pair.
Exact identity checks for direct single-run outputs and the exact record oracle for public archives
run outside timed intervals. Input generation, fingerprints, archive compression, and baseline AST
extraction are also excluded from timings.

Direct decoding measures single runs of 1, 128, 4,096, 50,000, and 250,000 values with the exact
`max_output_size` set. Public measurements decode prebuilt 10,000-record archives for a constant
column, the nested-record corpus, and the schema-diverse corpus. They include `decompress` and its
record-limit checks; archive construction and full exact-output validation are outside the timer.
Baseline-first and candidate-first order alternates each sample pair. These synthetic results measure
the tested shapes only; they do not establish a general decoder speedup or predict every workload.

On CPython 3.12.13 / arm64, the 15-pair medians were:

| Case | Baseline | Candidate | Median shift |
| --- | ---: | ---: | ---: |
| Direct single run, 1 item | 0.000334 ms | 0.000292 ms | -12.6% |
| Direct single run, 128 items | 0.000916 ms | 0.000333 ms | -63.6% |
| Direct single run, 4,096 items | 0.030542 ms | 0.000584 ms | -98.1% |
| Direct single run, 50,000 items | 0.383917 ms | 0.004458 ms | -98.8% |
| Direct single run, 250,000 items | 1.938666 ms | 0.015208 ms | -99.2% |
| Public constant-column archive, 10,000 rows | 2.491041 ms | 2.294125 ms | -7.9% |
| Public nested-record archive, 10,000 rows | 26.134791 ms | 26.584125 ms | +1.7% |
| Public schema-diverse archive, 10,000 rows | 18.084583 ms | 17.980125 ms | -0.6% |

The direct 1-item case is close to the timer floor. For the constant-column public workload, the
candidate was faster in all 15 pairs. The nested and schema-diverse public workloads had large
pair-to-pair variation (candidate faster in 7/15 pairs in each); their small median shifts are
inconclusive. The result justifies the low-risk shortcut for a validated single run, not a blanket
decompression performance claim. Raw per-pair samples and source hashes remain in the JSON report.

The retained raw samples are in [`benchmarks/results/rle-single-run.json`](../benchmarks/results/rle-single-run.json).
Reproduce them from the repository root with:

```bash
PYTHONPATH=. python benchmarks/compare_rle_single_run.py \
  --samples 15 --warmups 2 --records 10000
```

The command writes the machine-readable report to `benchmarks/results/rle-single-run.json` by
default. It overwrites that file when run again.
