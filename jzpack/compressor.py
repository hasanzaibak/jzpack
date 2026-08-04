import io
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import Any, BinaryIO

from .analyzer import ColumnEncoder
from .encoders import RLEEncoder
from .errors import InvalidFormatError, ResourceLimitError
from .schema import Path, SchemaManager, SchemaReconstructor
from .serializer import PayloadSerializer

FilePath = str | os.PathLike[str]
FileDestination = FilePath | BinaryIO
FileSource = FilePath | BinaryIO


class JZPackCompressor:
    def __init__(self, compression_level: int = 3, fast: bool = False):
        self._schema_manager = SchemaManager()
        self._column_encoder = ColumnEncoder(skip_analysis=fast)
        self._serializer = PayloadSerializer(compression_level)
        self._reconstructor = SchemaReconstructor()

    def compress(self, data: Iterable[Mapping[str, Any]] | Mapping[str, Any]) -> bytes:
        if isinstance(data, Mapping):
            records = [data]
        else:
            try:
                records = list(data)
            except TypeError as exc:
                raise TypeError("data must be a mapping or an iterable of mappings") from exc

        if not records:
            return self._serializer.serialize({"s": {}, "o": []})

        self._schema_manager.clear()
        self._schema_manager.add_batch(records)
        payload = self._build_payload()
        return self._serializer.serialize(payload)

    def decompress(self, data: bytes, max_output_size: int | None = None, max_records: int | None = None) -> list[dict[str, Any]]:
        payload = self._serializer.deserialize(data, max_output_size=max_output_size)
        return self._reconstruct(payload, max_records=max_records)

    def compress_to_file(self, data: Iterable[Mapping[str, Any]] | Mapping[str, Any], path: FileDestination) -> int:
        destination_path = _coerce_path(path, "destination")
        compressed = self.compress(data)
        if destination_path is not None:
            _write_atomically(destination_path, compressed)
        else:
            _write_file_like(path, compressed)
        return len(compressed)

    def decompress_from_file(
        self, path: FileSource, max_output_size: int | None = None, max_records: int | None = None
    ) -> list[dict[str, Any]]:
        source_path = _coerce_path(path, "source")
        if source_path is not None:
            with open(source_path, "rb") as stream:
                data = stream.read()
        else:
            data = _read_file_like(path)
        return self.decompress(data, max_output_size=max_output_size, max_records=max_records)

    def _build_payload(self) -> dict:
        schemas = {}

        for schema_id, group in self._schema_manager.get_schemas().items():
            keys: list[Path] = group["keys"]
            encoded_columns = [self._column_encoder.encode(group["columns"][key]) for key in keys]
            schemas[schema_id] = {
                "k": [list(key) for key in keys],
                "c": encoded_columns,
                "n": group["count"],
            }

        return {"s": schemas, "o": RLEEncoder.encode(self._schema_manager.get_schema_order())}

    def _reconstruct(self, payload: dict[str, Any], max_records: int | None = None) -> list[dict[str, Any]]:
        if not isinstance(payload.get("s"), dict):
            raise InvalidFormatError("Invalid JZPK payload: missing schema map")

        if max_records is not None and (
            isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 0
        ):
            raise ValueError("max_records must be a non-negative integer")

        schemas = payload["s"]
        if any(not isinstance(schema_id, str) for schema_id in schemas):
            raise InvalidFormatError("Invalid JZPK payload: schema IDs must be strings")
        order = payload.get("o", [])
        try:
            schema_order = RLEEncoder.decode(order, max_output_size=max_records) if order else []
        except ResourceLimitError as exc:
            if max_records is not None:
                raise ResourceLimitError("JZPK payload exceeds max_records") from exc
            raise
        except (TypeError, ValueError, IndexError) as exc:
            raise InvalidFormatError("Invalid JZPK schema order") from exc
        if any(not isinstance(schema_id, str) for schema_id in schema_order):
            raise InvalidFormatError("Invalid JZPK payload: schema order must contain strings")

        if max_records is not None and len(schema_order) > max_records:
            raise ResourceLimitError("JZPK payload exceeds max_records")

        if len(schemas) == 1:
            result = self._reconstruct_single_schema(schemas, schema_order, max_records)
        else:
            result = self._reconstruct_multi_schema(schemas, schema_order, max_records)

        if max_records is not None and len(result) > max_records:
            raise ResourceLimitError("JZPK payload exceeds max_records")
        return result

    def _reconstruct_single_schema(
        self, schemas: dict, schema_order: list[str], max_records: int | None
    ) -> list[dict[str, Any]]:
        schema_id, schema_data = next(iter(schemas.items()))
        if schema_order and any(order_id != schema_id for order_id in schema_order):
            raise InvalidFormatError("Invalid JZPK payload: schema order mismatch")
        fallback_count = schema_order.count(schema_id) if schema_order else None
        records = self._decode_schema(schema_data, fallback_count, max_records)
        if schema_order and len(records) != len(schema_order):
            raise InvalidFormatError("Invalid JZPK payload: schema row count mismatch")
        return records

    def _reconstruct_multi_schema(
        self, schemas: dict, schema_order: list[str], max_records: int | None
    ) -> list[dict[str, Any]]:
        counts = Counter(schema_order)
        schema_records = {}

        for schema_id, schema_data in schemas.items():
            schema_records[schema_id] = self._decode_schema(schema_data, counts.get(schema_id), max_records)

        if not schema_order:
            return [rec for records in schema_records.values() for rec in records]

        schema_indices = {sid: 0 for sid in schema_records}
        result = []

        for schema_id in schema_order:
            if schema_id not in schema_records:
                raise InvalidFormatError("Invalid JZPK payload: unknown schema in order")
            idx = schema_indices[schema_id]
            if idx >= len(schema_records[schema_id]):
                raise InvalidFormatError("Invalid JZPK payload: schema row count mismatch")
            result.append(schema_records[schema_id][idx])
            schema_indices[schema_id] = idx + 1

        if any(schema_indices[sid] != len(records) for sid, records in schema_records.items()):
            raise InvalidFormatError("Invalid JZPK payload: schema row count mismatch")

        return result

    def _decode_schema(
        self, schema_data: Any, fallback_count: int | None, max_records: int | None
    ) -> list[dict[str, Any]]:
        if not isinstance(schema_data, dict):
            raise InvalidFormatError("Invalid JZPK payload: schema must be a map")

        raw_keys = schema_data.get("k")
        raw_columns = schema_data.get("c")
        if not isinstance(raw_keys, list):
            raise InvalidFormatError("Invalid JZPK payload: schema keys must be a list")

        keys: list[Path] = []
        for raw_key in raw_keys:
            if isinstance(raw_key, str):
                key = tuple(raw_key.split(".")) if "." in raw_key else (raw_key,)
            elif isinstance(raw_key, list) and all(isinstance(segment, str) for segment in raw_key):
                key = tuple(raw_key)
            else:
                raise InvalidFormatError("Invalid JZPK payload: invalid schema path")
            if not key:
                raise InvalidFormatError("Invalid JZPK payload: empty schema path")
            keys.append(key)
        if len(set(keys)) != len(keys):
            raise InvalidFormatError("Invalid JZPK payload: duplicate schema path")

        try:
            if isinstance(raw_columns, list):
                if len(raw_columns) != len(keys):
                    raise InvalidFormatError("Invalid JZPK payload: key and column counts differ")
                decoded_columns = {
                    key: self._column_encoder.decode(encoded, max_values=max_records)
                    for key, encoded in zip(keys, raw_columns)
                }
            elif isinstance(raw_columns, dict):
                decoded_columns = {
                    (tuple(raw_key.split(".")) if "." in raw_key else (raw_key,)): self._column_encoder.decode(
                        encoded, max_values=max_records
                    )
                    for raw_key, encoded in raw_columns.items()
                    if isinstance(raw_key, str)
                }
                if len(decoded_columns) != len(raw_columns):
                    raise InvalidFormatError("Invalid JZPK payload: invalid legacy column key")
            else:
                raise InvalidFormatError("Invalid JZPK payload: columns must be a list or map")
            if set(decoded_columns) != set(keys):
                raise InvalidFormatError("Invalid JZPK payload: schema keys and columns differ")
        except InvalidFormatError:
            raise
        except ResourceLimitError as exc:
            if max_records is not None:
                raise ResourceLimitError("JZPK payload exceeds max_records") from exc
            raise
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise InvalidFormatError("Invalid JZPK column encoding") from exc

        count = schema_data.get("n", schema_data.get("count", fallback_count))
        if count is None:
            count = len(decoded_columns[keys[0]]) if keys else 0
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise InvalidFormatError("Invalid JZPK payload: invalid row count")
        if max_records is not None and count > max_records:
            raise ResourceLimitError("JZPK payload exceeds max_records")

        return self._reconstructor.reconstruct_records({"keys": keys, "columns": decoded_columns, "count": count})


