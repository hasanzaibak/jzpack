"""Contracts for shallow schema reconstruction and its nested fallback."""

import struct
from typing import Any

import pytest

from jzpack import compress, decompress
from jzpack.schema import SchemaReconstructor
from tests.fidelity_oracle import is_faithful


def _float_from_bits(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def test_flat_schema_preserves_path_order_and_duplicate_overwrite_behavior() -> None:
    schema: dict[str, Any] = {
        "keys": [("second",), ("first",), ("second",)],
        "columns": {
            ("second",): [2, 4],
            ("first",): [1, 3],
        },
        "count": 2,
    }

    records = SchemaReconstructor().reconstruct_records(schema)

    assert records == [{"second": 2, "first": 1}, {"second": 4, "first": 3}]
    assert [list(record) for record in records] == [["second", "first"], ["second", "first"]]


def test_flat_schema_preserves_empty_rows_zero_rows_and_column_validation() -> None:
    reconstructor = SchemaReconstructor()

    assert reconstructor.reconstruct_records({"keys": [("unused",)], "columns": {}, "count": 2}) == [
        {},
        {},
    ]
    assert reconstructor.reconstruct_records(
        {"keys": [("value",)], "columns": {("value",): []}, "count": 0}
    ) == []

    invalid = {"keys": [("value",)], "columns": {("value",): [1]}, "count": 2}
    with pytest.raises(
        ValueError,
        match="Invalid JZPK payload: column length does not match row count",
    ):
        reconstructor.reconstruct_records(invalid)


def test_flat_public_round_trip_preserves_missing_null_types_bits_and_record_order() -> None:
    negative_zero = _float_from_bits("8000000000000000")
    nan_with_payload = _float_from_bits("7ff8000000000042")
    records = [
        {"z": negative_zero, "a": None, "flag": True, "big": 2**64 - 1},
        {"z": nan_with_payload, "flag": 1, "big": -(2**63)},
        {},
        {"a": None, "big": False},
    ]

    restored = decompress(compress(records))

    assert is_faithful(records, restored)
