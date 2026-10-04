"""Contract tests for the direct-to-sink v3 writer."""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Any

import msgpack
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import jzpack.writer as writer_module
from jzpack import (
    ChunkError,
    ChunkRecords,
    JZPackError,
    ResourceLimitError,
    compress,
    decompress,
    iter_decompress,
    iter_decompress_recover,
    write_records,
)
from jzpack.serializer import PayloadSerializer
from tests.fidelity_oracle import is_faithful


def _write(records: object, **kwargs: Any) -> bytes:
    buffer = io.BytesIO()
    write_records(records, buffer, **kwargs)
    return buffer.getvalue()


def _chunks(data: bytes) -> list[ChunkRecords]:
    events = list(iter_decompress_recover(data))
    assert all(isinstance(event, ChunkRecords) for event in events)
    assert not any(isinstance(event, ChunkError) for event in events)
    return [event for event in events if isinstance(event, ChunkRecords)]


def _v3_chunk_sizes(data: bytes) -> tuple[int, int]:
    assert data[:4] == b"JZPK" and data[4] == 3
    assert data[16:20] == b"CHNK"
    return int.from_bytes(data[40:48], "big"), int.from_bytes(data[56:64], "big")


def test_single_chunk_writer_matches_existing_v3_output_and_exact_values() -> None:
    nan = struct.unpack(">d", bytes.fromhex("7ff8000000000042"))[0]
    records = [
        {"literal.dot": {"bytes": b"\x00\xff", "nothing": None}, "id": -(1 << 63), "float": -0.0},
        {"literal.dot": {}, "id": (1 << 64) - 1, "float": nan, "optional": [True, "text"]},
    ]

    encoded = _write(records)

    assert encoded == compress(records)
    actual = decompress(encoded)
    assert is_faithful(actual, records)


def test_empty_input_has_a_valid_empty_container() -> None:
    encoded = _write(iter(()))

    assert len(encoded) == 72
    assert decompress(encoded) == []
    assert list(iter_decompress(encoded)) == []


def test_alternating_schemas_and_multiple_chunks_preserve_order() -> None:
    records = [{"left": index} if index % 2 == 0 else {"right": index} for index in range(7)]

    encoded = _write(records, max_chunk_records=3)
    chunks = _chunks(encoded)

    assert [len(chunk.records) for chunk in chunks] == [3, 3, 1]
    assert [record for chunk in chunks for record in chunk.records] == records
    assert decompress(encoded) == records


def test_deterministic_output_and_schema_caps_split_before_crossing() -> None:
    records = [{"a": 1}, {"b": 2}, {"a": 3}, {"b": 4}]

    first = _write(records, max_schemas=1)
    second = _write(records, max_schemas=1)

    assert first == second
    assert [len(chunk.records) for chunk in _chunks(first)] == [1, 1, 1, 1]
    assert decompress(first) == records


@pytest.mark.parametrize(
    "record",
    [
        {"a": None},
        {"é": "🌞"},
        {"a": [1, 2, 3]},
        {"a": -(1 << 63)},
        {"a": (1 << 64) - 1},
        {"a": b"\x00\xff"},
        {"a": {"b": {}}},
    ],
)
def test_record_messagepack_size_boundaries_match_msgpack(record: dict[str, Any]) -> None:
    serialized_size = len(msgpack.packb(record, use_bin_type=True))

    assert decompress(
        _write(
            [record],
            target_chunk_input_bytes=1,
            max_record_bytes=serialized_size,
            max_chunk_input_bytes=serialized_size,
        )
    ) == [record]
    with pytest.raises(ResourceLimitError, match="max_record_bytes"):
        _write(
            [record],
            target_chunk_input_bytes=1,
            max_record_bytes=serialized_size - 1,
            max_chunk_input_bytes=serialized_size,
        )


def _assert_record_size_matches_msgpack(record: dict[str, Any]) -> None:
    serialized_size = len(msgpack.packb(record, use_bin_type=True))
    options = {
        "target_chunk_input_bytes": 1,
        "max_record_bytes": serialized_size,
        "max_chunk_input_bytes": serialized_size,
        "fast": True,
    }
    assert decompress(_write([record], **options)) == [record]
    if serialized_size > 1:
        with pytest.raises(ResourceLimitError, match="max_record_bytes"):
            _write([record], **{**options, "max_record_bytes": serialized_size - 1})


