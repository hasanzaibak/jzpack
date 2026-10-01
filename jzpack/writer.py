"""Bounded, direct-to-sink writing for JZPK version 3 containers."""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, BinaryIO

from .chunks import (
    _MAX_U64,
    _build_chunk_header,
    _build_footer,
    _build_outer_header,
    _ChunkHeader,
    _Totals,
    _write_all,
)
from .compressor import JZPackCompressor
from .errors import ResourceLimitError
from .schema import Path

_DEFAULT_TARGET_CHUNK_INPUT_BYTES = 1_048_576
_DEFAULT_MAX_CHUNK_RECORDS = 4_096
_DEFAULT_MAX_RECORD_BYTES = 1_048_576
_DEFAULT_MAX_CHUNK_INPUT_BYTES = 4_194_304
_DEFAULT_MAX_CHUNK_UNCOMPRESSED_BYTES = 8_388_608
_DEFAULT_MAX_CHUNK_PAYLOAD_BYTES = 8_388_608
_DEFAULT_MAX_SCHEMAS = 256
_DEFAULT_MAX_PATHS = 8_192
_DEFAULT_MAX_PATH_BYTES = 1_048_576
_DEFAULT_MAX_DEPTH = 64
_DEFAULT_MAX_NODES = 65_536
_MAX_CONFIGURED_DEPTH = 128
_MESSAGEPACK_INT_MIN = -(1 << 63)
_MESSAGEPACK_INT_MAX = (1 << 64) - 1

_WriteSink = str | os.PathLike[str] | BinaryIO


@dataclass(frozen=True)
class _WriterLimits:
    target_chunk_input_bytes: int
    max_chunk_records: int
    max_record_bytes: int
    max_chunk_input_bytes: int
    max_chunk_uncompressed_bytes: int
    max_chunk_payload_bytes: int
    max_schemas: int
    max_paths: int
    max_path_bytes: int
    max_depth: int
    max_nodes: int


@dataclass(frozen=True)
class _RecordMeasure:
    serialized_bytes: int
    node_count: int
    path_count: int
    path_bytes: int


@dataclass(frozen=True)
class _PreparedRecord:
    snapshot: dict[str, Any]
    signature: tuple[Path, ...]
    measure: _RecordMeasure


