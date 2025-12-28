from typing import Any


class SchemaManager:
    def __init__(self):
        self._groups: dict[str, dict] = {}
        self._schema_order: list[str] = []
        self._schema_id_cache: dict[tuple, str] = {}
        self._key_path_cache: dict[tuple, list[tuple[str, list[str] | None]]] = {}

    def add_batch(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return

        first_flat = self._flatten(records[0])
        first_keys = tuple(sorted(first_flat.keys()))

        if self._has_uniform_schema(records, first_keys):
            self._add_uniform_batch(records, first_keys)
        else:
            for record in records:
                self.add_record(record)

    def add_record(self, record: dict[str, Any]) -> str:
        flat = self._flatten(record)
        keys = tuple(sorted(flat.keys()))
        schema_id = self._get_schema_id(keys)

        if schema_id not in self._groups:
            self._groups[schema_id] = {"keys": list(keys), "columns": {k: [] for k in keys}}

        group = self._groups[schema_id]
        for key in keys:
            group["columns"][key].append(flat[key])

        self._schema_order.append(schema_id)
        return schema_id

    def get_schemas(self) -> dict[str, dict]:
        return self._groups

    def get_schema_order(self) -> list[str]:
        return self._schema_order

    def clear(self) -> None:
        self._groups.clear()
        self._schema_order.clear()
        self._schema_id_cache.clear()
        self._key_path_cache.clear()

    def _has_uniform_schema(self, records: list[dict], reference_keys: tuple) -> bool:
        n = len(records)
        sample_indices = {0, n // 4, n // 2, 3 * n // 4, n - 1}
        sample_indices = [i for i in sample_indices if 0 <= i < n]

        for i in sample_indices:
            flat = self._flatten(records[i])
            if tuple(sorted(flat.keys())) != reference_keys:
                return False
        return True

    def _add_uniform_batch(self, records: list[dict], keys: tuple) -> None:
        schema_id = self._get_schema_id(keys)
        key_list = list(keys)
        num_records = len(records)
        key_paths = self._resolve_key_paths(keys)

        if schema_id not in self._groups:
            columns = {k: [None] * num_records for k in key_list}
            self._groups[schema_id] = {"keys": key_list, "columns": columns}
            start_idx = 0
        else:
            group = self._groups[schema_id]
            start_idx = len(group["columns"][key_list[0]])
            for key in key_list:
                group["columns"][key].extend([None] * num_records)
            columns = group["columns"]

        for i, record in enumerate(records):
            idx = start_idx + i
            for key, path in key_paths:
                columns[key][idx] = self._extract_value(record, path)

        self._schema_order.extend([schema_id] * num_records)

    def _extract_value(self, record: dict, path: list[str] | None) -> Any:
        if path is None:
            return None

        value = record
        for segment in path:
            if isinstance(value, dict):
                value = value.get(segment)
            else:
                return None
        return value

    def _resolve_key_paths(self, keys: tuple) -> list[tuple[str, list[str] | None]]:
        if keys in self._key_path_cache:
            return self._key_path_cache[keys]

        paths = []
        for key in keys:
            segments = key.split(".") if "." in key else [key]
            paths.append((key, segments))

        self._key_path_cache[keys] = paths
        return paths

    def _flatten(self, obj: dict, prefix: str = "") -> dict[str, Any]:
        items = {}
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                nested = self._flatten(value, full_key)
                items.update(nested) if nested else items.update({full_key: {}})
            else:
                items[full_key] = value
        return items

    def _get_schema_id(self, keys: tuple) -> str:
        if keys in self._schema_id_cache:
            return self._schema_id_cache[keys]

        schema_id = format(hash(keys) & 0xFFFFFFFF, "x")
        self._schema_id_cache[keys] = schema_id
        return schema_id


class SchemaReconstructor:
    __slots__ = ()

    def reconstruct_records(self, schema: dict) -> list[dict[str, Any]]:
        keys = schema["keys"]
        columns = schema["columns"]

        if not columns:
            return []

        num_records = len(columns[keys[0]])
        key_paths = self._parse_key_paths(keys)

        if not any(path for _, path in key_paths):
            return [{key: columns[key][i] for key in keys} for i in range(num_records)]

        return [self._build_record(columns, key_paths, i) for i in range(num_records)]

    def _parse_key_paths(self, keys: list[str]) -> list[tuple[str, list[str] | None]]:
        return [(key, key.split(".") if "." in key else None) for key in keys]

    def _build_record(self, columns: dict, key_paths: list[tuple[str, list[str] | None]], index: int) -> dict[str, Any]:
        result = {}
        for key, parts in key_paths:
            value = columns[key][index]
            if parts is None:
                result[key] = value
            else:
                self._set_nested_value(result, parts, value)
        return result

    def _set_nested_value(self, target: dict, parts: list[str], value: Any) -> None:
        current = target
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