@pytest.mark.parametrize(
    "value",
    [
        -33,
        -32,
        -129,
        -128,
        127,
        128,
        255,
        256,
        -32_769,
        -32_768,
        65_535,
        65_536,
        -(1 << 31) - 1,
        -(1 << 31),
        (1 << 32) - 1,
        1 << 32,
        -(1 << 63),
        (1 << 63) - 1,
        1 << 63,
        (1 << 64) - 1,
    ],
)
def test_messagepack_signed_and_unsigned_integer_width_boundaries(value: int) -> None:
    _assert_record_size_matches_msgpack({"value": value})


@pytest.mark.parametrize("length", [0, 31, 32, 255, 256, 65_535, 65_536])
def test_messagepack_string_header_boundaries(length: int) -> None:
    _assert_record_size_matches_msgpack({"value": "x" * length})


@pytest.mark.parametrize("length", [0, 255, 256, 65_535, 65_536])
def test_messagepack_binary_header_boundaries(length: int) -> None:
    _assert_record_size_matches_msgpack({"value": b"x" * length})


@pytest.mark.parametrize("length", [16, 128, 32_768])
def test_utf8_byte_count_matches_multibyte_string_header_boundaries(length: int) -> None:
    _assert_record_size_matches_msgpack({"value": "é" * length})


@pytest.mark.parametrize("length", [15, 16])
def test_messagepack_map_and_array_header_boundaries(length: int) -> None:
    mapping = {f"key-{index}": index for index in range(length)}
    _assert_record_size_matches_msgpack({"mapping": mapping})
    _assert_record_size_matches_msgpack({"array": [None] * length})


@pytest.mark.parametrize("record", [{}, {"": {}}, {"x": {"": {}}}, {"": {"": {}}}])
def test_empty_maps_and_empty_nested_path_components_match_msgpack(record: dict[str, Any]) -> None:
    _assert_record_size_matches_msgpack(record)


def test_nested_lists_match_messagepack_size_and_round_trip() -> None:
    _assert_record_size_matches_msgpack({"nested": [[[None, 1], ["é"]], []]})


class _MessagePackIntSubclass(int):
    pass


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        -33,
        -32,
        127,
        128,
        -129,
        -128,
        255,
        256,
        -32_769,
        -32_768,
        65_535,
        65_536,
        -(1 << 31) - 1,
        -(1 << 31),
        (1 << 32) - 1,
        1 << 32,
        -(1 << 63),
        (1 << 63) - 1,
        1 << 63,
        (1 << 64) - 1,
        _MessagePackIntSubclass(128),
        0.0,
        -0.0,
        float("inf"),
        float("nan"),
        "",
        "é",
        b"",
        b"\x00\xff",
    ],
)
def test_encoded_payload_scalar_size_matches_msgpack_and_exact_cap(value: object) -> None:
    payload = {"outer": [{"value": value}]}
    expected_size = len(msgpack.packb(payload, use_bin_type=True))

    assert writer_module._messagepack_size(payload, expected_size, max_depth=8) == expected_size
    with pytest.raises(ResourceLimitError, match="max_chunk_uncompressed_bytes"):
        writer_module._messagepack_size(payload, expected_size - 1, max_depth=8)


def test_encoded_payload_scalar_size_falls_back_for_unsupported_subclasses() -> None:
    class FloatSubclass(float):
        pass

    with pytest.raises(TypeError, match="does not support values of type FloatSubclass"):
        writer_module._messagepack_size({"value": FloatSubclass(1.0)}, 100, max_depth=8)


@pytest.mark.parametrize(
    "record",
    [
        {"items": [{"x": 1}, {"x": 2}]},
        {"items": [{"outer": {"inner": [{"é": 1}, {}]}}]},
        {"items": [[], [{"": {"value": None}}]]},
    ],
)
def test_array_of_objects_and_nested_empty_maps_round_trip(record: dict[str, Any]) -> None:
    assert decompress(_write([record])) == [record]


