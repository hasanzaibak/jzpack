from collections.abc import Iterable, Mapping
from typing import Any

Path = tuple[str, ...]


class SchemaManager:
    def __init__(self):
        self._groups: dict[str, dict[str, Any]] = {}
        self._schema_order: list[str] = []
        self._schema_id_cache: dict[tuple[Path, ...], str] = {}

    def add_batch(self, records: Iterable[Mapping[str, Any]]) -> None:
        records = list(records)
        if not records:
            return

        first_flat = self._flatten(records[0])
        first_keys = tuple(sorted(first_flat))

        if self._has_uniform_schema(records, first_keys):
            self._add_uniform_batch(records, first_keys)
        else:
            for record in records:
                self.add_record(record)

    def add_record(self, record: Mapping[str, Any]) -> str:
        flat = self._flatten(record)
        keys = tuple(sorted(flat))
        schema_id = self._get_schema_id(keys)

        if schema_id not in self._groups:
            self._groups[schema_id] = {
                "keys": list(keys),
                "columns": {key: [] for key in keys},
                "count": 0,
            }

        group = self._groups[schema_id]
        for key in keys:
            group["columns"][key].append(flat[key])

        group["count"] += 1
        self._schema_order.append(schema_id)
        return schema_id

    def get_schemas(self) -> dict[str, dict[str, Any]]:
        return self._groups

    def get_schema_order(self) -> list[str]:
        return self._schema_order

    def clear(self) -> None:
        self._groups.clear()
        self._schema_order.clear()
        self._schema_id_cache.clear()

    def _has_uniform_schema(self, records: list[Mapping[str, Any]], reference_keys: tuple[Path, ...]) -> bool:
        for record in records:
            flat = self._flatten(record)
            if tuple(sorted(flat)) != reference_keys:
                return False
        return True

    def _add_uniform_batch(self, records: list[Mapping[str, Any]], keys: tuple[Path, ...]) -> None:
        schema_id = self._get_schema_id(keys)
        key_list = list(keys)
        num_records = len(records)

        if schema_id not in self._groups:
            columns = {k: [None] * num_records for k in key_list}
            self._groups[schema_id] = {"keys": key_list, "columns": columns, "count": num_records}
            start_idx = 0
        else:
            group = self._groups[schema_id]
            start_idx = group["count"]
            for key in key_list:
                group["columns"][key].extend([None] * num_records)
            columns = group["columns"]
            group["count"] += num_records

        for i, record in enumerate(records):
            flat = self._flatten(record)
            idx = start_idx + i
            for key in key_list:
                columns[key][idx] = flat[key]

        self._schema_order.extend([schema_id] * num_records)

    def _flatten(self, obj: Mapping[str, Any], prefix: Path = ()) -> dict[Path, Any]:
        if not isinstance(obj, Mapping):
            raise TypeError("JZPack records must be mappings")

        items = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError("JZPack records must use string keys")

            full_key = prefix + (key,)
            if isinstance(value, Mapping):
                nested = self._flatten(value, full_key)
                items.update(nested) if nested else items.update({full_key: {}})
            else:
                items[full_key] = value
        return items

    def _get_schema_id(self, keys: tuple[Path, ...]) -> str:
        if keys in self._schema_id_cache:
            return self._schema_id_cache[keys]

        schema_id = f"s{len(self._schema_id_cache)}"
        self._schema_id_cache[keys] = schema_id
        return schema_id


class SchemaReconstructor:
    __slots__ = ()

    def reconstruct_records(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        keys = [self._normalize_path(key) for key in schema.get("keys", [])]
        columns = schema.get("columns", {})
        num_records = schema.get("count")

        if num_records is None:
            num_records = len(columns[keys[0]]) if keys and columns else 0

        if not columns:
            return [{} for _ in range(num_records)]

        for key in keys:
            if key not in columns or len(columns[key]) != num_records:
                raise ValueError("Invalid JZPK payload: column length does not match row count")

        return [self._build_record(columns, keys, i) for i in range(num_records)]

    def _normalize_path(self, key: str | list[str] | tuple[str, ...]) -> Path:
        if isinstance(key, str):
            return tuple(key.split(".")) if "." in key else (key,)
        return tuple(key)

    def _build_record(self, columns: dict[Path, list[Any]], key_paths: list[Path], index: int) -> dict[str, Any]:
        result = {}
        for path in key_paths:
            value = columns[path][index]
            if len(path) == 1:
                result[path[0]] = value
            else:
                self._set_nested_value(result, path, value)
        return result

    def _set_nested_value(self, target: dict[str, Any], parts: Path, value: Any) -> None:
        current = target
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