class _RecordInspector:
    """Validate one built-in record and measure its exact input MessagePack size."""

    def __init__(self, limits: _WriterLimits):
        self._limits = limits
        self._serialized_bytes = 0
        self._node_count = 0
        self._path_count = 0
        self._path_bytes = 0
        self._active_containers: set[int] = set()

    def inspect(self, record: object) -> _RecordMeasure:
        if type(record) is not dict:
            raise TypeError("write_records accepts only built-in dict records")
        self._measure_mapping(record, depth=1, path_bytes=0, schema_mapping=True, path_present=False)
        return _RecordMeasure(
            serialized_bytes=self._serialized_bytes,
            node_count=self._node_count,
            path_count=self._path_count,
            path_bytes=self._path_bytes,
        )

    def _add_bytes(self, value: int) -> None:
        if value > self._limits.max_record_bytes - self._serialized_bytes:
            raise ResourceLimitError("record exceeds max_record_bytes")
        self._serialized_bytes += value

    def _add_node(self) -> None:
        if self._node_count >= self._limits.max_nodes:
            raise ResourceLimitError("record exceeds max_nodes")
        self._node_count += 1

    def _add_schema_path(self, path_bytes: int) -> None:
        if self._path_count >= self._limits.max_paths:
            raise ResourceLimitError("record exceeds max_paths")
        if path_bytes > self._limits.max_path_bytes - self._path_bytes:
            raise ResourceLimitError("record exceeds max_path_bytes")
        self._path_count += 1
        self._path_bytes += path_bytes

    def _check_depth(self, depth: int) -> None:
        if depth > self._limits.max_depth:
            raise ResourceLimitError("record exceeds max_depth")

    def _container_enter(self, value: dict[Any, Any] | list[Any], depth: int) -> int:
        self._check_depth(depth)
        identity = id(value)
        if identity in self._active_containers:
            raise ValueError("write_records does not accept cyclic records")
        self._active_containers.add(identity)
        return identity

    def _container_exit(self, identity: int) -> None:
        self._active_containers.remove(identity)

    def _measure_mapping(
        self,
        value: dict[Any, Any],
        *,
        depth: int,
        path_bytes: int,
        schema_mapping: bool,
        path_present: bool,
    ) -> None:
        self._add_node()
        identity = self._container_enter(value, depth)
        try:
            self._add_bytes(_map_header_size(len(value)))
            if len(value) * 2 > self._limits.max_nodes - self._node_count:
                raise ResourceLimitError("record exceeds max_nodes")
            if len(value) * 2 > self._limits.max_record_bytes - self._serialized_bytes:
                raise ResourceLimitError("record exceeds max_record_bytes")
            if not value and schema_mapping and path_present:
                self._add_schema_path(path_bytes)

            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("write_records requires built-in string mapping keys")
                self._add_node()
                key_utf8_bytes = _string_utf8_size(
                    key, self._limits.max_record_bytes - self._serialized_bytes
                )
                key_bytes = _string_header_size(key_utf8_bytes) + key_utf8_bytes
                self._add_bytes(key_bytes)
                child_path_bytes = path_bytes + key_utf8_bytes if schema_mapping else 0

                if schema_mapping:
                    if type(item) is dict:
                        self._measure_mapping(
                            item,
                            depth=depth + 1,
                            path_bytes=child_path_bytes,
                            schema_mapping=True,
                            path_present=True,
                        )
                    else:
                        self._add_schema_path(child_path_bytes)
                        self._measure_value(item, depth=depth + 1)
                else:
                    self._measure_value(item, depth=depth + 1)
        finally:
            self._container_exit(identity)

    def _measure_value(self, value: object, *, depth: int) -> None:
        value_type = type(value)
        if value_type is dict:
            self._add_node()
            identity = self._container_enter(value, depth)  # type: ignore[arg-type]
            try:
                mapping = value  # type: ignore[assignment]
                self._add_bytes(_map_header_size(len(mapping)))
                if len(mapping) * 2 > self._limits.max_nodes - self._node_count:
                    raise ResourceLimitError("record exceeds max_nodes")
                if len(mapping) * 2 > self._limits.max_record_bytes - self._serialized_bytes:
                    raise ResourceLimitError("record exceeds max_record_bytes")
                for key, item in mapping.items():
                    if type(key) is not str:
                        raise TypeError("write_records requires built-in string mapping keys")
                    self._add_node()
                    key_utf8_bytes = _string_utf8_size(
                        key, self._limits.max_record_bytes - self._serialized_bytes
                    )
                    self._add_bytes(_string_header_size(key_utf8_bytes) + key_utf8_bytes)
                    self._measure_value(item, depth=depth + 1)
            finally:
                self._container_exit(identity)
            return

        if value_type is list:
            self._add_node()
            identity = self._container_enter(value, depth)  # type: ignore[arg-type]
            try:
                sequence = value  # type: ignore[assignment]
                self._add_bytes(_array_header_size(len(sequence)))
                if len(sequence) > self._limits.max_nodes - self._node_count:
                    raise ResourceLimitError("record exceeds max_nodes")
                if len(sequence) > self._limits.max_record_bytes - self._serialized_bytes:
                    raise ResourceLimitError("record exceeds max_record_bytes")
                for item in sequence:
                    self._measure_value(item, depth=depth + 1)
            finally:
                self._container_exit(identity)
            return

        self._add_node()
        self._add_bytes(_scalar_size(value, self._limits.max_record_bytes - self._serialized_bytes))