def test_nested_non_ascii_array_key_obeys_exact_record_byte_boundary() -> None:
    record = {"items": [{"é": 1}]}
    size = len(msgpack.packb(record, use_bin_type=True))

    assert decompress(_write([record], max_record_bytes=size)) == [record]
    with pytest.raises(ResourceLimitError, match="max_record_bytes"):
        _write([record], max_record_bytes=size - 1)


_writer_scalar = (
    st.none()
    | st.booleans()
    | st.sampled_from([-(1 << 63), -(1 << 32), -129, -32, 127, 128, (1 << 32) - 1, (1 << 64) - 1])
    | st.integers(-1000, 1000)
    | st.floats(width=64, allow_nan=True, allow_infinity=True)
    | st.text(max_size=8)
    | st.binary(max_size=8)
)
_writer_value = st.recursive(
    _writer_scalar,
    lambda children: st.lists(children, max_size=3)
    | st.dictionaries(st.text(max_size=5), children, max_size=3),
    max_leaves=12,
)
_writer_records = st.lists(
    st.dictionaries(st.text(max_size=5), _writer_value, max_size=4),
    max_size=12,
)


@settings(max_examples=35, deadline=None)
@given(_writer_records)
def test_generated_writer_records_preserve_exact_values_across_small_chunks(
    records: list[dict[str, Any]],
) -> None:
    actual = decompress(_write(records, max_chunk_records=3, target_chunk_input_bytes=256))

    assert is_faithful(actual, records)


def test_soft_target_and_hard_single_record_chunk_input_boundary() -> None:
    record = {"field": "value"}
    size = len(msgpack.packb(record, use_bin_type=True))
    encoded = _write(
        [record],
        target_chunk_input_bytes=size - 1,
        max_record_bytes=size,
        max_chunk_input_bytes=size,
    )
    assert decompress(encoded) == [record]

    with pytest.raises(ResourceLimitError, match="max_chunk_input_bytes"):
        _write(
            [record],
            target_chunk_input_bytes=size - 1,
            max_record_bytes=size,
            max_chunk_input_bytes=size - 1,
        )


def test_input_byte_target_splits_at_exact_record_sum() -> None:
    first = {"a": 1}
    second = {"a": 2}
    third = {"a": 3}
    size = len(msgpack.packb(first, use_bin_type=True))

    encoded = _write([first, second, third], target_chunk_input_bytes=size * 2)

    assert [len(chunk.records) for chunk in _chunks(encoded)] == [2, 1]
    assert decompress(encoded) == [first, second, third]


def test_encoded_body_and_payload_limits_accept_exact_boundary_and_reject_minus_one() -> None:
    record = {"numbers": list(range(20)), "name": "limit"}
    reference = compress([record])
    body_size, payload_size = _v3_chunk_sizes(reference)

    assert decompress(_write([record], max_chunk_uncompressed_bytes=body_size)) == [record]
    with pytest.raises(ResourceLimitError, match="max_chunk_uncompressed_bytes"):
        _write([record], max_chunk_uncompressed_bytes=body_size - 1)

    assert decompress(_write([record], max_chunk_payload_bytes=payload_size)) == [record]
    with pytest.raises(ResourceLimitError, match="max_chunk_payload_bytes"):
        _write([record], max_chunk_payload_bytes=payload_size - 1)


def test_oversized_encoded_chunk_leaves_stream_without_terminal_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_serialize(self: PayloadSerializer, payload: dict) -> tuple[bytes, bytes]:
        pytest.fail("oversized encoded body reached serializer allocation")

    monkeypatch.setattr(PayloadSerializer, "serialize_with_body", unexpected_serialize)
    sink = io.BytesIO()
    with pytest.raises(ResourceLimitError, match="max_chunk_uncompressed_bytes"):
        write_records([{"value": list(range(30))}], sink, max_chunk_uncompressed_bytes=5)

    assert sink.getvalue().startswith(b"JZPK\x03")
    with pytest.raises(JZPackError):
        list(iter_decompress(sink.getvalue()))


def test_rle_cannot_bypass_per_chunk_row_cap() -> None:
    rows = [{} for _ in range(5)]

    encoded = _write(rows, max_chunk_records=2)

    assert [len(chunk.records) for chunk in _chunks(encoded)] == [2, 2, 1]
    assert decompress(encoded) == rows


