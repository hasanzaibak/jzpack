"""Behavioral checks for faster codec guards."""

import pytest

from jzpack.analyzer import ColumnEncoder
from jzpack.encoders import DeltaEncoder, EncodingType
from tests.fidelity_oracle import is_faithful


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        pytest.param(
            ["stable"] * 120,
            {"t": EncodingType.RLE, "d": [["stable", 120]]},
            id="rle-column",
        ),
        pytest.param(
            list(range(120)),
            {"t": EncodingType.DELTA, "b": 0, "d": [1] * 119},
            id="integer-delta-column",
        ),
    ],
)
def test_guard_optimizations_keep_valid_encoded_column_shape(values, expected):
    encoded = ColumnEncoder().encode(values)

    assert encoded == expected
    assert is_faithful(ColumnEncoder().decode(encoded), values)


def test_rle_guard_checks_nested_outlier_beyond_analyzer_sample():
    values = ["stable"] * 121 + [["nested"]]

    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == EncodingType.RAW
    assert is_faithful(ColumnEncoder().decode(encoded), values)


def test_delta_guard_checks_type_outlier_beyond_analyzer_sample():
    values = list(range(121)) + [120.0]

    assert not DeltaEncoder.can_encode(values)
    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == EncodingType.RAW
    assert is_faithful(ColumnEncoder().decode(encoded), values)


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([True, True], id="boolean-values"),
        pytest.param([0, 2**64], id="unsupported-value-at-tail"),
        pytest.param([-(2**63), 2**64 - 1], id="unsupported-delta-at-tail"),
    ],
)
def test_direct_delta_encoder_still_rejects_malformed_inputs(values):
    with pytest.raises(ValueError, match="supported integers and deltas"):
        DeltaEncoder.encode(values)


def test_delta_decoder_preserves_legacy_numeric_type_transitions():
    decoded = DeltaEncoder.decode(0, [0.5, 1, -1.5, 2])

    assert is_faithful(decoded, [0, 0.5, 1.5, 0.0, 2.0])
