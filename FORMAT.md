# JZPK format

This document describes the sole public JZPK wire format: the version 3 chunked container. It is
intended to be stable enough for other implementations to reproduce and decode the format without
depending on the Python package.

## Container

Every public file or byte payload is a version 3 outer container. Its exact header, chunk, and footer
grammar is specified below. The library emits and accepts version `3` only; standalone version `1`
and version `2` payloads are retired and raise `UnsupportedVersionError`.

Each non-empty chunk contains one internal payload with this shape:

```text
4 bytes: ASCII "JZPK"
1 byte:  format version
rest:    one Zstandard frame containing one MessagePack map
```

The internal payload header is always version `2`. It is not a standalone public JZPK format; it is
the fixed chunk representation used by version 3 so chunks remain independently decodable.

Version 2 writes Zstandard content checksums. Implementations should reject a bad checksum and
should expose decompression limits for untrusted input.

## Top-level MessagePack map

```text
{
  "s": schemas,
  "o": schema_order_rle
}
```

`s` is a map from deterministic schema IDs (`s0`, `s1`, ...) to schema maps. `o` is a run-length
encoded list describing the original record order. Empty input is represented by `{"s": {}, "o": []}`.

Each internal version 2 schema map is:

```text
{
  "k": [[path_segment, ...], ...],
  "c": [encoded_column, ...],
  "n": row_count
}
```

The entries in `k` and `c` are positional. A path with one segment is a top-level key. A path
with multiple segments represents nested mappings. Path segments are strings, so a literal key
containing `.` remains distinct from a nested path.

The version 1 representation is not supported.

## Supported values and fidelity

The fidelity guarantee applies to ordinary Python dictionaries and lists containing the following
built-in scalar types: `None`, `bool`, `str`, `bytes`, `int`, and `float`. Record and nested mapping
keys must be strings. Lists preserve their order, records preserve their order, and a missing field
remains distinct from a field explicitly set to `None`. Mapping key order is not part of the
guarantee. The guarantee starts with Python values passed to jzpack; it does not cover original JSON
whitespace, number spelling, duplicate textual object keys, or source bytes.

Integers must be in the MessagePack range `-2**63` through `2**64 - 1`, inclusive. Floats are
IEEE-754 binary64 values; the complete 64-bit representation is significant and preserved, including
signed zero, infinities, and NaN sign and payload bits. Strings are not normalized or translated.
Bytes are preserved as bytes.

Tuples are outside the fidelity guarantee. The current MessagePack serializer can encode a tuple as
an array, which the reader returns as a list; tuple identity is therefore not preserved. Custom
objects and container subclasses are also outside the guarantee. jzpack provides no custom object
conversion hook, so values unsupported by MessagePack ordinarily fail during serialization.

New writers use these encoding rules:

- RLE merges only values of the same supported built-in scalar type; floats are equivalent only
  when their 64-bit representations match. Nested lists and mappings use RAW.
- DELTA is selected only for columns of supported integers when every base and delta fits the
  MessagePack integer range. Float, mixed-type, and out-of-range-delta columns use RAW.
- Dictionary encoding is selected only for built-in strings. RAW stores the supported values
  directly.

For compatibility, this Python reader continues to decode historical numeric DELTA payloads whose
base or deltas are floats. Those payloads may already have lost distinctions during an older write;
decoding them cannot reconstruct the original values. Encoding IDs and payload shapes remain
unchanged in version 3. RLE readers continue to decode supported MessagePack values in run entries,
including nested values emitted by older writers, even though new writers keep nested values RAW.

## Column encodings

Every encoded column is a map with an integer `t` encoding type:

| `t` | Encoding | Fields |
|---:|---|---|
| 0 | Raw | `d`: values |
| 1 | Run-length | `d`: `[value, count]` pairs |
| 2 | Delta | `b`: first value, `d`: deltas |
| 3 | Dictionary | `m`: dictionary, `d`: integer indices |

The map must contain exactly the fields shown for its encoding. RLE data is a list of two-item lists;
each count is a positive supported MessagePack integer. Delta data has one base followed by a list of
supported integer or float deltas. Dictionary data uses non-negative integer indices into `m`.
Implementations must reject unknown encoding types and malformed field sets, counts, delta values,
lengths, or indices rather than silently treating them as raw data.

## Schema order

`o` is a list of `[schema_id, count]` pairs. Expanding those pairs produces one schema ID per
record. The expanded length must equal the sum of the schema row counts.

## Version 3 chunked container

Version 3 is the current writer and reader format. `compress` emits a valid single-chunk container
for non-empty input (and an empty header/footer container for empty input); the iterator API decodes
one chunk at a time.

Version 3 is an outer container containing a sequence of independently decodable internal version 2
payloads. This reuses the MessagePack schema and column encoding rules while giving the iterator a
natural chunk boundary.