def test_aggregate_node_limit_splits_at_boundary() -> None:
    rows = [{"x": 1}, {"x": 2}, {"x": 3}]
    # Each row has a root map, a key, and a scalar value.
    encoded = _write(rows, max_nodes=6)

    assert [len(chunk.records) for chunk in _chunks(encoded)] == [2, 1]
    assert decompress(encoded) == rows


def test_single_record_node_limit_accepts_exact_boundary_and_rejects_minus_one() -> None:
    row = {"x": 1}  # root mapping, key, scalar value

    assert decompress(_write([row], max_nodes=3)) == [row]
    with pytest.raises(ResourceLimitError, match="max_nodes"):
        _write([row], max_nodes=2)


def test_path_count_and_utf8_byte_budgets_count_paths_across_schema_definitions() -> None:
    rows = [{"a": 1}, {"b": 2}]
    exact = _write(rows, max_schemas=2, max_paths=2, max_path_bytes=2)
    assert [len(chunk.records) for chunk in _chunks(exact)] == [2]

    path_count_limited = _write(rows, max_schemas=2, max_paths=1, max_path_bytes=2)
    assert [len(chunk.records) for chunk in _chunks(path_count_limited)] == [1, 1]

    path_bytes_limited = _write(rows, max_schemas=2, max_paths=2, max_path_bytes=1)
    assert [len(chunk.records) for chunk in _chunks(path_bytes_limited)] == [1, 1]


def test_repeated_paths_in_distinct_schemas_count_toward_aggregate_caps() -> None:
    rows = [{"a": 1}, {"a": 2, "b": 3}]

    exact = _write(rows, max_schemas=2, max_paths=3, max_path_bytes=3)
    assert [len(chunk.records) for chunk in _chunks(exact)] == [2]

    path_count_limited = _write(rows, max_schemas=2, max_paths=2, max_path_bytes=3)
    assert [len(chunk.records) for chunk in _chunks(path_count_limited)] == [1, 1]

    path_bytes_limited = _write(rows, max_schemas=2, max_paths=3, max_path_bytes=2)
    assert [len(chunk.records) for chunk in _chunks(path_bytes_limited)] == [1, 1]


def test_empty_string_nested_key_still_counts_as_a_path() -> None:
    rows = [{"": {}}, {"x": 1}]

    encoded = _write(rows, max_paths=1, max_path_bytes=1)

    assert [len(chunk.records) for chunk in _chunks(encoded)] == [1, 1]
    assert decompress(encoded) == rows


def test_utf8_path_byte_limit_is_checked_before_snapshot_retention() -> None:
    with pytest.raises(ResourceLimitError, match="max_path_bytes"):
        _write([{"é": 1}], max_path_bytes=1)
    assert decompress(_write([{"é": 1}], max_path_bytes=2)) == [{"é": 1}]


def test_container_depth_limit_counts_root_and_nested_dict_or_list_only() -> None:
    assert decompress(_write([{"x": 1}], max_depth=1)) == [{"x": 1}]
    with pytest.raises(ResourceLimitError, match="max_depth"):
        _write([{"x": {"y": 1}}], max_depth=1)
    with pytest.raises(ResourceLimitError, match="max_depth"):
        _write([{"x": [[1]]}], max_depth=2)
    assert decompress(_write([{"x": [[1]]}], max_depth=3)) == [{"x": [[1]]}]


def test_fused_preflight_accepts_exact_nested_utf8_limits_and_rejects_one_less() -> None:
    record = {"é": {"": []}}
    record_bytes = len(msgpack.packb(record, use_bin_type=True))
    exact = {
        "target_chunk_input_bytes": 1,
        "max_record_bytes": record_bytes,
        "max_chunk_input_bytes": record_bytes,
        "max_nodes": 5,  # root, two keys, nested mapping, list
        "max_depth": 3,
        "max_paths": 1,
        "max_path_bytes": 2,  # UTF-8 bytes for "é" and the empty component
    }

    assert is_faithful(decompress(_write([record], **exact)), [record])
    for limit, message in (
        ("max_record_bytes", "max_record_bytes"),
        ("max_chunk_input_bytes", "max_chunk_input_bytes"),
        ("max_nodes", "max_nodes"),
        ("max_depth", "max_depth"),
        ("max_path_bytes", "max_path_bytes"),
    ):
        with pytest.raises(ResourceLimitError, match=message):
            _write([record], **{**exact, limit: exact[limit] - 1})

    empty_nested = {"é": {}}
    assert decompress(_write([empty_nested], max_paths=1, max_path_bytes=2)) == [empty_nested]
    with pytest.raises(ResourceLimitError, match="max_path_bytes"):
        _write([empty_nested], max_paths=1, max_path_bytes=1)

    two_paths = {"é": {"": []}, "a": 1}
    assert decompress(_write([two_paths], max_paths=2, max_path_bytes=3)) == [two_paths]
    with pytest.raises(ResourceLimitError, match="max_paths"):
        _write([two_paths], max_paths=1, max_path_bytes=3)


