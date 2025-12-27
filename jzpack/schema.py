import hashlib
from collections import defaultdict
from typing import Any


class SchemaManager:
    def __init__(self):
        self._groups: dict[str, dict] = defaultdict(
            lambda: {"keys": None, "columns": defaultdict(list)}
        )
        self._schema_order: list[str] = []
        self._schema_id_cache: dict[tuple, str] = {}

    def add_record(self, record: dict[str, Any]) -> str:
        flat = self._flatten(record)
        keys = tuple(sorted(flat.keys()))
        schema_id = self._get_schema_id(keys)

        group = self._groups[schema_id]
        if group["keys"] is None:
            group["keys"] = list(keys)

        for key in keys:
            group["columns"][key].append(flat[key])

        self._schema_order.append(schema_id)
        return schema_id

    def add_batch(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            self.add_record(record)

    def get_schemas(self) -> dict[str, dict]:
        return dict(self._groups)

    def get_schema_order(self) -> list[str]:
        return self._schema_order

    def clear(self) -> None:
        self._groups.clear()
        self._schema_order.clear()
        self._schema_id_cache.clear()

    def _flatten(self, obj: dict, prefix: str = "") -> dict[str, Any]:
        items = {}
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                nested = self._flatten(value, full_key)
                if nested:
                    items.update(nested)
                else:
                    items[full_key] = {}
            else:
                items[full_key] = value
        return items

    def _get_schema_id(self, keys: tuple) -> str:
        if keys in self._schema_id_cache:
            return self._schema_id_cache[keys]

        key_str = ",".join(keys)
        schema_id = hashlib.md5(key_str.encode()).hexdigest()[:8]
        self._schema_id_cache[keys] = schema_id
        return schema_id


class SchemaReconstructor:
    def reconstruct_records(self, schema: dict) -> list[dict[str, Any]]:
        keys = schema["keys"]
        columns = schema["columns"]

        if not columns:
            return []

        num_records = len(columns[keys[0]])
        records = []

        for i in range(num_records):
            flat = {key: columns[key][i] for key in keys}
            records.append(self._unflatten(flat))

        return records

    def _unflatten(self, flat: dict[str, Any]) -> dict[str, Any]:
        result = {}
        for key, value in flat.items():
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        return result
