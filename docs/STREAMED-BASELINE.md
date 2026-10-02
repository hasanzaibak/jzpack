# Record-stream MessagePack and Zstandard comparison

This wave adds a genuine record-at-a-time MessagePack plus Zstandard encode and
decode path beside the full-list baseline. The raw capture is
[`streamed-baseline-wave4.json`](../benchmarks/results/streamed-baseline-wave4.json).
It uses the existing version-2 synthetic corpus and its full recursive oracle:
exact built-in types, mapping keys, list lengths, values, binary64 float bits,
and complete output length. Missing keys remain distinct from explicit nulls.

The capture was run from source checkout `492adad5afccf0b2b87188b9a4d0bfe19dd2feaf`.
Package metadata reported version `0.5.1`, while the pinned runtime sources
include the reviewed ASCII string-sizing and single-run RLE optimizations being
prepared for the upcoming 0.5.2 release. This is not a measurement of the
published 0.5.1 wheel or the final 0.5.2 package. The raw report contains SHA-256
hashes for the harness, corpus generator, and every `jzpack/*.py` runtime module;
the harness was uncommitted during capture, so the report pins its source hash
and marks the checkout dirty. The test file was also uncommitted and is not
hashed in the raw report; identify it through the later reviewed component
commit.

## Methods and boundaries

The streamed baseline writes one MessagePack array header, whose row count is
known from the benchmark case, then packs and writes one record at a time into a
single checksummed Zstandard frame. Its normal timed encode does not force a
flush per row. The decoder incrementally feeds fixed compressed-input slices
to a Zstandard decompression object and its output to a MessagePack `Unpacker`,
then yields individual maps. It requires the expected array count, the declared
number of string-keyed map rows, a completed frame with a checksum, no trailing
compressed data, and exact consumption of the MessagePack body. Full validation
occurs only when the iterator is exhausted. Rows can be yielded before the
single frame checksum is verified; a consumer that stops early has not
validated the archive. JZPack's iterator verifies each complete chunk before
yielding that chunk's rows and can recover at later valid chunk boundaries.

The MessagePack writer needs the row count before it starts, unlike JZPack's
unknown-length iterator writer. This is a benchmark-specific prerequisite, not
a general unknown-length streaming API. The two formats also have different
framing, checksums, recovery, and resource-limit contracts; the comparison is
only about these generated supported-value records.

The decoder takes the complete compressed archive as a `bytes` object, and the
timed decode for every mode materializes the complete output list. The memory
probes measure encode workflows only: each starts from a deterministic lazy
corpus generator and writes to the same hashing-discard sink. List APIs
materialize their input in that measured region; the writer APIs consume the
generator incrementally. These probes do not establish bounded source-I/O or
decode memory. Although compressed input is fed in fixed-size slices, a
Zstandard decompression call can expand a slice into a large output block, so
this implementation is not an untrusted-input memory bound. The MessagePack
buffer limit does not cap native decompressor output.

Timed encodes start with prebuilt input lists. The two writer modes retain
output in an in-memory `BytesIO`; the list APIs return and retain the complete
archive bytes. In both cases, retrieving writer bytes, hashing, and exact
round-trip checking occur outside the encode timer. Timed decodes start from
those retained archive bytes and return a complete list. For memory probes, all
four methods use the same hashing-discard sink, and the report checks that each
probe produced the same archive hash as its timed encode. Traced allocation is active during each
memory operation. RSS is the process high-water mark, including native
allocations and tracing overhead; the reported delta is relative to the mark
after a 128-row warmup. A zero delta means the later high-water mark did not
exceed warmup, not that the operation used no memory. Traced elapsed times are
not comparable with the ordinary encode timings. Python's default garbage
collector policy is unchanged. Each decoded output and its archive are
explicitly released after exact-oracle verification, outside the timer and
before the next method's timed operation.

First-record availability is a separate experiment. The streamed MessagePack
probe writes the known-count header and first row, forces `FLUSH_BLOCK`, and
stops its timer after decoding a complete row; it does not validate the frame
checksum or completion. Writer close happens after the timestamp as cleanup.
This forced-flush path is not the normal timed encode policy, so its latency
cannot be paired with the normal archive size or encode throughput as one
operating point. The JZPack writer probe stops after the normal writer emits its
first complete, verified chunk and decodes the first row. The list APIs report
availability only after encoding and decoding the entire archive. These times
have different integrity boundaries and should not be ranked as equivalent
service latency.

## Results

The capture used corpus version 2, seed `20261001`, compression level 3, three
timed samples, one warmup, and three isolated memory samples per workload and
method. It ran on macOS 27.2 arm64 with Python 3.12.13, msgpack 1.2.3, and
Zstandard 0.25.0. Values below are medians; the raw JSON retains all samples,
input fingerprints, archive hashes, environment details, and source hashes.

