import struct

import msgpack

from jzpack.analyzer import (
    _MAX_CACHED_RLE_RUNS,
    ColumnAnalyzer,
    ColumnEncoder,
    EncodingThresholds,
)
from jzpack.encoders import EncodingType, RLEEncoder
from tests.fidelity_oracle import is_faithful


def _values_with_runs(row_count: int, run_count: int) -> list[str]:
    base_size, remainder = divmod(row_count, run_count)
    return [
        f"run-{run_index}"
        for run_index in range(run_count)
        for _ in range(base_size + (run_index < remainder))
    ]


def test_rle_run_ratio_accepts_the_limit_and_rejects_the_next_run():
    at_limit = _values_with_runs(120, 12)
    over_limit = _values_with_runs(120, 13)

    encoded = ColumnEncoder().encode(at_limit)
    thresholds = EncodingThresholds()
    thresholds.DICTIONARY_MAX_CARDINALITY = 0.05
    analyzer = ColumnAnalyzer(thresholds)

    assert encoded["t"] == EncodingType.RLE
    assert len(encoded["d"]) == 12
    assert analyzer._is_rle_suitable(at_limit)
    assert not analyzer._is_rle_suitable(over_limit)
    assert analyzer.determine_encoding(over_limit) == EncodingType.RAW


def test_reused_rle_runs_preserve_float_bits_and_exact_round_trips():
    nan_one = struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0]
    nan_two = struct.unpack(">d", bytes.fromhex("fff8000000000042"))[0]
    cases = [
        ([0.0] * 60 + [-0.0] * 60, [0x0000000000000000, 0x8000000000000000]),
        ([nan_one] * 60 + [nan_two] * 60, [0x7FF8000000000001, 0xFFF8000000000042]),
    ]

    for values, expected_bits in cases:
        encoded = ColumnEncoder().encode(values)
        restored = ColumnEncoder().decode(encoded)

        assert encoded["t"] == EncodingType.RLE
        assert [struct.unpack(">Q", struct.pack(">d", run[0]))[0] for run in encoded["d"]] == expected_bits
        assert is_faithful(restored, values)

    identical_nan_values = [nan_one] * 120
    identical_nan_runs = ColumnEncoder().encode(identical_nan_values)
    assert len(identical_nan_runs["d"]) == 1
    assert is_faithful(ColumnEncoder().decode(identical_nan_runs), identical_nan_values)


def test_unsupported_value_falls_back_without_comparing_it():
    class EqualityTrap:
        def __eq__(self, other):
            raise AssertionError("unsupported values must not be compared")

    values = ["stable"] * 100 + [EqualityTrap()] + ["stable"] * 19
    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == EncodingType.RAW
    assert encoded["d"] is values


def test_rle_cache_keeps_list_subclasses_on_the_existing_encoder_path(monkeypatch):
    class Values(list):
        pass

    values = Values(["stable"] * 120)
    original_encode = RLEEncoder.encode
    calls = 0

    def record_encode(items):
        nonlocal calls
        calls += 1
        return original_encode(items)

    monkeypatch.setattr(RLEEncoder, "encode", staticmethod(record_encode))
    encoded = ColumnEncoder().encode(values)

    assert encoded["t"] == EncodingType.RLE
    assert calls == 1


def test_reused_rle_column_keeps_wire_bytes_and_decodes_exactly():
    values = ["stable"] * 120
    encoded = ColumnEncoder().encode(values)
    wire_bytes = msgpack.packb(encoded, use_bin_type=True)

    assert wire_bytes.hex() == "82a17401a1649192a6737461626c6578"
    assert is_faithful(ColumnEncoder().decode(encoded), values)


def test_run_cache_abandons_collection_after_its_absolute_cap(monkeypatch):
    original_encode = RLEEncoder.encode
    calls = 0

    def record_encode(values):
        nonlocal calls
        calls += 1
        return original_encode(values)

    monkeypatch.setattr(RLEEncoder, "encode", staticmethod(record_encode))

    for run_count, expected_encoder_calls in (
        (_MAX_CACHED_RLE_RUNS, 0),
        (_MAX_CACHED_RLE_RUNS + 1, 1),
    ):
        values = _values_with_runs(3_000, run_count)
        before_calls = calls
        encoded = ColumnEncoder().encode(values)
        assert encoded["t"] == EncodingType.RLE
        assert calls - before_calls == expected_encoder_calls

        expected = {"t": EncodingType.RLE, "d": original_encode(values)}
        assert msgpack.packb(encoded, use_bin_type=True) == msgpack.packb(expected, use_bin_type=True)
        assert is_faithful(ColumnEncoder().decode(encoded), values)
