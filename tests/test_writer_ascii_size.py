"""Boundary contracts for the writer's allocation-free ASCII size path."""

from __future__ import annotations

import io

import msgpack
import pytest

from jzpack import ResourceLimitError, decompress, write_records
from jzpack.writer import _string_utf8_size


@pytest.mark.parametrize("length", [0, 1, 31, 32, 4095, 4096, 4097, 8192])
def test_ascii_utf8_size_matches_bytes_at_chunk_boundaries(length: int) -> None:
    value = "a" * length

    assert _string_utf8_size(value, length) == length
    if length:
        with pytest.raises(ResourceLimitError, match="max_record_bytes"):
            _string_utf8_size(value, length - 1)


@pytest.mark.parametrize(
    "value",
    [
        "é",
        "a" * 4095 + "é",
        "a" * 4096 + "é",
        "a" * 4095 + "🧪é",
    ],
)
def test_non_ascii_utf8_size_preserves_multibyte_chunk_boundaries(value: str) -> None:
    encoded_size = len(value.encode("utf-8"))

    assert _string_utf8_size(value, encoded_size) == encoded_size
    with pytest.raises(ResourceLimitError, match="max_record_bytes"):
        _string_utf8_size(value, encoded_size - 1)


@pytest.mark.parametrize("value", ["a" * 4095 + "\ud800", "a" * 4096 + "\ud800"])
def test_non_ascii_path_keeps_surrogate_encoding_errors(value: str) -> None:
    with pytest.raises(UnicodeEncodeError, match="surrogates not allowed"):
        _string_utf8_size(value, len(value))


def test_public_writer_ascii_size_limit_accepts_exact_messagepack_size() -> None:
    record = {"payload": "a" * 8192}
    serialized_size = len(msgpack.packb(record, use_bin_type=True))
    sink = io.BytesIO()

    assert write_records([record], sink, max_record_bytes=serialized_size) == len(sink.getvalue())
    assert decompress(sink.getvalue()) == [record]
    with pytest.raises(ResourceLimitError, match="max_record_bytes"):
        write_records([record], io.BytesIO(), max_record_bytes=serialized_size - 1)
