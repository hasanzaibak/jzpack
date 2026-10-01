import math
import struct

import pytest

from jzpack import InvalidFormatError, compress, decompress
from jzpack.analyzer import ColumnEncoder
from jzpack.chunks import serialize_v3_container
from jzpack.encoders import DeltaEncoder, DictionaryEncoder, EncodingType, RLEEncoder
from jzpack.errors import ResourceLimitError
from jzpack.serializer import PayloadSerializer
from tests.fidelity_oracle import is_faithful


def _v3_archive(payload, record_count):
    inner_payload, body = PayloadSerializer().serialize_with_body(payload)
    return serialize_v3_container(
        inner_payload,
        record_count=record_count,
        uncompressed_body_bytes=len(body),
    )


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([1e16, 1.0] * 60, id="float-cancellation"),
        pytest.param([True, 1] * 60, id="bool-and-int-rle"),
        pytest.param([1, 1.0] * 60, id="int-and-float-rle"),
        pytest.param([0.0, -0.0] * 60, id="signed-zero-rle"),
        pytest.param([[True], [1]] * 60, id="nested-bool-and-int-rle"),
        pytest.param([1, 2.0] * 60, id="mixed-int-and-float-delta"),
        pytest.param([-(2**63), 2**64 - 1] * 60, id="supported-int-boundaries"),
    ],
)
def test_default_codec_preserves_reproduced_values(values):
    records = [{"value": value} for value in values]
    restored = [record["value"] for record in decompress(compress(records))]

    assert is_faithful(restored, values)

    # These deliberately adversarial columns must take the existing lossless RAW path.
    assert ColumnEncoder().encode(values)["t"] == EncodingType.RAW


@pytest.mark.parametrize("value", [-(2**63) - 1, 2**64])
def test_integers_outside_supported_messagepack_range_are_rejected(value):
    with pytest.raises((OverflowError, TypeError)):
        compress([{"value": value}])


@pytest.mark.parametrize(
    ("values", "encoding"),
    [
        pytest.param(["ok"] * 120, EncodingType.RLE, id="rle-string-run"),
        pytest.param(list(range(120)), EncodingType.DELTA, id="delta-integers"),
        pytest.param(["abcde"[i % 5] for i in range(120)], EncodingType.DICTIONARY, id="dictionary-strings"),
        pytest.param([float(i) / 7 for i in range(120)], EncodingType.RAW, id="raw-floats"),
    ],
)
def test_generated_columns_exercise_default_encodings(values, encoding):
    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == encoding
    assert is_faithful(ColumnEncoder().decode(encoded), values)
    assert is_faithful([row["value"] for row in decompress(compress([{"value": value} for value in values]))], values)


@pytest.mark.parametrize("value", [True, 1, 0.0, -0.0, float("inf"), float("nan"), "text", b"bytes", None])
def test_rle_preserves_exact_type_and_float_bits(value):
    values = [value] * 120
    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == EncodingType.RLE
    assert is_faithful(ColumnEncoder().decode(encoded), values)
    assert is_faithful([row["value"] for row in decompress(compress([{"value": item} for item in values]))], values)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="not-a-list"),
        pytest.param([["value"]], id="pair-has-wrong-length"),
        pytest.param([("value", 1)], id="pair-is-not-a-wire-list"),
        pytest.param([["value", True]], id="boolean-count"),
        pytest.param([["value", 0]], id="zero-count"),
        pytest.param([["value", -1]], id="negative-count"),
    ],
)
def test_rle_decoder_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        RLEEncoder.decode(payload, max_output_size=100)


@pytest.mark.parametrize("limit", [True, -1, 1.5])
def test_rle_decoder_validates_limits_for_empty_payloads(limit):
    with pytest.raises(ValueError, match="output limit"):
        RLEEncoder.decode([], max_output_size=limit)


def test_rle_decoder_rejects_out_of_range_counts_before_limit_check():
    with pytest.raises(ValueError, match="RLE count"):
        RLEEncoder.decode([["value", 2**64]], max_output_size=100)


@pytest.mark.parametrize(
    ("base", "deltas"),
    [
        pytest.param(True, [1], id="boolean-base"),
        pytest.param("1", [1], id="text-base"),
        pytest.param(2**64, [], id="base-outside-msgpack-range"),
        pytest.param(1, None, id="deltas-not-a-list"),
        pytest.param(1, [True], id="boolean-delta"),
        pytest.param(1, ["1"], id="text-delta"),
        pytest.param(2**64 - 1, [1], id="decoded-value-outside-range"),
        pytest.param(-(2**63), [-1], id="decoded-value-below-range"),
        pytest.param(1, [2**64], id="delta-outside-msgpack-range"),
    ],
)
def test_delta_decoder_rejects_malformed_payloads(base, deltas):
    with pytest.raises(ValueError):
        DeltaEncoder.decode(base, deltas)


def test_legacy_float_delta_payloads_remain_decodable():
    # Older v3 writers emitted numeric float deltas. Reader compatibility keeps this payload valid.
    assert DeltaEncoder.decode(1, [1.5, -0.5]) == [1, 2.5, 2.0]