### Versioning and compatibility

The first five bytes remain the discriminator used by current readers:

```text
4 bytes: ASCII "JZPK"
1 byte:  format version
```

The chunked container uses version `3`. The public reader accepts this version only and raises
`UnsupportedVersionError` for any other outer version.

The payload of a non-empty chunk is a complete internal `JZPK` version 2 byte stream, including its
five-byte header and Zstandard frame. The outer chunk header adds metadata around those bytes but
does not modify them. Empty input is represented by a version 3 header plus zero-count footer because
the base format does not allow a zero-record chunk.

### Byte-level grammar

All fixed-width integers in version 3 are unsigned, big-endian integers. The maximum representable
value is therefore `2**64 - 1`; an implementation must perform checked arithmetic before converting
lengths to host-language sizes. The CRC fields use CRC-32C (Castagnoli): reflected polynomial
`0x82F63B78`, initial value `0xFFFFFFFF`, final XOR `0xFFFFFFFF`, encoded big-endian.

The complete grammar is:

```text
v3_container = outer_header chunk_record* footer
chunk_record = chunk_header payload[payload_bytes]
```

The outer header is exactly 16 bytes and is written before any input is consumed:

| Offset | Width | Field | Required value/meaning |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `JZPK` |
| 4 | 1 | version | `3` |
| 5 | 1 | flags | `0` in the base format |
| 6 | 2 | header size | `16`, including this field |
| 8 | 4 | reserved | `0` |
| 12 | 4 | header CRC-32C | CRC of bytes 0 through 11 |

Global counts are deliberately not in the outer header: a bounded writer must be able to write it
to a non-seekable stream before it knows the final counts. A mandatory footer terminates every valid
container and carries the global counts.

Each chunk header is exactly 56 bytes:

| Offset | Width | Field | Required value/meaning |
|---:|---:|---|---|
| 0 | 4 | record tag | ASCII `CHNK` |
| 4 | 1 | chunk kind | `0`, complete embedded JZPK v2 payload |
| 5 | 1 | flags | `0` in the base format |
| 6 | 2 | header size | `56`, including this field |
| 8 | 8 | sequence | zero-based, exactly the next expected sequence number |
| 16 | 8 | record count | number of records in this chunk; at least 1 |
| 24 | 8 | uncompressed bytes | MessagePack body length inside the inner v2 Zstandard frame |
| 32 | 8 | compressed frame bytes | length of the inner v2 Zstandard frame only |
| 40 | 8 | payload bytes | length of the complete inner v2 payload; exactly `5 + compressed frame bytes` |
| 48 | 4 | reserved | `0` |
| 52 | 4 | header CRC-32C | CRC of bytes 0 through 51 |

The `payload_bytes` bytes immediately following the header are the complete inner v2 payload. Thus,
`payload_bytes` includes the inner five-byte `JZPK`/version-2 header and the Zstandard frame, while
`compressed_frame_bytes` excludes the inner five-byte header. Neither field includes the 56-byte
chunk header. `uncompressed bytes` excludes all headers and is the exact length of the bytes obtained
after decompressing the inner Zstandard frame, before MessagePack decoding.

The footer is exactly 56 bytes and must be the final bytes of the container:

| Offset | Width | Field | Required value/meaning |
|---:|---:|---|---|
| 0 | 4 | footer tag | ASCII `JZPF` |
| 4 | 1 | footer kind | `0` in the base format |
| 5 | 1 | flags | `0` in the base format |
| 6 | 2 | footer size | `56`, including this field |
| 8 | 8 | total record count | sum of all chunk record counts |
| 16 | 8 | total uncompressed bytes | sum of all chunk uncompressed-byte fields |
| 24 | 8 | total compressed frame bytes | sum of all chunk compressed-frame-byte fields |
| 32 | 8 | total payload bytes | sum of all chunk payload-byte fields |
| 40 | 8 | total chunk count | number of chunk records |
| 48 | 4 | reserved | `0` |
| 52 | 4 | footer CRC-32C | CRC of bytes 0 through 51 |

An empty input is an outer header followed immediately by a footer whose five totals are zero. A
chunk with zero records is not valid. A non-empty sequence of empty mappings is represented inside
one or more v2 chunks using the existing explicit empty-schema representation (`k: []`, `c: []`,
and `n` equal to the row count).

A reader must reject all of the following as `InvalidFormatError`:

- a truncated outer header, chunk header, inner payload, or footer;
- a bad header or footer CRC-32C;
- an unexpected tag, non-zero reserved field, unknown base-format kind/flag, or wrong header size;
- a zero chunk record count, a sequence number that is missing, duplicated, or out of order;
- `payload_bytes < 5`, `payload_bytes != 5 + compressed_frame_bytes`, or a payload length that
  exceeds the remaining input;
