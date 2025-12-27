from typing import Any

from .analyzer import ColumnEncoder
from .encoders import RLEEncoder
from .schema import SchemaManager, SchemaReconstructor
from .serializer import PayloadSerializer


class JZPackCompressor:
    def __init__(self, compression_level: int = 3):
        self._schema_manager = SchemaManager()
        self._column_encoder = ColumnEncoder()
        self._serializer = PayloadSerializer(compression_level)

    def compress(self, data: list[dict[str, Any]]) -> bytes:
        if not data:
            return self._serializer.serialize({"s": {}, "o": []})

        self._schema_manager.clear()
        self._schema_manager.add_batch(data)
        payload = self._build_payload()
        return self._serializer.serialize(payload)

    def decompress(self, data: bytes) -> list[dict[str, Any]]:
        payload = self._serializer.deserialize(data)
        return self._reconstruct_data(payload)

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
            encoded_columns = {}
            for key in group["keys"]:
                values = group["columns"][key]
                encoded_columns[key] = self._column_encoder.encode_column(values)

            schemas[schema_id] = {"k": group["keys"], "c": encoded_columns}

        schema_order = self._schema_manager.get_schema_order()
        order_rle = RLEEncoder.encode(schema_order)

        return {"s": schemas, "o": order_rle}

    def _reconstruct_data(self, payload: dict) -> list[dict[str, Any]]:
        reconstructor = SchemaReconstructor()

        schema_records: dict[str, list[dict]] = {}
        for schema_id, schema_data in payload["s"].items():
            keys = schema_data["k"]
            decoded_columns = {}

            for key in keys:
                encoded = schema_data["c"][key]
                decoded_columns[key] = self._column_encoder.decode_column(encoded)

            schema = {"keys": keys, "columns": decoded_columns}
            schema_records[schema_id] = reconstructor.reconstruct_records(schema)

        order_rle = payload.get("o", [])
        if not order_rle:
            all_records = []
            for records in schema_records.values():
                all_records.extend(records)
            return all_records

        schema_order = RLEEncoder.decode(order_rle)

        schema_indices: dict[str, int] = {sid: 0 for sid in schema_records}
        result = []

        for schema_id in schema_order:
            idx = schema_indices[schema_id]
            result.append(schema_records[schema_id][idx])
            schema_indices[schema_id] = idx + 1

        return result


class StreamingCompressor:
    def __init__(self, compression_level: int = 3):
        self._schema_manager = SchemaManager()
        self._column_encoder = ColumnEncoder()
        self._serializer = PayloadSerializer(compression_level)

    def add_record(self, record: dict[str, Any]) -> None:
        self._schema_manager.add_record(record)

    def add_batch(self, records: list[dict[str, Any]]) -> None:
        self._schema_manager.add_batch(records)

    def finalize(self) -> bytes:
        compressor = JZPackCompressor()
        compressor._schema_manager = self._schema_manager
        compressor._column_encoder = self._column_encoder
        compressor._serializer = self._serializer
        return compressor._serializer.serialize(compressor._build_payload())

    def clear(self) -> None:
        self._schema_manager.clear()
