from collections import UserDict
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from jzpack import InvalidFormatError, compress, decompress
from jzpack.chunks import serialize_v3_container
from jzpack.schema import SchemaManager
from jzpack.serializer import PayloadSerializer


def _inner_payload(container: bytes) -> bytes:
    chunk_offset = 16
    payload_size = int.from_bytes(container[chunk_offset + 40 : chunk_offset + 48], "big")
    payload_offset = chunk_offset + 56
    return container[payload_offset : payload_offset + payload_size]


def _mutated_container(mutator: Callable[[dict[str, Any]], None], records: list[dict[str, Any]]) -> bytes:
    serializer = PayloadSerializer()
    payload, _ = serializer.deserialize_with_body(_inner_payload(compress(records)))
    payload = deepcopy(payload)
    mutator(payload)
    inner_payload, body = serializer.serialize_with_body(payload)
    return serialize_v3_container(
        inner_payload,
        record_count=len(records),
        uncompressed_body_bytes=len(body),
    )


def test_structural_paths_preserve_dotted_keys_nested_keys_missing_and_null() -> None:
    records = [
        {
            "a.b": "literal dotted key",
            "a": {"b": "nested key"},
            "nullable": None,
            "empty": {},
            "nested": {"items": [1, {"雪": True}]},
            "blob": b"\x00\x01data",
        },
        {"a.b": "second literal", "a": {"b": "second nested"}, "empty": {}, "nested": {"items": []}},
        {},
        {"nullable": None},
    ]
    original = deepcopy(records)

    assert decompress(compress(records)) == records
    assert records == original
    assert "nullable" not in decompress(compress([{}]))[0]
    assert decompress(compress([{"nullable": None}])) == [{"nullable": None}]


def test_empty_mappings_and_alternating_shapes_keep_record_order() -> None:
    records = [{}, {"outer": {}}, {"outer": {"value": 1}}, {}, {"outer": {"value": 2}}]

    assert decompress(compress(records)) == records


def test_builtin_dict_flatten_preserves_nested_empty_null_and_literal_keys() -> None:
    manager = SchemaManager()
    record = {
        "a.b": None,
        "a": {"b": 2},
        "empty": {},
        "nested": {"empty": {}},
    }

    assert manager._flatten(record) == {
        ("a.b",): None,
        ("a", "b"): 2,
        ("empty",): {},
        ("nested", "empty"): {},
    }


def test_userdict_root_and_nested_mappings_keep_generic_mapping_behavior() -> None:
    manager = SchemaManager()
    record = UserDict(
        {
            "outer": UserDict({"value": 7, "empty": UserDict()}),
            "nullable": None,
        }
    )

    assert manager._flatten(record) == {
        ("outer", "value"): 7,
        ("outer", "empty"): {},
        ("nullable",): None,
    }
    assert decompress(compress(record)) == [
        {"outer": {"value": 7, "empty": {}}, "nullable": None}
    ]


@pytest.mark.parametrize(
    "record",
    [
        {1: "bad"},
        {"outer": {1: "bad"}},
        UserDict({1: "bad"}),
        {"outer": UserDict({1: "bad"})},
    ],
    ids=["builtin-root", "builtin-nested", "userdict-root", "userdict-nested"],
)
def test_flatten_rejects_non_string_keys_for_builtin_and_custom_mappings(record: Any) -> None:
    with pytest.raises(TypeError, match="JZPack records must use string keys"):
        SchemaManager()._flatten(record)


def test_flatten_keeps_non_mapping_root_error() -> None:
    with pytest.raises(TypeError, match="JZPack records must be mappings"):
        SchemaManager()._flatten([("a", 1)])  # type: ignore[arg-type]


def test_late_invalid_record_keeps_existing_schema_state_unchanged() -> None:
    manager = SchemaManager()
    manager.add_record({"existing": "value"})
    schemas_before = deepcopy(manager.get_schemas())
    order_before = manager.get_schema_order().copy()
    records: list[Any] = [{"same": index} for index in range(32)]
    records[-1] = {1: "invalid key"}

    with pytest.raises(TypeError, match="JZPack records must use string keys"):
        manager.add_batch(records)

    assert manager.get_schemas() == schemas_before
    assert manager.get_schema_order() == order_before


def test_mapping_fast_path_keeps_missing_null_heterogeneous_and_archive_fidelity() -> None:
    records = [
        {"shared": None, "nested": {"empty": {}}},
        {"shared": "present", "other": 2},
        {"nested": {"value": [1, {"雪": True}]}},
        {},
    ]

    archive = compress(records)

    assert decompress(archive) == records
    assert compress(records) == archive
    assert decompress(compress([{}])) == [{}]
    assert decompress(compress([{"shared": None}])) == [{"shared": None}]


@pytest.mark.parametrize(
    "paths",
    [
        [[], ["b"]],
        [["a", 1], ["b"]],
        [["a"], ["a"]],
        [["a"], ["a", "b"]],
    ],
    ids=["empty-path", "non-string-segment", "duplicate-path", "prefix-collision"],
)
def test_malformed_structural_paths_are_reported_as_invalid_format(paths: list[list[Any]]) -> None:
    records = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    container = _mutated_container(
        lambda payload: payload["s"]["s0"].__setitem__("k", paths),
        records,
    )

    with pytest.raises(InvalidFormatError):
        decompress(container)


@pytest.mark.parametrize("row_count", [True, -1, 1, 3])
def test_malformed_schema_row_counts_are_reported_as_invalid_format(row_count: object) -> None:
    records = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    container = _mutated_container(
        lambda payload: payload["s"]["s0"].__setitem__("n", row_count),
        records,
    )

    with pytest.raises(InvalidFormatError):
        decompress(container)


@pytest.mark.parametrize("order", [[("s0", 1)], [("missing", 2)]])
def test_schema_order_counts_and_ids_must_match_schema_rows(order: list[tuple[str, int]]) -> None:
    records = [{"a": 1}, {"a": 2}]
    container = _mutated_container(lambda payload: payload.__setitem__("o", order), records)

    with pytest.raises(InvalidFormatError):
        decompress(container)