class _ChunkState:
    def __init__(self, compression_level: int, fast: bool):
        self.compressor = JZPackCompressor(compression_level=compression_level, fast=fast)
        self.record_count = 0
        self.input_bytes = 0
        self.node_count = 0
        self.schemas: dict[tuple[Path, ...], str] = {}
        self.path_count = 0
        self.path_bytes = 0

    def add(self, record: _PreparedRecord) -> None:
        existing_id = self.schemas.get(record.signature)
        schema_id = self.compressor._schema_manager.add_record(record.snapshot)
        if existing_id is None:
            expected_id = f"s{len(self.schemas)}"
            if schema_id != expected_id:
                raise RuntimeError("schema manager produced a non-sequential chunk schema ID")
            self.schemas[record.signature] = schema_id
            self.path_count += record.measure.path_count
            self.path_bytes += record.measure.path_bytes
        elif schema_id != existing_id:
            raise RuntimeError("schema manager assigned a different ID to a known schema")
        self.record_count += 1
        self.input_bytes += record.measure.serialized_bytes
        self.node_count += record.measure.node_count

    def encode(self, limits: _WriterLimits) -> tuple[_ChunkHeader, bytes]:
        payload = self.compressor._build_payload()
        body_size = _messagepack_size(payload, limits.max_chunk_uncompressed_bytes, limits.max_depth + 16)
        inner_payload, body = self.compressor._serializer.serialize_with_body(payload)
        if len(body) != body_size:
            raise RuntimeError("internal MessagePack size calculation disagrees with msgpack")
        if len(inner_payload) > limits.max_chunk_payload_bytes:
            raise ResourceLimitError("chunk exceeds max_chunk_payload_bytes")

        frame_bytes = len(inner_payload) - 5
        return (
            _ChunkHeader(
                sequence=0,
                record_count=self.record_count,
                uncompressed_bytes=len(body),
                compressed_frame_bytes=frame_bytes,
                payload_bytes=len(inner_payload),
            ),
            inner_payload,
        )