- totals that overflow checked arithmetic or do not equal the sums observed while reading;
- a missing footer or any trailing bytes after the footer; and
- an inner payload that is not a structurally valid JZPK version 2 payload, whose decompressed body
  length differs from `uncompressed bytes`, or whose decoded row count differs from `record count`.

The reader must check a length against the caller's safety limits before allocating that many bytes.
A well-formed but over-limit length is a `ResourceLimitError`, not a format error. An outer version
byte other than `3` is an `UnsupportedVersionError`.

For example, a two-record homogeneous chunk has this shape (bracketed values are fixed-width
big-endian fields, not literal text):

```text
JZPK 03 ... outer header ...
CHNK kind=0 sequence=0 records=2 uncompressed=N frame=M payload=5+M ...
  JZPK 02 [one Zstandard frame of]
    {"s": {"s0": {"k": [["id"]], "c": [{"t": 0, "d": [1, 2]}], "n": 2}},
     "o": [["s0", 2]]}
JZPF records=2 uncompressed=N frame=M payload=5+M chunks=1 ... footer CRC ...
```

The map shown is the decompressed inner MessagePack value. The actual chunk payload contains its
MessagePack serialization inside the inner Zstandard frame; the example is not an alternative wire
encoding.

### Chunk contents and schemas

Each chunk is an independent v2 payload, not a fragment of one global column store. Its `s` schema
map, `o` schema-order RLE list, column encodings, nested paths, and explicit row counts follow the
internal version 2 rules above. The inner map contains all schema information required to decode its
own chunk.

Schema IDs are local to one chunk. Within a chunk, `s0`, `s1`, and so on are assigned in first-seen
order of distinct sorted path tuples, matching the current `SchemaManager` behavior. IDs restart at
`s0` for the next chunk. The same shape in two chunks may therefore have the same ID by coincidence,
but IDs never have meaning across a chunk boundary. Schema paths are still structural lists of
strings, so dotted literal keys remain distinct from nested paths.

The expanded inner `o` list must contain exactly the chunk's records, in original order. Each schema's
`n` must equal the number of records of that schema in the chunk. Chunk order supplies the only
cross-chunk ordering rule; no global `o` list is needed. Heterogeneous schemas can appear in any
order, including alternating schemas, and a schema may be repeated independently in later chunks.

The base format does not allow zero-record chunks. Empty input is represented by zero chunks and a
zero-count footer. Records that are empty mappings are real records and use the existing v2 empty
schema with an explicit `n`; they are not dropped or confused with empty input.

The base chunk policy is an exact uncompressed-byte target, selected by the future writer as a
positive `target_chunk_bytes` value. For each next input record, the writer forms the candidate v2
payload using the current v2 encoding rules and measures the exact length of its uncompressed
MessagePack body. If the candidate is at most the target, the record is added. If it exceeds the
target and the current chunk is non-empty, the current chunk is emitted and the record is retried as
the first record of the next chunk. If one record by itself exceeds the target, it is emitted alone;
the target is soft for that case. This rule is deterministic and does not depend on compressed-size
variability between Zstandard implementations. The selected `fast`/analysis setting is part of the
chunk policy because it can change the measured v2 body.

The exact-size rule may require a future writer to serialize a candidate while deciding a boundary.
That is an intentional bounded lookahead, not permission to retain the complete input. A hard
`max_chunk_uncompressed_bytes` limit should be available to reject an oversized singleton instead
of emitting it. A caller that needs a strict byte bound must also set a maximum accepted record
size/complexity; no container can hold an intrinsically unbounded individual record within a fixed
chunk bound.

### Bounded-memory model

Version 3 enables bounded-memory file and stream APIs; it does not make an API that returns one
`bytes` object bounded, because that API must retain the complete output by definition. The existing
`compress`, `decompress`, `JZPackCompressor`, `StreamingCompressor`, and file-helper behavior must
not change as part of this design.

For a future writer that sends chunks directly to a sink, only the current chunk's records/columns,
its schema metadata, one candidate serialization, one Zstandard frame, and a constant number of
temporary buffers are retained. After the chunk is written, those objects are released before the
next chunk is accumulated. There is no global schema manager, global column store, or global input
list. If `T` is the target body size, `L` is the largest accepted record, `S` is bounded schema and
record-complexity metadata, and `W(level)` is the Zstandard workspace, the intended working-set
model is `O(T + L + S + W(level))`, with an implementation-dependent constant factor for candidate
serialization and compression buffers. The compressed output stream itself is not counted because
it is consumed by the caller.

