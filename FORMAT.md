# JZPK format

This document describes the version 2 JZPK format. It is intended to be stable enough for other
implementations to reproduce and decode the format without depending on the Python package.

## Container

Every file or byte payload is:

```text
4 bytes: ASCII "JZPK"
1 byte:  format version
rest:    one Zstandard frame containing one MessagePack map
```

The current writer emits version `2`. Readers accept versions `1` and `2`.

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

Each version 2 schema map is:

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

The legacy version 1 representation used string keys in `k` and a map in `c`; readers may support
that representation for compatibility.

## Column encodings

Every encoded column is a map with an integer `t` encoding type:

| `t` | Encoding | Fields |
|---:|---|---|
| 0 | Raw | `d`: values |
| 1 | Run-length | `d`: `[value, count]` pairs |
| 2 | Delta | `b`: first value, `d`: deltas |
| 3 | Dictionary | `m`: dictionary, `d`: integer indices |

An implementation must reject unknown encoding types and invalid lengths or indices rather than
silently treating them as raw data.

## Schema order

`o` is a list of `[schema_id, count]` pairs. Expanding those pairs produces one schema ID per
record. The expanded length must equal the sum of the schema row counts.