def test_fused_preflight_preserves_signed_zero_and_nan_payload_bits() -> None:
    values = [
        struct.unpack(">d", bytes.fromhex(bits))[0]
        for bits in (
            "8000000000000000",  # negative zero
            "7ff8000000000042",  # quiet NaN with a non-default payload
            "7ff0000000000000",  # positive infinity
        )
    ]
    records = [{"nested": {"values": values, "empty": {}}}, {}]

    assert is_faithful(decompress(_write(records, max_chunk_records=1)), records)


def test_reused_generator_record_is_snapshotted_before_the_next_yield() -> None:
    float_values = [
        struct.unpack(">d", bytes.fromhex(bits))[0]
        for bits in (
            "8000000000000000",
            "7ff8000000000001",
            "7ff8000000000002",
            "7ff0000000000000",
        )
    ]
    reused: dict[str, Any] = {"nested": {"value": 0.0, "empty": {}}, "items": [{"value": 0}]}
    expected: list[dict[str, Any]] = []

    def generate() -> Any:
        for index, value in enumerate(float_values):
            reused["nested"]["value"] = value
            reused["items"][0]["value"] = index
            expected.append(
                {"nested": {"value": value, "empty": {}}, "items": [{"value": index}]}
            )
            yield reused

    actual = decompress(_write(generate(), max_chunk_records=2))
    assert is_faithful(actual, expected)


def test_invalid_next_record_fails_before_flushing_the_prepared_chunk() -> None:
    sink = io.BytesIO()

    with pytest.raises(TypeError, match="does not support values of type object"):
        write_records(
            [{"id": 1}, {"outer": {"valid": [1, 2], "late": object()}}],
            sink,
            max_chunk_records=1,
        )

    assert len(sink.getvalue()) == 16
    assert sink.getvalue().startswith(b"JZPK\x03")


def test_late_type_error_keeps_precedence_after_chunk_input_crossing() -> None:
    sink = io.BytesIO()

    with pytest.raises(TypeError, match="does not support values of type object"):
        write_records(
            [{"id": 1}, {"id": 2, "bad": object()}],
            sink,
            target_chunk_input_bytes=1,
            max_chunk_input_bytes=7,
            max_chunk_records=1,
        )

    assert len(sink.getvalue()) == 16
    assert sink.getvalue().startswith(b"JZPK\x03")


def test_late_depth_error_keeps_precedence_after_chunk_input_crossing() -> None:
    sink = io.BytesIO()

    with pytest.raises(ResourceLimitError, match="max_depth"):
        write_records(
            [{"id": 1}, {"id": 2, "nested": [[1]]}],
            sink,
            target_chunk_input_bytes=1,
            max_chunk_input_bytes=7,
            max_chunk_records=1,
            max_depth=1,
        )

    assert len(sink.getvalue()) == 16
    assert sink.getvalue().startswith(b"JZPK\x03")


def test_late_cycle_error_keeps_precedence_after_chunk_input_crossing() -> None:
    cyclic: dict[str, Any] = {"id": 2}
    cyclic["self"] = cyclic
    sink = io.BytesIO()

    with pytest.raises(ValueError, match="cyclic"):
        write_records(
            [{"id": 1}, cyclic],
            sink,
            target_chunk_input_bytes=1,
            max_chunk_input_bytes=7,
            max_chunk_records=1,
        )

    assert len(sink.getvalue()) == 16
    assert sink.getvalue().startswith(b"JZPK\x03")


