"""Sequential encoding and decoding for the JZPK version 3 container.

Each chunk embeds a complete v2 payload as an internal implementation detail,
which keeps independently decodable chunks on the established schema and column
encoding contract while exposing only version 3 to library consumers.
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

from .compressor import JZPackCompressor
from .errors import InvalidFormatError, JZPackError, ResourceLimitError, UnsupportedVersionError
from .serializer import PayloadSerializer

FileSource = str | os.PathLike[str] | BinaryIO

_OUTER_HEADER_SIZE = 16
_CHUNK_HEADER_SIZE = 56
_FOOTER_SIZE = 56
_MAX_U64 = (1 << 64) - 1
_MAX_SAFE_BUFFER = sys.maxsize
_DISCARD_BLOCK_SIZE = 64 * 1024


@dataclass(frozen=True)
class ChunkRecords:
    """A successfully decoded v3 chunk emitted by the recovery iterator."""

    sequence: int
    records: list[dict[str, Any]]


@dataclass(frozen=True)
class ChunkError:
    """A recoverable corrupt v3 chunk emitted by the recovery iterator.

    Its presence means the recovered record stream is incomplete.  No records
    from this chunk are exposed.
    """

    sequence: int
    error: JZPackError


class _BytesReader:
    """A forward-only reader over bytes-like input without copying all input."""

    def __init__(self, data: bytes | bytearray | memoryview):
        self._data = memoryview(data)
        self._position = 0

    def read(self, size: int) -> bytes:
        end = min(self._position + size, len(self._data))
        result = self._data[self._position : end].tobytes()
        self._position = end
        return result


def iter_decompress(
    source: bytes | bytearray | memoryview | FileSource,
    *,
    max_output_size: int | None = None,
    max_records: int | None = None,
    max_chunks: int | None = None,
    max_chunk_uncompressed_bytes: int | None = None,
    max_chunk_payload_bytes: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield records from a JZPK payload.

    Paths and binary streams are read one chunk at a time. Corruption fails
    fast; use :func:`iter_decompress_recover` for the explicit recoverable-
    chunk event stream. Standalone version 1 and 2 payloads are unsupported.
    """

    for event in _iter_events(
        source,
        recover=False,
        max_output_size=max_output_size,
        max_records=max_records,
        max_chunks=max_chunks,
        max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
        max_chunk_payload_bytes=max_chunk_payload_bytes,
    ):
        if isinstance(event, ChunkRecords):
            yield from event.records


def iter_decompress_recover(
    source: bytes | bytearray | memoryview | FileSource,
    *,
    max_output_size: int | None = None,
    max_records: int | None = None,
    max_chunks: int | None = None,
    max_chunk_uncompressed_bytes: int | None = None,
    max_chunk_payload_bytes: int | None = None,
) -> Iterator[ChunkRecords | ChunkError]:
    """Yield per-chunk results while recovering from safely skippable corruption.

    A chunk with valid framing but an invalid embedded v2 payload becomes a
    :class:`ChunkError`; later chunks remain available.  Header, length, CRC,
    sequence, footer, and resource-limit failures are not recoverable because
    this base format has no safe resynchronization point.
    """

    yield from _iter_events(
        source,
        recover=True,
        max_output_size=max_output_size,
        max_records=max_records,
        max_chunks=max_chunks,
        max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
        max_chunk_payload_bytes=max_chunk_payload_bytes,
    )


