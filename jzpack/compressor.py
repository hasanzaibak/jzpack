from typing import Any

from .analyzer import ColumnEncoder
from .encoders import RLEEncoder
from .schema import SchemaManager, SchemaReconstructor
from .serializer import PayloadSerializer


class JZPackCompressor:
    def __init__(self, compression_level: int = 3, fast: bool = False):
        self._schema_manager = SchemaManager()
        self._column_encoder = ColumnEncoder(skip_analysis=fast)
        self._serializer = PayloadSerializer(compression_level)
        self._reconstructor = SchemaReconstructor()

    def compress(self, data: list[dict[str, Any]]) -> bytes:
        if isinstance(data, dict):
            data = [data]

        if not data:
            return self._serializer.serialize({"s": {}, "o": []})

        self._schema_manager.clear()
        self._schema_manager.add_batch(data)
        payload = self._build_payload()
        return self._serializer.serialize(payload)

    def decompress(self, data: bytes) -> list[dict[str, Any]]:
        payload = self._serializer.deserialize(data)
        return self._reconstruct(payload)

    def compress_to_file(self, data: list[dict[str, Any]], path: str) -> int:
        compressed = self.compress(data)
        with open(path, "wb") as f:
            f.write(compressed)
        return len(compressed)

    def decompress_from_file(self, path: str) -> list[dict[str, Any]]:
        with open(path, "rb") as f:
            return self.decompress(f.read())

    def _build_payload(self) -> dict:
        schemas = {}

        for schema_id, group in self._schema_manager.get_schemas().items():
            encoded_columns = {key: self._column_encoder.encode(group["columns"][key]) for key in group["keys"]}
            schemas[schema_id] = {"k": group["keys"], "c": encoded_columns}

        return {"s": schemas, "o": RLEEncoder.encode(self._schema_manager.get_schema_order())}

    def _reconstruct(self, payload: dict) -> list[dict[str, Any]]:
        schemas = payload["s"]

        if len(schemas) == 1:
            return self._reconstruct_single_schema(schemas)

        return self._reconstruct_multi_schema(payload)

    def _reconstruct_single_schema(self, schemas: dict) -> list[dict[str, Any]]:
        schema_data = next(iter(schemas.values()))
        decoded_columns = {key: self._column_encoder.decode(schema_data["c"][key]) for key in schema_data["k"]}
        return self._reconstructor.reconstruct_records({"keys": schema_data["k"], "columns": decoded_columns})

    def _reconstruct_multi_schema(self, payload: dict) -> list[dict[str, Any]]:
        schema_records = {}

        for schema_id, schema_data in payload["s"].items():
            decoded_columns = {key: self._column_encoder.decode(schema_data["c"][key]) for key in schema_data["k"]}
            schema_records[schema_id] = self._reconstructor.reconstruct_records(
                {"keys": schema_data["k"], "columns": decoded_columns}
            )

        order = payload.get("o", [])
        if not order:
            return [rec for records in schema_records.values() for rec in records]

        schema_order = RLEEncoder.decode(order)
        schema_indices = {sid: 0 for sid in schema_records}
        result = []

        for schema_id in schema_order:
            idx = schema_indices[schema_id]
            result.append(schema_records[schema_id][idx])
            schema_indices[schema_id] = idx + 1

        return result


class StreamingCompressor:
    def __init__(self, compression_level: int = 3, fast: bool = False):
        self._compression_level = compression_level
        self._fast = fast
        self._schema_manager = SchemaManager()

    def add_record(self, record: dict[str, Any]) -> None:
        self._schema_manager.add_record(record)

    def add_batch(self, records: list[dict[str, Any]]) -> None:
        self._schema_manager.add_batch(records)

    def finalize(self) -> bytes:
        compressor = JZPackCompressor(compression_level=self._compression_level, fast=self._fast)
        compressor._schema_manager = self._schema_manager
        return compressor._serializer.serialize(compressor._build_payload())

    def clear(self) -> None:
        self._schema_manager.clear()