def test_public_reader_keeps_legacy_float_delta_payloads_decodable():
    count = 120
    payload = {
        "s": {
            "s0": {
                "k": [["value"]],
                "c": [{"t": EncodingType.DELTA, "b": 1e16, "d": [-1e16, 1e16] * 59 + [-1e16]}],
                "n": count,
            }
        },
        "o": [["s0", count]],
    }
    archive = _v3_archive(payload, count)
    expected = [{"value": value} for value in [1e16, 0.0] * 60]

    assert is_faithful(decompress(archive), expected)


def test_public_reader_wraps_malformed_column_fields_as_format_error():
    payload = {
        "s": {
            "s0": {
                "k": [["value"]],
                "c": [{"t": EncodingType.RAW, "d": [1], "extra": 2}],
                "n": 1,
            }
        },
        "o": [["s0", 1]],
    }

    with pytest.raises(InvalidFormatError):
        decompress(_v3_archive(payload, 1))


def test_rle_decoder_enforces_expansion_limit_before_allocating():
    with pytest.raises(ResourceLimitError):
        RLEEncoder.decode([["value", 101]], max_output_size=100)


def test_delta_decoder_enforces_expansion_limit_before_reconstruction():
    with pytest.raises(ResourceLimitError):
        DeltaEncoder.decode(0, list(range(100)), max_output_size=100)


@pytest.mark.parametrize("limit", [True, -1, 1.5])
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"t": EncodingType.RAW, "d": []}, id="empty-raw-column"),
        pytest.param({"t": EncodingType.RLE, "d": []}, id="empty-rle-column"),
        pytest.param({"t": EncodingType.DICTIONARY, "m": [], "d": []}, id="empty-dictionary-column"),
    ],
)
def test_column_decoder_validates_limits_consistently_for_empty_columns(payload, limit):
    with pytest.raises(ValueError, match="max_values"):
        ColumnEncoder().decode(payload, max_values=limit)


@pytest.mark.parametrize(
    "indices",
    [
        pytest.param(None, id="indices-not-a-list"),
        pytest.param([True], id="boolean-index"),
        pytest.param([-1], id="negative-index"),
        pytest.param([1], id="index-equals-dictionary-length"),
        pytest.param([0.0], id="float-index"),
    ],
)
def test_dictionary_decoder_rejects_malformed_indices(indices):
    with pytest.raises(ValueError):
        DictionaryEncoder.decode(["value"], indices)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"t": True, "d": []}, id="boolean-encoding-type"),
        pytest.param({"t": 99, "d": []}, id="unknown-encoding-type"),
        pytest.param({"t": EncodingType.RAW}, id="missing-required-values"),
        pytest.param({"t": EncodingType.RAW, "d": [], "extra": 1}, id="unexpected-field"),
    ],
)
def test_column_decoder_rejects_malformed_payload_shapes(payload):
    with pytest.raises(ValueError):
        ColumnEncoder().decode(payload)


def test_fidelity_oracle_distinguishes_python_equal_but_wire_distinct_values():
    assert not is_faithful(True, 1)
    assert not is_faithful(1, 1.0)
    assert not is_faithful(0.0, -0.0)
    assert is_faithful(float("nan"), float("nan"))
    assert is_faithful([{"v": -0.0}], [{"v": -0.0}])


def test_supported_nonfinite_float_bits_survive_raw_round_trip():
    values = [math.inf, -math.inf, float("nan"), -0.0]
    restored = decompress(compress([{"value": value} for value in values]))

    assert is_faithful([record["value"] for record in restored], values)
    assert struct.pack(">d", restored[3]["value"]) == struct.pack(">d", -0.0)


def test_missing_null_empty_and_dotted_paths_remain_distinct():
    records = [
        {},
        {"value": None},
        {"a.b": 1, "a": {"b": 2}},
        {"a": {"b": None}},
        {"nested": {}, "items": []},
    ] * 20

    assert is_faithful(decompress(compress(records)), records)


@pytest.mark.parametrize(
    "bits",
    [0x7FF8000000000042, 0xFFF8000000000001, 0x7FF0000000000001],
)
def test_nan_payload_bits_survive_rle_and_public_round_trip(bits):
    value = struct.unpack(">d", bits.to_bytes(8, "big"))[0]
    values = [value] * 120

    encoded = ColumnEncoder().encode(values)
    restored = decompress(compress([{"value": item} for item in values]))

    assert encoded["t"] == EncodingType.RLE
    assert is_faithful(ColumnEncoder().decode(encoded), values)
    assert is_faithful([row["value"] for row in restored], values)


def test_rle_does_not_merge_distinct_nan_payload_bits():
    nan_values = [
        struct.unpack(">d", bits.to_bytes(8, "big"))[0]
        for bits in (0x7FF8000000000001, 0x7FF8000000000002)
    ]
    values = nan_values * 60

    encoded = ColumnEncoder().encode(values)
    restored = decompress(compress([{"value": value} for value in values]))

    assert encoded["t"] == EncodingType.RAW
    assert is_faithful([row["value"] for row in restored], values)