def write_records(
    records: Iterable[dict[str, Any]] | dict[str, Any],
    sink: _WriteSink,
    *,
    target_chunk_input_bytes: int = _DEFAULT_TARGET_CHUNK_INPUT_BYTES,
    max_chunk_records: int = _DEFAULT_MAX_CHUNK_RECORDS,
    max_record_bytes: int = _DEFAULT_MAX_RECORD_BYTES,
    max_chunk_input_bytes: int = _DEFAULT_MAX_CHUNK_INPUT_BYTES,
    max_chunk_uncompressed_bytes: int = _DEFAULT_MAX_CHUNK_UNCOMPRESSED_BYTES,
    max_chunk_payload_bytes: int = _DEFAULT_MAX_CHUNK_PAYLOAD_BYTES,
    max_schemas: int = _DEFAULT_MAX_SCHEMAS,
    max_paths: int = _DEFAULT_MAX_PATHS,
    max_path_bytes: int = _DEFAULT_MAX_PATH_BYTES,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_nodes: int = _DEFAULT_MAX_NODES,
    compression_level: int = 3,
    fast: bool = False,
) -> int:
    """Write supported records as deterministic v3 chunks to a stream or atomically to a path.

    The input-byte target and per-chunk schema/path/node/row ceilings trigger a
    chunk boundary before a valid next record would cross them. Per-record and
    encoded body/payload ceilings are hard limits. A record may exceed the soft
    target by itself, but never its per-record or hard chunk-input limit.
    """
    limits = _validate_limits(
        target_chunk_input_bytes=target_chunk_input_bytes,
        max_chunk_records=max_chunk_records,
        max_record_bytes=max_record_bytes,
        max_chunk_input_bytes=max_chunk_input_bytes,
        max_chunk_uncompressed_bytes=max_chunk_uncompressed_bytes,
        max_chunk_payload_bytes=max_chunk_payload_bytes,
        max_schemas=max_schemas,
        max_paths=max_paths,
        max_path_bytes=max_path_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if target_chunk_input_bytes > max_chunk_input_bytes:
        raise ValueError("target_chunk_input_bytes cannot exceed max_chunk_input_bytes")
    if isinstance(compression_level, bool) or not isinstance(compression_level, int) or not 1 <= compression_level <= 22:
        raise ValueError("compression_level must be an integer from 1 to 22")
    if type(fast) is not bool:
        raise TypeError("fast must be a bool")

    record_iterator = _records_iterator(records)
    destination_path = _coerce_destination(sink)
    if destination_path is not None:
        return _write_atomically(record_iterator, destination_path, limits, compression_level, fast)
    _validate_sink(sink)
    return _write_stream(record_iterator, sink, limits, compression_level, fast)


def _write_stream(
    records: Iterable[object],
    sink: BinaryIO,
    limits: _WriterLimits,
    compression_level: int,
    fast: bool,
) -> int:
    total_written = _write_all(sink, _build_outer_header())
    totals = _Totals()
    chunk = _ChunkState(compression_level, fast)

    for record in records:
        measure = _RecordInspector(limits).inspect(record)
        if measure.serialized_bytes > limits.max_record_bytes:
            raise ResourceLimitError("record exceeds max_record_bytes")
        if measure.serialized_bytes > limits.max_chunk_input_bytes:
            raise ResourceLimitError("record exceeds max_chunk_input_bytes")
        prepared = _prepare_record(record, measure, limits.max_depth)

        if _must_flush(chunk, prepared, limits):
            total_written += _flush_chunk(chunk, sink, totals, limits)
            chunk = _ChunkState(compression_level, fast)

        _validate_singleton(prepared, limits)
        chunk.add(prepared)
        del prepared

    if chunk.record_count:
        total_written += _flush_chunk(chunk, sink, totals, limits)
    total_written += _write_all(sink, _build_footer(totals))
    return total_written


def _flush_chunk(
    chunk: _ChunkState,
    sink: BinaryIO,
    totals: _Totals,
    limits: _WriterLimits,
) -> int:
    header, inner_payload = chunk.encode(limits)
    header = _ChunkHeader(
        sequence=totals.chunk_count,
        record_count=header.record_count,
        uncompressed_bytes=header.uncompressed_bytes,
        compressed_frame_bytes=header.compressed_frame_bytes,
        payload_bytes=header.payload_bytes,
    )
    _check_total_overflow(totals, header)
    written = _write_all(sink, _build_chunk_header(header))
    written += _write_all(sink, inner_payload)
    totals.add(header)
    return written


def _write_atomically(
    records: Iterable[object],
    destination: str,
    limits: _WriterLimits,
    compression_level: int,
    fast: bool,
) -> int:
    parent = os.path.dirname(os.path.abspath(destination))
    descriptor, temporary = tempfile.mkstemp(prefix=".jzpack-", suffix=".tmp", dir=parent)
    try:
        try:
            stream = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            written = _write_stream(records, stream, limits, compression_level, fast)
            _flush_stream(stream)
            _sync_stream(stream)
        os.replace(temporary, destination)
        temporary = ""
        return written
    finally:
        if temporary:
            with suppress(OSError):
                os.unlink(temporary)


def _flush_stream(stream: BinaryIO) -> None:
    stream.flush()


def _sync_stream(stream: BinaryIO) -> None:
    os.fsync(stream.fileno())


def _records_iterator(records: object) -> Iterable[object]:
    if isinstance(records, Mapping):
        if type(records) is not dict:
            raise TypeError("write_records accepts only built-in dict records")
        return iter((records,))
    try:
        return iter(records)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("records must be a built-in dict or an iterable of built-in dict records") from exc


def _coerce_destination(sink: object) -> str | None:
    if not isinstance(sink, (str, os.PathLike)):
        return None
    path = os.fspath(sink)
    if not isinstance(path, str):
        raise TypeError("sink path must be a str or os.PathLike[str]")
    return path


def _validate_sink(sink: object) -> None:
    if isinstance(sink, io.TextIOBase) or not callable(getattr(sink, "write", None)):
        raise TypeError("sink must be a path or binary file-like object with write(bytes)")


def _validate_limits(**values: int) -> _WriterLimits:
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if values["max_depth"] > _MAX_CONFIGURED_DEPTH:
        raise ValueError(f"max_depth cannot exceed {_MAX_CONFIGURED_DEPTH}")
    return _WriterLimits(**values)


def _must_flush(chunk: _ChunkState, record: _PreparedRecord, limits: _WriterLimits) -> bool:
    if not chunk.record_count:
        return False
    if chunk.record_count + 1 > limits.max_chunk_records:
        return True
    if chunk.input_bytes + record.measure.serialized_bytes > limits.target_chunk_input_bytes:
        return True
    if chunk.input_bytes + record.measure.serialized_bytes > limits.max_chunk_input_bytes:
        return True
    if chunk.node_count + record.measure.node_count > limits.max_nodes:
        return True
    if record.signature in chunk.schemas:
        return False
    if len(chunk.schemas) + 1 > limits.max_schemas:
        return True
    if chunk.path_count + record.measure.path_count > limits.max_paths:
        return True
    if chunk.path_bytes + record.measure.path_bytes > limits.max_path_bytes:
        return True
    return False


def _validate_singleton(record: _PreparedRecord, limits: _WriterLimits) -> None:
    if record.measure.serialized_bytes > limits.max_record_bytes:
        raise ResourceLimitError("record exceeds max_record_bytes")
    if record.measure.serialized_bytes > limits.max_chunk_input_bytes:
        raise ResourceLimitError("record exceeds max_chunk_input_bytes")
    if record.measure.node_count > limits.max_nodes:
        raise ResourceLimitError("record exceeds max_nodes")
    if len(record.signature) > limits.max_paths:
        raise ResourceLimitError("record exceeds max_paths")
    if record.measure.path_bytes > limits.max_path_bytes:
        raise ResourceLimitError("record exceeds max_path_bytes")


def _prepare_record(record: object, measure: _RecordMeasure, max_depth: int) -> _PreparedRecord:
    if type(record) is not dict:
        raise TypeError("write_records accepts only built-in dict records")
    paths: set[Path] = set()
    snapshot = _snapshot_value(record, paths, prefix=(), depth=1, schema_mapping=True, max_depth=max_depth)
    signature = tuple(sorted(paths))
    if len(signature) != measure.path_count:
        raise RuntimeError("record path measurement disagrees with its defensive snapshot")
    return _PreparedRecord(snapshot, signature, measure)


def _snapshot_value(
    value: object,
    paths: set[Path],
    *,
    prefix: Path,
    depth: int,
    schema_mapping: bool,
    max_depth: int,
    active: set[int] | None = None,
) -> Any:
    if active is None:
        active = set()
    value_type = type(value)
    if value_type not in (dict, list):
        return value
    if depth > max_depth:
        raise ResourceLimitError("record exceeds max_depth")
    identity = id(value)
    if identity in active:
        raise ValueError("write_records does not accept cyclic records")
    active.add(identity)
    try:
        if value_type is list:
            return [
                _snapshot_value(
                    item,
                    paths,
                    prefix=(),
                    depth=depth + 1,
                    schema_mapping=False,
                    max_depth=max_depth,
                    active=active,
                )
                for item in value  # type: ignore[union-attr]
            ]

        result: dict[str, Any] = {}
        mapping = value  # type: ignore[assignment]
        if schema_mapping and not mapping and prefix:
            paths.add(prefix)
        for key, item in mapping.items():
            child_path = prefix + (key,) if schema_mapping else ()
            if schema_mapping and type(item) is dict:
                result[key] = _snapshot_value(
                    item,
                    paths,
                    prefix=child_path,
                    depth=depth + 1,
                    schema_mapping=True,
                    max_depth=max_depth,
                    active=active,
                )
            else:
                if schema_mapping:
                    paths.add(child_path)
                result[key] = _snapshot_value(
                    item,
                    paths,
                    prefix=(),
                    depth=depth + 1,
                    schema_mapping=False,
                    max_depth=max_depth,
                    active=active,
                )
        return result
    finally:
        active.remove(identity)


def _check_total_overflow(totals: _Totals, header: _ChunkHeader) -> None:
    values = (
        (totals.record_count, header.record_count),
        (totals.uncompressed_bytes, header.uncompressed_bytes),
        (totals.compressed_frame_bytes, header.compressed_frame_bytes),
        (totals.payload_bytes, header.payload_bytes),
        (totals.chunk_count, 1),
    )
    if any(left > _MAX_U64 - right for left, right in values):
        raise ResourceLimitError("archive totals exceed the version 3 unsigned 64-bit range")


def _messagepack_size(value: object, max_bytes: int, max_depth: int) -> int:
    total = 0

    def add(value_bytes: int) -> None:
        nonlocal total
        if value_bytes > max_bytes - total:
            raise ResourceLimitError("chunk exceeds max_chunk_uncompressed_bytes")
        total += value_bytes

    def add_scalar(item: object) -> None:
        try:
            scalar_bytes = _scalar_size(item, max_bytes - total)
        except ResourceLimitError as exc:
            raise ResourceLimitError("chunk exceeds max_chunk_uncompressed_bytes") from exc
        add(scalar_bytes)

    def visit(item: object, depth: int) -> None:
        item_type = type(item)
        if item_type is dict:
            if depth > max_depth:
                raise ResourceLimitError("encoded payload exceeds bounded serialization depth")
            add(_map_header_size(len(item)))
            for key, nested in item.items():
                visit(key, depth + 1)
                visit(nested, depth + 1)
            return
        if item_type is list:
            if depth > max_depth:
                raise ResourceLimitError("encoded payload exceeds bounded serialization depth")
            add(_array_header_size(len(item)))
            for nested in item:
                visit(nested, depth + 1)
            return
        if isinstance(item, int) and not isinstance(item, bool) and item_type is not int:
            add_scalar(int(item))
            return
        add_scalar(item)

    visit(value, 0)
    return total


def _map_header_size(length: int) -> int:
    if length <= 15:
        return 1
    if length <= 0xFFFF:
        return 3
    if length <= 0xFFFFFFFF:
        return 5
    raise TypeError("MessagePack map is too large")


def _array_header_size(length: int) -> int:
    if length <= 15:
        return 1
    if length <= 0xFFFF:
        return 3
    if length <= 0xFFFFFFFF:
        return 5
    raise TypeError("MessagePack array is too large")


def _string_header_size(length: int) -> int:
    if length <= 31:
        return 1
    if length <= 0xFF:
        return 2
    if length <= 0xFFFF:
        return 3
    if length <= 0xFFFFFFFF:
        return 5
    raise TypeError("MessagePack string is too large")


def _binary_header_size(length: int) -> int:
    if length <= 0xFF:
        return 2
    if length <= 0xFFFF:
        return 3
    if length <= 0xFFFFFFFF:
        return 5
    raise TypeError("MessagePack binary value is too large")


def _string_utf8_size(value: str, remaining_bytes: int) -> int:
    if len(value) > remaining_bytes:
        raise ResourceLimitError("record exceeds max_record_bytes")
    utf8_bytes = 0
    for offset in range(0, len(value), 4096):
        try:
            utf8_bytes += len(value[offset : offset + 4096].encode("utf-8"))
        except UnicodeEncodeError:
            raise
        if utf8_bytes > remaining_bytes:
            raise ResourceLimitError("record exceeds max_record_bytes")
    return utf8_bytes


def _scalar_size(value: object, remaining_bytes: int) -> int:
    value_type = type(value)
    if value is None or value_type is bool:
        return 1
    if value_type is int:
        if value < _MESSAGEPACK_INT_MIN or value > _MESSAGEPACK_INT_MAX:
            raise TypeError("integer is outside the MessagePack integer range")
        if -32 <= value <= 127:
            return 1
        if -128 <= value <= 0xFF:
            return 2
        if -0x8000 <= value <= 0xFFFF:
            return 3
        if -(1 << 31) <= value <= 0xFFFFFFFF:
            return 5
        return 9
    if value_type is float:
        return 9
    if value_type is str:
        utf8_bytes = _string_utf8_size(value, remaining_bytes)
        return _string_header_size(utf8_bytes) + utf8_bytes
    if value_type is bytes:
        return _binary_header_size(len(value)) + len(value)
    raise TypeError(f"write_records does not support values of type {value_type.__name__}")