The target is measured in serialized bytes, but Python object overhead, schema metadata, encoder
working space, and a large individual value can exceed the target. A bounded-memory implementation
must enforce caller-configurable limits for individual records/schema complexity and chunk sizes,
and must document native Zstandard workspace separately from language-level allocations. The
format provides a bounded number of records and bytes per chunk; it does not promise a fixed RSS
value across languages or Zstandard versions.

The iterator decoder reads one chunk header and payload at a time, decompresses one inner v2 frame,
reconstructs that chunk, yields its records, and releases the chunk before reading the next one. Its
working set follows the same `O(T + L + S + W(level))` model plus the caller's output queue. The
list-returning decoder may still use memory proportional to the returned list; bounded chunk decoding
and bounded total returned output are separate properties.

### Integrity, limits, and recovery hooks

Every chunk has two integrity layers:

1. The chunk-header CRC-32C protects its sequence number, counts, lengths, and flags.
2. The embedded v2 Zstandard frame provides content-checksum behavior. The v3 writer enables the
   Zstandard content checksum. A reader validates it when present and never treats a missing checksum
   as evidence of integrity; it may accept an otherwise valid chunk from another producer that omitted
   the checksum, with reduced corruption-detection coverage.

The outer-header and footer CRCs protect framing metadata and the global totals. CRC-32C is for
accidental corruption detection, not authenticity or resistance to an adversary deliberately
constructing a collision.

The iterator decoder exposes these limits:

- `max_output_size`: the aggregate number of uncompressed inner MessagePack body bytes, checked
  against the footer when available and cumulatively while reading a forward-only stream;
- `max_records`: the aggregate decoded record count, checked against the footer when available and
  before decoding any chunk that would cross the limit;
- `max_chunks`: the number of chunk records permitted;
- `max_chunk_uncompressed_bytes`: the uncompressed body limit for one chunk; and
- `max_chunk_payload_bytes` (or an equivalent total-payload limit): the bytes that may be read for
  one chunk before decompression.

An implementation may add a total compressed-input limit. These are safety limits, not wire-format
defaults; explicit violations must raise `ResourceLimitError`. Structural failures must continue to
raise `InvalidFormatError`, and unsupported version/kind extensions must not be silently accepted.

The framing supports the explicit `iter_decompress_recover` API. If a chunk header passes its CRC
and its payload length is within the input, an iterator can skip exactly that payload after a
Zstandard checksum, MessagePack, or schema validation failure. It emits a `ChunkError` with the
sequence and typed exception; the event makes the recovered output explicitly incomplete, and no
records from that chunk are returned. Successful chunks are `ChunkRecords` events. If the chunk
tag/header, length, CRC, sequence, or footer is corrupt, there is no safe resynchronization point in
the base format and the decoder stops with `InvalidFormatError`.

### Indexing and future extensions

The base version 3 format has no random-access index. Its mandatory footer contains totals but no
byte offsets, and a reader normally scans from the first chunk. This keeps the base writer
forward-only and makes chunk finalization possible on pipes and sockets. Random-access metadata is
reserved for the later roadmap item; a future indexed version should add sequence-to-offset and
row-range entries while retaining the existing chunk records.

The version 3 outer flags, chunk kind/flags, footer kind/flags, and reserved fields must be zero in
this base format. They are extension points for a future format version, but a version 3 reader must
reject non-zero or unknown values rather than assigning them new meanings. A future version can add
an index, alternate chunk payload kind, or authenticated checksums without making a base v3 reader
misinterpret the bytes.

Schema projection can be layered on the existing chunks because each chunk already names its paths
and keeps columns positionally aligned. A future decoder can inspect the local `k` paths and decode
only selected columns while still using `o` and `n` to reconstruct order. Schema evolution can add
new per-chunk schema metadata or a separate catalog in a later version; existing chunks remain
independently decodable and do not require a global schema ID table.

### Performance and determinism

Independent chunks trade compression ratio for bounded working sets and recovery granularity. Each
chunk repeats its schema metadata and starts a new Zstandard frame, so very small chunks have more
header, schema, frame, and checksum overhead and lose cross-chunk Zstandard history. Larger chunks
usually compress better but retain more column data and make corruption recovery coarser. Local
schemas are the deliberate choice for bounded memory and independent decoding; a shared global
schema/dictionary is not part of the base format.

For the same logical record sequence, compression settings, exact `target_chunk_bytes` policy, and
implementation of the v2 encoders, chunk boundaries, schema IDs, RLE order, counts, and footer
totals must be deterministic. Exact compressed bytes additionally depend on the Zstandard
implementation/version, as they do for current v2. Future benchmarks must measure peak language and
native memory separately, compression/decompression throughput, chunk and schema overhead, ratio at
several chunk targets, iterator latency, random-access cost after indexing, and the amount of data
lost or skipped during corruption recovery.

Future product decisions include writer chunk-target defaults and record-complexity limits. They do
not change the version 3 wire grammar selected here.
