import struct
import sys

import pytest

from jzpack import ResourceLimitError, compress, decompress
from jzpack.analyzer import ColumnEncoder
from jzpack.chunks import serialize_v3_container
from jzpack.encoders import EncodingType, RLEEncoder
from jzpack.serializer import PayloadSerializer
from tests.fidelity_oracle import is_faithful

SINGLE_RUN_VALUES = [
    pytest.param(None, id="none"),
    pytest.param(False, id="bool"),
    pytest.param(-(2**63), id="minimum-int"),
    pytest.param(2**64 - 1, id="maximum-int"),
    pytest.param("constant", id="string"),
    pytest.param(b"constant", id="bytes"),
    pytest.param(0.0, id="positive-zero"),
    pytest.param(-0.0, id="negative-zero"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(
        struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0],
        id="nan-payload",
    ),
]


@pytest.mark.parametrize("value", SINGLE_RUN_VALUES)
def test_single_run_rle_preserves_exact_repeated_values(value):
    actual = RLEEncoder.decode([[value, 19]], max_output_size=19)

    assert is_faithful([value] * 19, actual)


def test_single_run_rle_preserves_legacy_nested_value_aliasing():
    nested_value = {"legacy": ["repeated"]}

    actual = RLEEncoder.decode([[nested_value, 4]], max_output_size=4)

    assert len(actual) == 4
    assert all(value is nested_value for value in actual)


def test_single_run_rle_checks_host_list_size_before_repetition():
    count = sys.maxsize + 1

    with pytest.raises(ValueError, match="supported list size"):
        RLEEncoder.decode([["constant", count]])


def test_single_run_rle_keeps_output_limit_error_before_repetition():
    with pytest.raises(ResourceLimitError, match="maximum output size"):
        RLEEncoder.decode([["constant", 101]], max_output_size=100)


def test_public_roundtrip_decodes_constant_rle_column():
    records = [{"constant": "same", "index": index} for index in range(256)]
    column = ColumnEncoder().encode([record["constant"] for record in records])
    assert column["t"] == EncodingType.RLE
    assert column["d"] == [["same", len(records)]]

    restored = decompress(compress(records), max_records=len(records))

    assert is_faithful(records, restored)


def test_public_reader_preserves_nested_value_from_legacy_rle_payload():
    nested_value = {"legacy": ["value"]}
    record_count = 3
    payload = {
        "s": {
            "s0": {
                "k": [["legacy"]],
                "c": [{"t": EncodingType.RLE, "d": [[nested_value, record_count]]}],
                "n": record_count,
            }
        },
        "o": [["s0", record_count]],
    }
    inner_payload, body = PayloadSerializer().serialize_with_body(payload)
    archive = serialize_v3_container(
        inner_payload,
        record_count=record_count,
        uncompressed_body_bytes=len(body),
    )

    restored = decompress(archive, max_records=record_count)

    assert is_faithful(restored, [{"legacy": nested_value} for _ in range(record_count)])
    assert all(record["legacy"] is restored[0]["legacy"] for record in restored)
