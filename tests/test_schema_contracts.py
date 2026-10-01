from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from jzpack import InvalidFormatError, compress, decompress
from jzpack.chunks import serialize_v3_container
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
