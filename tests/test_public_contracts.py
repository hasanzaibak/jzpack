import io
from collections import UserDict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from jzpack import (
    InvalidFormatError,
    JZPackCompressor,
    JZPackError,
    ResourceLimitError,
    StreamingCompressor,
    UnsupportedVersionError,
    compress,
    decompress,
    iter_decompress,
)


def test_public_error_hierarchy_and_version_distinction() -> None:
    assert issubclass(InvalidFormatError, JZPackError)
    assert issubclass(UnsupportedVersionError, InvalidFormatError)
    assert issubclass(ResourceLimitError, JZPackError)

    with pytest.raises(InvalidFormatError):
        decompress(b"not a JZPK archive")
    with pytest.raises(UnsupportedVersionError):
        decompress(b"JZPK\xff" + b"\x00" * 100)


def test_mapping_list_generator_class_file_and_iterator_routes_agree(tmp_path: Path) -> None:
    records = [
        {"id": 1, "meta": {"region": "sp"}},
        {"id": 2, "meta": {"region": "ny"}},
    ]
    mapping = UserDict({"id": 1, "meta": {"region": "sp"}})
    expected = compress(records)
    compressor = JZPackCompressor()
    streamed = StreamingCompressor()
    streamed.add_batch(records)
    path = tmp_path / "contracts.jzpk"
    stream = io.BytesIO()

    assert compress(mapping) == compress([dict(mapping)])
    assert compressor.compress(records) == expected
    assert compressor.compress(record for record in records) == expected
    assert streamed.finalize() == expected
    assert compressor.compress_to_file(records, path) == len(expected)
    assert path.read_bytes() == expected
    assert compressor.decompress_from_file(path) == records
    assert compressor.compress_to_file(records, stream) == len(expected)
    stream.seek(0)
    assert compressor.decompress_from_file(stream) == records
    assert not stream.closed
    assert decompress(expected) == records
    assert list(iter_decompress(expected)) == records


def test_mapping_record_and_empty_iterable_have_distinct_row_counts() -> None:
    assert decompress(compress({})) == [{}]
    assert decompress(compress([])) == []


@pytest.mark.parametrize(
    "data",
    [
        123,
        "record",
        None,
        [123],
        [None],
        [{"id": 1}, None],
        [{1: "non-string top-level key"}],
        [{"nested": {1: "bad"}}],
    ],
)
def test_compress_rejects_non_mapping_records_and_non_string_keys(data: object) -> None:
    with pytest.raises(TypeError):
        compress(data)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", [1, 22])
def test_compression_level_inclusive_boundaries(level: int) -> None:
    records = [{"id": 1}]

    assert decompress(compress(records, level=level)) == records
    assert decompress(JZPackCompressor(compression_level=level).compress(records)) == records


@pytest.mark.parametrize("level", [0, 23, True, 1.5])
def test_compression_level_rejects_values_outside_the_documented_integer_range(level: object) -> None:
    with pytest.raises(ValueError, match="compression level"):
        compress([{"id": 1}], level=level)  # type: ignore[arg-type]


@pytest.mark.parametrize("keyword", ["max_records", "max_output_size"])
def test_decompress_limits_accept_the_exact_boundary_and_reject_one_below(keyword: str) -> None:
    records = [{"id": 1}, {"id": 2}]
    payload = compress(records)
    body_size = int.from_bytes(payload[40:48], "big")
    exact = 2 if keyword == "max_records" else body_size

    assert decompress(payload, **{keyword: exact}) == records
    with pytest.raises(ResourceLimitError, match=keyword):
        decompress(payload, **{keyword: exact - 1})


@pytest.mark.parametrize("keyword", ["max_records", "max_output_size"])
@pytest.mark.parametrize("limit", [True, -1, 1.5])
def test_decompress_limits_reject_bool_negative_and_non_integer_values(keyword: str, limit: object) -> None:
    with pytest.raises(ValueError, match=keyword):
        decompress(compress([{"id": 1}]), **{keyword: limit})


def test_empty_archive_accepts_zero_decompression_limits() -> None:
    assert decompress(compress([]), max_records=0, max_output_size=0) == []


def test_decompress_requires_bytes_like_input() -> None:
    for value in (None, "JZPK", object()):
        with pytest.raises(TypeError, match="bytes-like"):
            decompress(value)  # type: ignore[arg-type]


def test_reused_compressor_resets_after_success_and_failed_compression() -> None:
    compressor = JZPackCompressor()
    first = [{"old": "value"}]

    assert compressor.decompress(compressor.compress(first)) == first
    with pytest.raises(TypeError):
        compressor.compress([{"partial": 1}, None])

    replacement = [{"new": "value"}]
    assert compressor.decompress(compressor.compress(replacement)) == replacement
    assert compressor.decompress(compressor.compress([])) == []


def test_streaming_compressor_finalize_is_repeatable_and_clear_reuses_it() -> None:
    stream = StreamingCompressor()

    assert decompress(stream.finalize()) == []
    stream.add_record({"id": 1})
    first = stream.finalize()
    assert decompress(first) == [{"id": 1}]
    assert stream.finalize() == first

    stream.add_batch([{"id": 2}, {"other": True}])
    assert decompress(stream.finalize()) == [{"id": 1}, {"id": 2}, {"other": True}]

    stream.clear()
    assert decompress(stream.finalize()) == []
    stream.add_record({"after_clear": True})
    assert decompress(stream.finalize()) == [{"after_clear": True}]


def test_compress_does_not_mutate_mapping_inputs_or_consume_generator_twice() -> None:
    records = [{"id": 1, "nested": {"keep": [1, 2]}}, {"id": 2, "nested": {"keep": [3]}}]
    before = deepcopy(records)
    consumed: list[Mapping[str, Any]] = []

    def source() -> Iterable[Mapping[str, Any]]:
        for record in records:
            consumed.append(record)
            yield record

    payload = compress(source())

    assert records == before
    assert consumed == records
    assert decompress(payload) == records
