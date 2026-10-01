"""Public round-trip coverage for the common nested-schema reconstruction path."""

import struct

from jzpack import compress, decompress
from tests.fidelity_oracle import is_faithful


def _float_from_bits(bits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(bits))[0]


def test_nested_schema_fast_path_preserves_exact_values_and_record_order() -> None:
    negative_zero = _float_from_bits("8000000000000000")
    nan_with_payload = _float_from_bits("7ff8000000000042")
    records = [
        {
            "account": {
                "id": 2**64 - 1,
                "active": True,
                "metrics": {"score": negative_zero, "details": {"sample": nan_with_payload}},
            },
            "events": ["open", {"ok": False}],
            "nullable": None,
        },
        {
            "account": {
                "id": -(2**63),
                "active": 1,
                "metrics": {"score": nan_with_payload, "details": {"sample": -0.0}},
            },
            "events": [{"ok": 1}, "close"],
        },
        {"account": {"id": 0}, "account.meta": {"literal.dot": True}, "empty": {}},
        {},
    ]

    restored = decompress(compress(records))

    assert is_faithful(restored, records)