def _coerce_path(value: FilePath | BinaryIO, kind: str) -> str | None:
    if not isinstance(value, (str, os.PathLike)):
        return None

    path = os.fspath(value)
    if not isinstance(path, str):
        raise TypeError(f"{kind} path must be a str or os.PathLike[str]")
    return path


def _write_file_like(stream: FileDestination, data: bytes) -> None:
    if isinstance(stream, io.TextIOBase) or not callable(getattr(stream, "write", None)):
        raise TypeError("destination must be a str, os.PathLike[str], or binary writable file-like object")

    written = stream.write(data)  # type: ignore[union-attr]
    if written is None:
        return
    if isinstance(written, bool) or not isinstance(written, int):
        raise TypeError("binary writable file-like object's write() must return an integer or None")
    if written != len(data):
        raise OSError(f"binary writable file-like object wrote {written} of {len(data)} bytes")


def _read_file_like(stream: FileSource) -> bytes | bytearray | memoryview:
    if isinstance(stream, io.TextIOBase) or not callable(getattr(stream, "read", None)):
        raise TypeError("source must be a str, os.PathLike[str], or binary readable file-like object")

    data = stream.read()  # type: ignore[union-attr]
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("source must be a binary readable file-like object")
    return data


def _write_atomically(path: str, data: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    prefix = f".{os.path.basename(path)}."
    file_descriptor: int | None = None
    temporary_path: str | None = None

    try:
        file_descriptor, temporary_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
        with os.fdopen(file_descriptor, "wb") as stream:
            file_descriptor = None
            written = stream.write(data)
            if written is not None and written != len(data):
                raise OSError(f"temporary file write was incomplete: wrote {written} of {len(data)} bytes")
            stream.flush()
            fsync = getattr(os, "fsync", None)
            if fsync is not None:
                fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if file_descriptor is not None:
            with suppress(OSError):
                os.close(file_descriptor)
        if temporary_path is not None:
            with suppress(OSError):
                os.unlink(temporary_path)


class StreamingCompressor:
    def __init__(self, compression_level: int = 3, fast: bool = False):
        self._compression_level = compression_level
        self._fast = fast
        self._schema_manager = SchemaManager()

    def add_record(self, record: dict[str, Any]) -> None:
        self._schema_manager.add_record(record)

    def add_batch(self, records: Iterable[Mapping[str, Any]]) -> None:
        self._schema_manager.add_batch(records)

    def finalize(self) -> bytes:
        compressor = JZPackCompressor(compression_level=self._compression_level, fast=self._fast)
        compressor._schema_manager = self._schema_manager
        return compressor._serializer.serialize(compressor._build_payload())

    def clear(self) -> None:
        self._schema_manager.clear()