| Profile (rows; canonical JSON bytes) | Method | Archive bytes | Encode ms | Full decode ms | Python traced peak MiB | RSS delta MiB |
|:---|:---|---:|---:|---:|---:|---:|
| `schema-diverse` (50,000; 14,363,207) | JZPack list | 1,266,330 | 270.8 | 87.4 | 92.00 | 228.41 |
| | JZPack writer | 1,440,234 | 1,121.7 | 150.4 | 3.23 | 7.44 |
| | MessagePack list + Zstandard | 1,674,766 | 33.9 | 58.5 | 94.04 | 200.00 |
| | MessagePack record stream + Zstandard | 1,685,830 | 40.8 | 82.5 | 0.34 | 1.84 |
| `nested-records` (40,000; 19,567,513) | JZPack list | 913,354 | 190.5 | 243.5 | 126.11 | 285.17 |
| | JZPack writer | 1,283,701 | 1,593.1 | 244.4 | 2.66 | 7.47 |
| | MessagePack list + Zstandard | 1,653,286 | 53.7 | 121.8 | 125.60 | 251.27 |
| | MessagePack record stream + Zstandard | 1,627,785 | 56.8 | 237.5 | 0.34 | 1.80 |
| `large-strings` (512; 8,462,482) | JZPack list | 4,434,767 | 25.4 | 7.0 | 29.04 | 35.14 |
| | JZPack writer | 4,426,106 | 28.0 | 7.0 | 3.54 | 0.00 |
| | MessagePack list + Zstandard | 4,440,797 | 23.8 | 6.4 | 32.38 | 22.16 |
| | MessagePack record stream + Zstandard | 4,440,812 | 28.7 | 7.7 | 0.42 | 0.00 |

The streamed MessagePack path sharply lowers measured encode memory against the
JZPack writer in these probes: traced peaks were about 0.34–0.42 MiB versus
2.66–3.54 MiB. It encoded the first two workloads much faster than the JZPack
writer (40.8 ms versus 1,121.7 ms for schema-diverse; 56.8 ms versus 1,593.1 ms
for nested records), with archives 17.1% and 26.8% larger. On large strings,
encode time was similar (28.7 ms versus 28.0 ms) and its archive was 14,706
bytes (0.33%) larger than the JZPack writer's and 15 bytes larger than
full-list MessagePack's. Decode was not uniformly faster: streamed MessagePack
took 237.5 ms for nested records, compared with 244.4 ms for the JZPack writer
and 121.8 ms for the full-list MessagePack baseline. Relative to full-list
MessagePack, the record-stream path used much less measured encode memory but
was slower to encode and decode in these three workloads. These are workload- and
machine-specific tradeoffs, not a general performance or memory ranking.

The separate first-record probe medians show why availability requires its
integrity boundary alongside the time:

| Profile | JZPack writer: verified first chunk + row | Streamed MessagePack: forced-flush row, checksum pending | JZPack list: full archive + decode | MessagePack list: full archive + decode |
|:---|---:|---:|---:|---:|
| `schema-diverse` | 53.4 ms (2,247 rows in chunk) | 0.084 ms | 355.8 ms | 94.6 ms |
| `nested-records` | 39.6 ms (957 rows in chunk) | 0.085 ms | 444.9 ms | 188.5 ms |
| `large-strings` | 4.1 ms (63 rows in chunk) | 0.058 ms | 32.0 ms | 30.3 ms |

The streamed row is available before the frame checksum by design, while the
JZPack writer has already verified its chunk. The forced-flush work is not
included in the normal streamed encode samples. The list-mode values include
full archive creation and full-list decode, rather than first-row decode from a
stream.

## Reproduction and gaps

Reproduce the large capture and its correctness contracts from the repository
root with:

```sh
PYTHONPATH=. python benchmarks/benchmark_streamed_baseline.py \
  --suite wave-4 --samples 3 --warmups 1 --memory-samples 3 \
  --output benchmarks/results/streamed-baseline-wave4.json
PYTHONPATH=. python -m pytest -q tests/test_streamed_baseline.py
```

The contracts have no timing or RSS thresholds. They cover exact type and
float-bit round trips, missing versus null, reused input mappings, short and
invalid writes, declared row counts, trailing complete and incomplete
MessagePack data, missing/truncated/corrupt checksummed frames, additional
frames, checksum failure after a row has been yielded, timer/cleanup boundaries,
and safe CLI errors. The tests pass with the current and minimum supported
Python/dependency environments; the reported performance capture used only the
current environment above.

This wave does not include Parquet or Vortex. A future columnar comparison must
run its exact full-dataset eligibility oracle against this same corpus, keeping
missing and null distinct, preserving mixed built-in types and float bits, and
counting construction and export costs. The capture also does not measure
filesystem streaming, arbitrary unknown-count MessagePack sources, streaming
decode memory, malformed-input resource caps, other machines, or production
data. In particular, the one-frame comparator does not provide JZPack's
per-chunk integrity, footer row count, or recovery behavior.
