from collections.abc import Iterable, Mapping
from typing import Any

Path = tuple[str, ...]


class SchemaManager:
    def __init__(self):
        self._groups: dict[str, dict[str, Any]] = {}
        self._schema_order: list[str] = []
        self._schema_id_cache: dict[tuple[Path, ...], str] = {}

    def add_batch(self, records: Iterable[Mapping[str, Any]]) -> None:
        batch = records if isinstance(records, list) else list(records)
        if not batch:
            return

        # Uniform batches are fully validated before state changes, as before,
        # but their flattened values are then reused instead of recomputed.
        first_flat = self._flatten(batch[0])
        first_keys = tuple(sorted(first_flat))
        flattened_records = [first_flat]

        # A shape mismatch selects the heterogeneous path. Commit its already
        # flattened prefix, then process the remainder without another batch copy.
        for index in range(1, len(batch)):
            flat = self._flatten(batch[index])
            keys = tuple(sorted(flat))
            if keys != first_keys:
                for prefix_flat in flattened_records:
                    self._add_flat_record_with_keys(prefix_flat, first_keys)
                self._add_flat_record_with_keys(flat, keys)
                for remaining_index in range(index + 1, len(batch)):
                    self.add_record(batch[remaining_index])
                return
            flattened_records.append(flat)

        self._add_uniform_flat_batch(flattened_records, first_keys)

    def add_record(self, record: Mapping[str, Any]) -> str:
        return self._add_flat_record(self._flatten(record))

    def _add_flat_record(self, flat: dict[Path, Any]) -> str:
        keys = tuple(sorted(flat))
        return self._add_flat_record_with_keys(flat, keys)

    def _add_flat_record_with_keys(self, flat: dict[Path, Any], keys: tuple[Path, ...]) -> str:
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

    def _add_uniform_flat_batch(self, records: list[dict[Path, Any]], keys: tuple[Path, ...]) -> None:
        schema_id = self._get_schema_id(keys)
        key_list = list(keys)
        num_records = len(records)

        if schema_id not in self._groups:
            columns = {key: [None] * num_records for key in key_list}
            self._groups[schema_id] = {"keys": key_list, "columns": columns, "count": num_records}
            start_index = 0
        else:
            group = self._groups[schema_id]
            start_index = group["count"]
            columns = group["columns"]
            for key in key_list:
                columns[key].extend([None] * num_records)
            group["count"] += num_records

        for offset, flat in enumerate(records):
            row_index = start_index + offset
            for key in key_list:
                columns[key][row_index] = flat[key]

        self._schema_order.extend([schema_id] * num_records)

    def get_schemas(self) -> dict[str, dict[str, Any]]:
        return self._groups

    def get_schema_order(self) -> list[str]:
        return self._schema_order

    def clear(self) -> None:
        self._groups.clear()
        self._schema_order.clear()
        self._schema_id_cache.clear()

    def _flatten(self, obj: Mapping[str, Any], prefix: Path = ()) -> dict[Path, Any]:
        if type(obj) is not dict and not isinstance(obj, dict) and not isinstance(obj, Mapping):
            raise TypeError("JZPack records must be mappings")

        items = {}
        for key, value in obj.items():
            if type(key) is not str and not isinstance(key, str):
                raise TypeError("JZPack records must use string keys")

            full_key = prefix + (key,)
            value_type = type(value)
            if value_type is dict:
                nested = self._flatten(value, full_key)
                items.update(nested) if nested else items.update({full_key: {}})
            elif (
                value_type is int
                or value_type is str
                or value_type is bool
                or value_type is type(None)
                or value_type is float
                or value_type is bytes
                or value_type is list
                or value_type is tuple
            ):
                items[full_key] = value
            elif isinstance(value, dict) or isinstance(value, Mapping):
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

        if all(len(key) == 1 for key in keys):
            flat_columns = [(key[0], columns[key]) for key in keys]
            return [
                {field: values[index] for field, values in flat_columns}
                for index in range(num_records)
            ]

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
            elif len(path) == 2:
                parent_key, leaf_key = path
                if parent_key not in result:
                    result[parent_key] = {}
                parent = result[parent_key]
                parent[leaf_key] = value
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