@pytest.mark.parametrize(
    "record",
    [
        {1: "non-string key"},
        {"tuple": (1, 2)},
        {"object": object()},
        {"large_int": 1 << 64},
    ],
)
def test_unsupported_record_types_are_rejected(record: dict[Any, Any]) -> None:
    with pytest.raises(TypeError):
        _write([record])


def test_container_subclasses_are_rejected() -> None:
    class CustomDict(dict):
        pass

    class CustomList(list):
        pass

    with pytest.raises(TypeError, match="built-in dict"):
        _write([CustomDict(value=1)])
    with pytest.raises(TypeError, match="does not support"):
        _write([{"value": CustomList([1])}])


def test_cyclic_record_is_rejected_before_its_chunk_is_emitted() -> None:
    record: dict[str, Any] = {}
    record["self"] = record
    sink = io.BytesIO()

    with pytest.raises(ValueError, match="cyclic"):
        write_records([record], sink)

    assert sink.getvalue().startswith(b"JZPK\x03")
    with pytest.raises(JZPackError):
        list(iter_decompress(sink.getvalue()))


def test_cycle_in_next_record_does_not_flush_pending_chunk_or_write_footer() -> None:
    cyclic: dict[str, Any] = {"valid": [1, 2]}
    cyclic["self"] = cyclic
    sink = io.BytesIO()

    with pytest.raises(ValueError, match="cyclic"):
        write_records([{"id": 1}, cyclic], sink, max_chunk_records=1)

    assert len(sink.getvalue()) == 16
    assert sink.getvalue().startswith(b"JZPK\x03")
    with pytest.raises(JZPackError):
        list(iter_decompress(sink.getvalue()))


def test_nonseekable_sink_retries_positive_short_writes_without_closing() -> None:
    class ShortWriter:
        def __init__(self) -> None:
            self.data = bytearray()
            self.calls = 0
            self.closed = False

        def write(self, data: memoryview) -> int:
            assert isinstance(data, memoryview)
            self.calls += 1
            count = min(3, len(data))
            self.data.extend(data[:count])
            return count

    sink = ShortWriter()
    records = [{"id": index} for index in range(4)]

    written = write_records(records, sink, max_chunk_records=2)

    encoded = bytes(sink.data)
    assert written == len(encoded)
    assert sink.calls > 4
    assert not sink.closed
    assert decompress(encoded) == records


@pytest.mark.parametrize(
    ("result", "error"),
    [(None, TypeError), (0, OSError), (-1, OSError), (True, TypeError), (1.5, TypeError)],
)
def test_invalid_sink_write_counts_are_rejected(result: object, error: type[Exception]) -> None:
    class InvalidWriter:
        def write(self, data: memoryview) -> object:
            return result

    with pytest.raises(error):
        write_records([], InvalidWriter())


def test_write_count_larger_than_remaining_buffer_is_rejected() -> None:
    class OverWriter:
        def write(self, data: memoryview) -> int:
            return len(data) + 1

    with pytest.raises(OSError, match="invalid byte count"):
        write_records([], OverWriter())


def test_generator_failure_after_an_emitted_chunk_leaves_no_footer() -> None:
    sink = io.BytesIO()

    def fail_after_two() -> Any:
        yield {"id": 1}
        yield {"id": 2}
        raise RuntimeError("generator failed")

    with pytest.raises(RuntimeError, match="generator failed"):
        write_records(fail_after_two(), sink, max_chunk_records=1)

    assert sink.getvalue().startswith(b"JZPK\x03")
    with pytest.raises(JZPackError):
        list(iter_decompress(sink.getvalue()))


def test_stream_cancellation_after_an_emitted_chunk_leaves_no_footer() -> None:
    sink = io.BytesIO()

    def cancel_after_two() -> Any:
        yield {"id": 1}
        yield {"id": 2}
        raise KeyboardInterrupt("cancelled")

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        write_records(cancel_after_two(), sink, max_chunk_records=1)

    assert sink.getvalue().startswith(b"JZPK\x03")
    with pytest.raises(JZPackError):
        list(iter_decompress(sink.getvalue()))