def iter_v3_decompress(
    source: bytes | bytearray | memoryview,
    *,
    max_output_size: int | None = None,
    max_records: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Internal list-adapter entry point used by ``JZPackCompressor.decompress``."""

    yield from iter_decompress(source, max_output_size=max_output_size, max_records=max_records)


def serialize_v3_container(
    inner_payload: bytes, *, record_count: int, uncompressed_body_bytes: int
) -> bytes:
    """Wrap one complete inner v2 payload in the sole public JZPK container."""

    _validate_writer_count("record_count", record_count)
    _validate_writer_count("uncompressed_body_bytes", uncompressed_body_bytes)
    if not isinstance(inner_payload, bytes):
        raise TypeError("inner_payload must be bytes")
    if record_count == 0:
        return _build_outer_header() + _build_footer(_Totals())
    if len(inner_payload) < PayloadSerializer.HEADER_SIZE:
        raise ValueError("inner_payload must include a complete JZPK header")
    _validate_inner_header(inner_payload[: PayloadSerializer.HEADER_SIZE])

    frame_bytes = len(inner_payload) - PayloadSerializer.HEADER_SIZE
    header = _ChunkHeader(
        sequence=0,
        record_count=record_count,
        uncompressed_bytes=uncompressed_body_bytes,
        compressed_frame_bytes=frame_bytes,
        payload_bytes=len(inner_payload),
    )
    totals = _Totals()
    totals.add(header)
    return _build_outer_header() + _build_chunk_header(header) + inner_payload + _build_footer(totals)


def _iter_events(
    source: bytes | bytearray | memoryview | FileSource,
    *,
    recover: bool,
    max_output_size: int | None,
    max_records: int | None,
    max_chunks: int | None,
    max_chunk_uncompressed_bytes: int | None,
    max_chunk_payload_bytes: int | None,
) -> Iterator[ChunkRecords | ChunkError]:
    limits = _validate_limits(
        max_output_size=max_output_size,
        max_records=max_records,
        max_chunks=max_chunks,
        max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
        max_chunk_payload_bytes=max_chunk_payload_bytes,
    )

    if isinstance(source, (bytes, bytearray, memoryview)):
        yield from _decode_source(_BytesReader(source), recover=recover, **limits)
        return

    path = _coerce_path(source)
    if path is not None:
        with open(path, "rb") as stream:
            yield from _decode_source(stream, recover=recover, **limits)
        return

    _validate_binary_stream(source)
    yield from _decode_source(source, recover=recover, **limits)


def _decode_source(
    stream: BinaryIO | _BytesReader,
    *,
    recover: bool,
    max_output_size: int | None,
    max_records: int | None,
    max_chunks: int | None,
    max_chunk_uncompressed_bytes: int | None,
    max_chunk_payload_bytes: int | None,
) -> Iterator[ChunkRecords | ChunkError]:
    first_header = _read_exact(stream, 5, "header")
    if first_header[:4] != PayloadSerializer.MAGIC:
        raise InvalidFormatError("Invalid file format: missing magic header")

    if first_header[4] != 3:
        raise UnsupportedVersionError(f"Unsupported version: {first_header[4]}")

    outer_header = first_header + _read_exact(stream, _OUTER_HEADER_SIZE - len(first_header), "outer header")
    _validate_outer_header(outer_header)
    yield from _decode_v3_chunks(
        stream,
        recover=recover,
        max_output_size=max_output_size,
        max_records=max_records,
        max_chunks=max_chunks,
        max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
        max_chunk_payload_bytes=max_chunk_payload_bytes,
    )


def _decode_v3_chunks(
    stream: BinaryIO | _BytesReader,
    *,
    recover: bool,
    max_output_size: int | None,
    max_records: int | None,
    max_chunks: int | None,
    max_chunk_uncompressed_bytes: int | None,
    max_chunk_payload_bytes: int | None,
) -> Iterator[ChunkRecords | ChunkError]:
    expected_sequence = 0
    totals = _Totals()
    serializer = PayloadSerializer()
    reconstructor = JZPackCompressor()

    while True:
        tag = _read_exact(stream, 4, "chunk header or footer")
        if tag == b"JZPF":
            footer = tag + _read_exact(stream, _FOOTER_SIZE - len(tag), "footer")
            _validate_footer(footer, totals)
            if _read_some(stream, 1):
                raise InvalidFormatError("Invalid JZPK v3 container: trailing bytes after footer")
            return
        if tag != b"CHNK":
            raise InvalidFormatError("Invalid JZPK v3 container: expected chunk header or footer")

        header_bytes = tag + _read_exact(stream, _CHUNK_HEADER_SIZE - len(tag), "chunk header")
        header = _parse_chunk_header(header_bytes, expected_sequence)
        _enforce_chunk_limits(
            header,
            totals,
            max_output_size=max_output_size,
            max_records=max_records,
            max_chunks=max_chunks,
            max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
            max_chunk_payload_bytes=max_chunk_payload_bytes,
        )

        payload = _read_chunk_payload(stream, header, recover=recover)
        if isinstance(payload, ChunkError):
            totals.add(header)
            expected_sequence += 1
            yield payload
            continue

        try:
            records = _decode_chunk(serializer, reconstructor, header, payload)
        except ResourceLimitError:
            raise
        except JZPackError as exc:
            if not recover:
                raise
            totals.add(header)
            expected_sequence += 1
            yield ChunkError(header.sequence, exc)
            continue

        totals.add(header)
        expected_sequence += 1
        yield ChunkRecords(header.sequence, records)


@dataclass(frozen=True)
class _ChunkHeader:
    sequence: int
    record_count: int
    uncompressed_bytes: int
    compressed_frame_bytes: int
    payload_bytes: int


@dataclass
class _Totals:
    record_count: int = 0
    uncompressed_bytes: int = 0
    compressed_frame_bytes: int = 0
    payload_bytes: int = 0
    chunk_count: int = 0

    def add(self, header: _ChunkHeader) -> None:
        self.record_count = _checked_add(self.record_count, header.record_count)
        self.uncompressed_bytes = _checked_add(self.uncompressed_bytes, header.uncompressed_bytes)
        self.compressed_frame_bytes = _checked_add(self.compressed_frame_bytes, header.compressed_frame_bytes)
        self.payload_bytes = _checked_add(self.payload_bytes, header.payload_bytes)
        self.chunk_count = _checked_add(self.chunk_count, 1)


def _read_chunk_payload(
    stream: BinaryIO | _BytesReader, header: _ChunkHeader, *, recover: bool
) -> bytes | ChunkError:
    if not recover:
        return _read_exact(stream, header.payload_bytes, "chunk payload")

    inner_header = _read_exact(stream, PayloadSerializer.HEADER_SIZE, "chunk payload")
    try:
        _validate_inner_header(inner_header)
    except JZPackError as exc:
        _discard_exact(stream, header.payload_bytes - PayloadSerializer.HEADER_SIZE, "chunk payload")
        return ChunkError(header.sequence, exc)
    return inner_header + _read_exact(
        stream, header.payload_bytes - PayloadSerializer.HEADER_SIZE, "chunk payload"
    )


def _decode_chunk(
    serializer: PayloadSerializer,
    reconstructor: JZPackCompressor,
    header: _ChunkHeader,
    payload: bytes,
) -> list[dict[str, Any]]:
    _validate_inner_header(payload[: PayloadSerializer.HEADER_SIZE])
    try:
        payload_data, body = serializer.deserialize_with_body(payload, max_output_size=header.uncompressed_bytes)
    except ResourceLimitError as exc:
        raise InvalidFormatError("Invalid JZPK v3 chunk: uncompressed body length mismatch") from exc
    except JZPackError:
        raise
    except Exception as exc:
        raise InvalidFormatError("Invalid JZPK v3 chunk payload") from exc
    if len(body) != header.uncompressed_bytes:
        raise InvalidFormatError("Invalid JZPK v3 chunk: uncompressed body length mismatch")

    try:
        records = reconstructor._reconstruct(payload_data, max_records=header.record_count)
    except ResourceLimitError as exc:
        raise InvalidFormatError("Invalid JZPK v3 chunk: decoded rows exceed chunk row count") from exc
    except Exception as exc:
        raise InvalidFormatError("Invalid JZPK v3 chunk payload") from exc

    if len(records) != header.record_count:
        raise InvalidFormatError("Invalid JZPK v3 chunk: row count mismatch")
    return records


def _validate_outer_header(header: bytes) -> None:
    if len(header) != _OUTER_HEADER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: truncated outer header")
    if header[:4] != PayloadSerializer.MAGIC or header[4] != 3:
        raise InvalidFormatError("Invalid JZPK v3 container: invalid outer header")
    if header[5] != 0 or _u16(header, 6) != _OUTER_HEADER_SIZE or _u32(header, 8) != 0:
        raise InvalidFormatError("Invalid JZPK v3 container: invalid outer header fields")
    if _u32(header, 12) != _crc32c(header[:12]):
        raise InvalidFormatError("Invalid JZPK v3 container: outer header CRC mismatch")


def _parse_chunk_header(header: bytes, expected_sequence: int) -> _ChunkHeader:
    if len(header) != _CHUNK_HEADER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: truncated chunk header")
    if _u32(header, 52) != _crc32c(header[:52]):
        raise InvalidFormatError("Invalid JZPK v3 container: chunk header CRC mismatch")
    if header[:4] != b"CHNK" or header[4] != 0 or header[5] != 0 or _u16(header, 6) != _CHUNK_HEADER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: invalid chunk header fields")
    if _u32(header, 48) != 0:
        raise InvalidFormatError("Invalid JZPK v3 container: non-zero chunk reserved field")

    chunk = _ChunkHeader(
        sequence=_u64(header, 8),
        record_count=_u64(header, 16),
        uncompressed_bytes=_u64(header, 24),
        compressed_frame_bytes=_u64(header, 32),
        payload_bytes=_u64(header, 40),
    )
    if chunk.sequence != expected_sequence:
        raise InvalidFormatError("Invalid JZPK v3 container: chunk sequence mismatch")
    if chunk.record_count == 0:
        raise InvalidFormatError("Invalid JZPK v3 container: zero-record chunk")
    if chunk.payload_bytes < PayloadSerializer.HEADER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: chunk payload is too short")
    if chunk.payload_bytes != PayloadSerializer.HEADER_SIZE + chunk.compressed_frame_bytes:
        raise InvalidFormatError("Invalid JZPK v3 container: chunk payload length mismatch")
    return chunk


def _validate_footer(footer: bytes, totals: _Totals) -> None:
    if len(footer) != _FOOTER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: truncated footer")
    if _u32(footer, 52) != _crc32c(footer[:52]):
        raise InvalidFormatError("Invalid JZPK v3 container: footer CRC mismatch")
    if footer[:4] != b"JZPF" or footer[4] != 0 or footer[5] != 0 or _u16(footer, 6) != _FOOTER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 container: invalid footer fields")
    if _u32(footer, 48) != 0:
        raise InvalidFormatError("Invalid JZPK v3 container: non-zero footer reserved field")

    expected = (
        totals.record_count,
        totals.uncompressed_bytes,
        totals.compressed_frame_bytes,
        totals.payload_bytes,
        totals.chunk_count,
    )
    actual = tuple(_u64(footer, offset) for offset in (8, 16, 24, 32, 40))
    if actual != expected:
        raise InvalidFormatError("Invalid JZPK v3 container: footer totals mismatch")


def _validate_inner_header(header: bytes) -> None:
    if len(header) != PayloadSerializer.HEADER_SIZE:
        raise InvalidFormatError("Invalid JZPK v3 chunk: truncated inner payload header")
    if header[:4] != PayloadSerializer.MAGIC:
        raise InvalidFormatError("Invalid JZPK v3 chunk: missing inner JZPK header")
    if header[4] == PayloadSerializer.INNER_VERSION:
        return
    raise InvalidFormatError("Invalid JZPK v3 chunk: inner payload must use version 2")


def _enforce_chunk_limits(
    header: _ChunkHeader,
    totals: _Totals,
    *,
    max_output_size: int | None,
    max_records: int | None,
    max_chunks: int | None,
    max_chunk_uncompressed_bytes: int | None,
    max_chunk_payload_bytes: int | None,
) -> None:
    prospective_output = _checked_add(totals.uncompressed_bytes, header.uncompressed_bytes)
    prospective_records = _checked_add(totals.record_count, header.record_count)
    prospective_chunks = _checked_add(totals.chunk_count, 1)
    if max_output_size is not None and prospective_output > max_output_size:
        raise ResourceLimitError("JZPK v3 container exceeds max_output_size")
    if max_records is not None and prospective_records > max_records:
        raise ResourceLimitError("JZPK v3 container exceeds max_records")
    if max_chunks is not None and prospective_chunks > max_chunks:
        raise ResourceLimitError("JZPK v3 container exceeds max_chunks")
    if max_chunk_uncompressed_bytes is not None and header.uncompressed_bytes > max_chunk_uncompressed_bytes:
        raise ResourceLimitError("JZPK v3 chunk exceeds max_chunk_uncompressed_bytes")
    if max_chunk_payload_bytes is not None and header.payload_bytes > max_chunk_payload_bytes:
        raise ResourceLimitError("JZPK v3 chunk exceeds max_chunk_payload_bytes")
    if header.uncompressed_bytes > _MAX_SAFE_BUFFER:
        raise ResourceLimitError("JZPK v3 chunk exceeds this platform's maximum buffer size")
    if header.payload_bytes > _MAX_SAFE_BUFFER:
        raise ResourceLimitError("JZPK v3 chunk exceeds this platform's maximum buffer size")


def _validate_limits(**limits: int | None) -> dict[str, int | None]:
    for name, value in limits.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{name} must be a non-negative integer")
    return limits


def _coerce_path(source: FileSource) -> str | None:
    if not isinstance(source, (str, os.PathLike)):
        return None
    path = os.fspath(source)
    if not isinstance(path, str):
        raise TypeError("source path must be a str or os.PathLike[str]")
    return path


def _validate_binary_stream(stream: object) -> None:
    if isinstance(stream, io.TextIOBase) or not callable(getattr(stream, "read", None)):
        raise TypeError("source must be bytes-like, a str, os.PathLike[str], or binary readable file-like object")


def _read_exact(stream: BinaryIO | _BytesReader, size: int, context: str) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = _read_some(stream, size - len(result))
        if not chunk:
            raise InvalidFormatError(f"Invalid JZPK v3 container: truncated {context}")
        if len(chunk) > size - len(result):
            raise InvalidFormatError("Invalid binary stream: read returned more data than requested")
        result.extend(chunk)
    return bytes(result)


def _discard_exact(stream: BinaryIO | _BytesReader, size: int, context: str) -> None:
    remaining = size
    while remaining:
        chunk = _read_some(stream, min(remaining, _DISCARD_BLOCK_SIZE))
        if not chunk:
            raise InvalidFormatError(f"Invalid JZPK v3 container: truncated {context}")
        if len(chunk) > remaining:
            raise InvalidFormatError("Invalid binary stream: read returned more data than requested")
        remaining -= len(chunk)


def _read_some(stream: BinaryIO | _BytesReader, size: int) -> bytes:
    try:
        value = stream.read(size)
    except (OSError, TypeError) as exc:
        raise InvalidFormatError("Invalid binary source stream") from exc
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("source must be a binary readable file-like object")
    return bytes(value)


def _checked_add(left: int, right: int) -> int:
    if left > _MAX_U64 - right:
        raise InvalidFormatError("Invalid JZPK v3 container: count total overflow")
    return left + right


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big")


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _build_outer_header() -> bytes:
    header = PayloadSerializer.MAGIC + b"\x03\x00" + _OUTER_HEADER_SIZE.to_bytes(2, "big") + b"\x00" * 4
    return header + _crc32c(header).to_bytes(4, "big")


def _build_chunk_header(header: _ChunkHeader) -> bytes:
    prefix = b"CHNK\x00\x00" + _CHUNK_HEADER_SIZE.to_bytes(2, "big")
    prefix += b"".join(
        value.to_bytes(8, "big")
        for value in (
            header.sequence,
            header.record_count,
            header.uncompressed_bytes,
            header.compressed_frame_bytes,
            header.payload_bytes,
        )
    )
    prefix += b"\x00" * 4
    return prefix + _crc32c(prefix).to_bytes(4, "big")


def _build_footer(totals: _Totals) -> bytes:
    prefix = b"JZPF\x00\x00" + _FOOTER_SIZE.to_bytes(2, "big")
    prefix += b"".join(
        value.to_bytes(8, "big")
        for value in (
            totals.record_count,
            totals.uncompressed_bytes,
            totals.compressed_frame_bytes,
            totals.payload_bytes,
            totals.chunk_count,
        )
    )
    prefix += b"\x00" * 4
    return prefix + _crc32c(prefix).to_bytes(4, "big")


def _validate_writer_count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_U64:
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