def test_sink_backpressure_allows_only_one_bounded_lookahead_record() -> None:
    produced = 0
    observed_at_chunk_write: list[int] = []

    def generate() -> Any:
        nonlocal produced
        for index in range(3):
            produced += 1
            yield {"id": index}

    class ObservingSink:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, data: memoryview) -> int:
            if data[:4] == b"CHNK":
                observed_at_chunk_write.append(produced)
            self.data.extend(data)
            return len(data)

    sink = ObservingSink()

    write_records(generate(), sink, max_chunk_records=1)

    assert observed_at_chunk_write == [2, 3, 3]
    assert decompress(bytes(sink.data)) == [{"id": 0}, {"id": 1}, {"id": 2}]


def test_path_write_replaces_atomically_and_returns_size(tmp_path: Path) -> None:
    records = [{"id": index} for index in range(4)]
    destination = tmp_path / "records.jzpk"
    destination.write_bytes(compress([{"old": True}]))

    written = write_records(records, destination, max_chunk_records=2)

    assert written == destination.stat().st_size
    assert decompress(destination.read_bytes()) == records
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["flush", "fsync", "replace", "write", "generator", "interrupt"])
def test_path_failures_preserve_existing_destination_and_remove_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    destination = tmp_path / "records.jzpk"
    previous = compress([{"old": True}])
    destination.write_bytes(previous)

    if failure == "flush":
        def fail_flush(_: object) -> None:
            raise OSError("injected flush failure")

        monkeypatch.setattr(writer_module, "_flush_stream", fail_flush)
        expected_error: type[BaseException] = OSError
        expected_message = "injected flush failure"
        records: object = [{"new": True}]
    elif failure == "fsync":
        def fail_fsync(_: int) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(writer_module.os, "fsync", fail_fsync)
        expected_error = OSError
        expected_message = "injected fsync failure"
        records = [{"new": True}]
    elif failure == "replace":
        def fail_replace(_: str, __: str) -> None:
            raise OSError("injected replace failure")

        monkeypatch.setattr(writer_module.os, "replace", fail_replace)
        expected_error = OSError
        expected_message = "injected replace failure"
        records = [{"new": True}]
    elif failure == "write":
        original_write_all = writer_module._write_all

        def fail_chunk_header(stream: object, data: bytes) -> int:
            if data.startswith(b"CHNK"):
                raise OSError("injected path write failure")
            return original_write_all(stream, data)

        monkeypatch.setattr(writer_module, "_write_all", fail_chunk_header)
        expected_error = OSError
        expected_message = "injected path write failure"
        records = [{"new": True}]
    else:
        def fail_after_chunk() -> Any:
            yield {"new": 1}
            yield {"new": 2}
            if failure == "interrupt":
                raise KeyboardInterrupt("injected path cancellation")
            raise RuntimeError("injected generator failure")

        expected_error = KeyboardInterrupt if failure == "interrupt" else RuntimeError
        expected_message = "injected path cancellation" if failure == "interrupt" else "injected generator failure"
        records = fail_after_chunk()

    with pytest.raises(expected_error, match=expected_message):
        write_records(records, destination, max_chunk_records=1)

    assert destination.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [destination]


def test_text_stream_and_invalid_limits_are_rejected() -> None:
    with pytest.raises(TypeError, match="binary file-like"):
        write_records([], io.StringIO())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        write_records([], io.BytesIO(), max_chunk_records=0)
    with pytest.raises(ValueError, match="cannot exceed"):
        write_records([], io.BytesIO(), target_chunk_input_bytes=5, max_chunk_input_bytes=4)
    with pytest.raises(ValueError, match="cannot exceed"):
        write_records([], io.BytesIO(), max_depth=129)


def test_failed_stream_writer_does_not_flush_or_close_caller_sink() -> None:
    class FailWriter:
        def __init__(self) -> None:
            self.data = bytearray()
            self.closed = False

        def write(self, data: memoryview) -> int:
            if len(self.data) > 16:
                raise OSError("injected write failure")
            count = min(8, len(data))
            self.data.extend(data[:count])
            return count

    sink = FailWriter()
    with pytest.raises(OSError, match="injected write failure"):
        write_records([{"value": "x" * 100}], sink)

    assert sink.data.startswith(b"JZPK\x03")
    assert not sink.closed
    with pytest.raises(JZPackError):
        list(iter_decompress(bytes(sink.data)))
