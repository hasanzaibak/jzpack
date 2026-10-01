import io
from dataclasses import dataclass
from pathlib import Path

import pytest

from jzpack import (
    ChunkError,
    ChunkRecords,
    InvalidFormatError,
    JZPackCompressor,
    ResourceLimitError,
    UnsupportedVersionError,
    compress,
    decompress,
    iter_decompress,
    iter_decompress_recover,
)
from jzpack.chunks import _crc32c
from jzpack.serializer import CompressionEngine, PayloadSerializer


@dataclass(frozen=True)
class V3Fixture:
    payload: bytes
    chunk_offsets: tuple[int, ...]
    footer_offset: int


def build_v3(chunks: list[list[dict[str, object]]]) -> V3Fixture:
    """Build deterministic multi-chunk v3 conformance bytes.

    The production writer emits one chunk per ``compress`` call; this helper
    composes those internal v2 chunk payloads to exercise the multi-chunk reader.
    """

    encoded_chunks = []
    totals = [0, 0, 0, 0, 0]
    for sequence, records in enumerate(chunks):
        assert records
        inner = _extract_inner_v2(compress(records))
        body = PayloadSerializer().deserialize_with_body(inner)[1]
        frame = inner[5:]
        encoded_chunks.append(_chunk(sequence, len(records), len(body), len(frame), inner))
        totals[0] += len(records)
        totals[1] += len(body)
        totals[2] += len(frame)
        totals[3] += len(inner)
        totals[4] += 1

    outer = _record(b"JZPK\x03\x00" + _u16(16) + _u32(0), _OUTER_CRC_OFFSET)
    chunk_offsets = []
    content = bytearray(outer)
    for encoded in encoded_chunks:
        chunk_offsets.append(len(content))
        content.extend(encoded)
    footer_offset = len(content)
    content.extend(_footer(*totals))
    return V3Fixture(bytes(content), tuple(chunk_offsets), footer_offset)


def _extract_inner_v2(container: bytes) -> bytes:
    assert container[:5] == b"JZPK\x03"
    chunk_offset = 16
    payload_bytes = int.from_bytes(container[chunk_offset + 40 : chunk_offset + 48], "big")
    payload_offset = chunk_offset + 56
    return container[payload_offset : payload_offset + payload_bytes]


_OUTER_CRC_OFFSET = 12
_CHUNK_CRC_OFFSET = 52
_FOOTER_CRC_OFFSET = 52


def _chunk(sequence: int, record_count: int, body_size: int, frame_size: int, inner: bytes) -> bytes:
    header = bytearray(b"CHNK\x00\x00" + _u16(56))
    header.extend(_u64(sequence))
    header.extend(_u64(record_count))
    header.extend(_u64(body_size))
    header.extend(_u64(frame_size))
    header.extend(_u64(len(inner)))
    header.extend(_u32(0))
    return _record(bytes(header), _CHUNK_CRC_OFFSET) + inner


def _footer(records: int, bodies: int, frames: int, payloads: int, chunks: int) -> bytes:
    footer = b"JZPF\x00\x00" + _u16(56)
    footer += b"".join(_u64(value) for value in (records, bodies, frames, payloads, chunks))
    footer += _u32(0)
    return _record(footer, _FOOTER_CRC_OFFSET)


def _record(prefix: bytes, crc_offset: int) -> bytes:
    assert len(prefix) == crc_offset
    return prefix + _u32(_crc32c(prefix))


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "big")


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "big")


def _rewrite_crc(data: bytearray, offset: int, crc_offset: int) -> None:
    data[offset + crc_offset : offset + crc_offset + 4] = _u32(_crc32c(data[offset : offset + crc_offset]))


def _set_chunk_field(
    fixture: V3Fixture, field_offset: int, value: int, width: int = 8, *, recalculate_crc: bool = True
) -> bytes:
    data = bytearray(fixture.payload)
    offset = fixture.chunk_offsets[0]
    data[offset + field_offset : offset + field_offset + width] = value.to_bytes(width, "big")
    if recalculate_crc:
        _rewrite_crc(data, offset, _CHUNK_CRC_OFFSET)
    return bytes(data)


def _append_to_inner_frame(fixture: V3Fixture, suffix: bytes) -> bytes:
    """Append bytes inside a one-chunk fixture and preserve all outer metadata."""

    chunk_offset = fixture.chunk_offsets[0]
    payload_size = int.from_bytes(fixture.payload[chunk_offset + 40 : chunk_offset + 48], "big")
    payload_end = chunk_offset + 56 + payload_size
    data = bytearray(fixture.payload[:payload_end] + suffix + fixture.payload[payload_end:])
    for field_offset in (32, 40):
        value = int.from_bytes(data[chunk_offset + field_offset : chunk_offset + field_offset + 8], "big")
        data[chunk_offset + field_offset : chunk_offset + field_offset + 8] = _u64(value + len(suffix))
    _rewrite_crc(data, chunk_offset, _CHUNK_CRC_OFFSET)

    footer_offset = fixture.footer_offset + len(suffix)
    for field_offset in (24, 32):
        value = int.from_bytes(data[footer_offset + field_offset : footer_offset + field_offset + 8], "big")
        data[footer_offset + field_offset : footer_offset + field_offset + 8] = _u64(value + len(suffix))
    _rewrite_crc(data, footer_offset, _FOOTER_CRC_OFFSET)
    return bytes(data)


class PartialReader:
    def __init__(self, data: bytes, maximum: int = 3):
        self._stream = io.BytesIO(data)
        self._maximum = maximum
        self.read_sizes: list[int] = []

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "v3 streams must never receive an unbounded read()"
        self.read_sizes.append(size)
        return self._stream.read(min(size, self._maximum))


def test_crc32c_matches_the_castagnoli_reference_vector() -> None:
    assert _crc32c(b"123456789") == 0xE3069283


def test_empty_v3_container_and_list_adapter() -> None:
    fixture = build_v3([])

    assert compress([]) == fixture.payload
    assert int.from_bytes(fixture.payload[56:64], "big") == 0
    assert list(iter_decompress(fixture.payload)) == []
    assert decompress(fixture.payload) == []


def test_writer_emits_a_single_valid_v3_chunk() -> None:
    payload = compress([{"id": 1}, {"id": 2}])

    assert payload[:5] == b"JZPK\x03"
    assert payload[16:20] == b"CHNK"
    assert int.from_bytes(payload[-16:-8], "big") == 1
    assert decompress(payload) == [{"id": 1}, {"id": 2}]


def test_multiple_chunks_preserve_order_local_schema_ids_and_empty_mappings() -> None:
    chunks = [
        [{"id": 1}, {}, {"kind": "first", "nested": {"id": 2}}],
        [{"id": 3}, {"different": True}, {}],
    ]
    fixture = build_v3(chunks)

    assert list(iter_decompress(fixture.payload)) == [record for chunk in chunks for record in chunk]
    assert decompress(fixture.payload) == [record for chunk in chunks for record in chunk]


def test_only_v3_is_accepted_by_the_iterator() -> None:
    v3 = compress([{"id": 1}, {"id": 2}])
    v2 = _extract_inner_v2(v3)

    assert list(iter_decompress(v3)) == [{"id": 1}, {"id": 2}]
    for retired in (b"JZPK\x01" + b"\x00" * 100, v2):
        with pytest.raises(UnsupportedVersionError):
            list(iter_decompress(retired))


def test_path_stream_current_position_partial_reads_and_stream_ownership(tmp_path: Path) -> None:
    records = [{"id": index} for index in range(5)]
    fixture = build_v3([records[:2], records[2:]])
    path = tmp_path / "fixture-v3.jzpk"
    path.write_bytes(fixture.payload)
    prefix = b"prefix"
    stream = io.BytesIO(prefix + fixture.payload)
    stream.seek(len(prefix))
    partial = PartialReader(fixture.payload)

    assert list(iter_decompress(path)) == records
    assert JZPackCompressor().decompress_from_file(path) == records
    assert list(iter_decompress(stream)) == records
    assert stream.tell() == len(prefix) + len(fixture.payload)
    assert not stream.closed
    assert list(iter_decompress(partial)) == records
    assert partial.read_sizes and all(size >= 0 for size in partial.read_sizes)
    assert not partial.closed


@pytest.mark.parametrize(
    ("keyword", "limit"),
    [
        ("max_output_size", 0),
        ("max_records", 1),
        ("max_chunks", 1),
        ("max_chunk_uncompressed_bytes", 0),
        ("max_chunk_payload_bytes", 1),
    ],
)
def test_v3_limits_are_checked_before_chunk_reconstruction(keyword: str, limit: int) -> None:
    fixture = build_v3([[{"id": 1}], [{"id": 2}]])

    with pytest.raises(ResourceLimitError, match=keyword):
        list(iter_decompress(fixture.payload, **{keyword: limit}))


@pytest.mark.parametrize(
    ("keyword", "exact", "below"),
    [
        ("max_records", 2, 1),
        ("max_chunks", 1, 0),
        ("max_chunk_uncompressed_bytes", "body", "body_minus_one"),
        ("max_chunk_payload_bytes", "payload", "payload_minus_one"),
        ("max_output_size", "body", "body_minus_one"),
    ],
)
def test_v3_limits_accept_the_exact_boundary_and_reject_one_below(
    keyword: str, exact: int | str, below: int | str
) -> None:
    records = [{"id": 1}, {"id": 2}]
    fixture = build_v3([records])
    chunk_offset = fixture.chunk_offsets[0]
    body_size = int.from_bytes(fixture.payload[chunk_offset + 24 : chunk_offset + 32], "big")
    payload_size = int.from_bytes(fixture.payload[chunk_offset + 40 : chunk_offset + 48], "big")
    limits = {
        "body": body_size,
        "body_minus_one": body_size - 1,
        "payload": payload_size,
        "payload_minus_one": payload_size - 1,
    }
    exact_value = limits[exact] if isinstance(exact, str) else exact
    below_value = limits[below] if isinstance(below, str) else below

    assert list(iter_decompress(fixture.payload, **{keyword: exact_value})) == records
    with pytest.raises(ResourceLimitError, match=keyword):
        list(iter_decompress(fixture.payload, **{keyword: below_value}))


@pytest.mark.parametrize("limit", [True, -1, 1.5])
def test_v3_limits_reject_bool_negative_and_non_integer_values(limit: object) -> None:
    fixture = build_v3([[{"id": 1}]])

    with pytest.raises(ValueError, match="max_chunks"):
        list(iter_decompress(fixture.payload, max_chunks=limit))  # type: ignore[arg-type]


@pytest.mark.parametrize("cut", [0, 5, 15, 16, 20, 71])
def test_truncated_v3_framing_is_invalid(cut: int) -> None:
    fixture = build_v3([[{"id": 1}]])

    with pytest.raises(InvalidFormatError):
        list(iter_decompress(fixture.payload[:cut]))


def test_v3_rejects_header_crc_flags_sizes_reserved_fields_and_sequences() -> None:
    fixture = build_v3([[{"id": 1}]])
    bad_outer_crc = bytearray(fixture.payload)
    bad_outer_crc[12] ^= 1
    bad_flags = bytearray(fixture.payload)
    bad_flags[5] = 1
    _rewrite_crc(bad_flags, 0, _OUTER_CRC_OFFSET)
    bad_size = _set_chunk_field(fixture, 6, 55, width=2)
    bad_reserved = _set_chunk_field(fixture, 48, 1, width=4)
    bad_sequence = _set_chunk_field(fixture, 8, 1)

    for payload in (bad_outer_crc, bad_flags, bad_size, bad_reserved, bad_sequence):
        with pytest.raises(InvalidFormatError):
            list(iter_decompress(payload))


def test_v3_rejects_bad_chunk_crc_bad_footer_crc_and_trailing_bytes() -> None:
    fixture = build_v3([[{"id": 1}]])
    bad_chunk_crc = bytearray(fixture.payload)
    bad_chunk_crc[fixture.chunk_offsets[0] + 52] ^= 1
    bad_footer_crc = bytearray(fixture.payload)
    bad_footer_crc[fixture.footer_offset + 52] ^= 1

    for payload in (bad_chunk_crc, bad_footer_crc, fixture.payload + b"extra"):
        with pytest.raises(InvalidFormatError):
            list(iter_decompress(payload))


@pytest.mark.parametrize("suffix", [b"junk", CompressionEngine().compress(b"another frame")])
def test_v3_rejects_bytes_after_the_inner_zstandard_frame(suffix: bytes) -> None:
    fixture = build_v3([[{"id": 1}]])
    payload = _append_to_inner_frame(fixture, suffix)

    with pytest.raises(InvalidFormatError, match="trailing frame data"):
        list(iter_decompress(payload))


def test_v3_rejects_unknown_kinds_and_footer_total_mismatches() -> None:
    fixture = build_v3([[{"id": 1}]])
    unknown_chunk_kind = bytearray(fixture.payload)
    unknown_chunk_kind[fixture.chunk_offsets[0] + 4] = 1
    _rewrite_crc(unknown_chunk_kind, fixture.chunk_offsets[0], _CHUNK_CRC_OFFSET)
    bad_totals = bytearray(fixture.payload)
    bad_totals[fixture.footer_offset + 8 : fixture.footer_offset + 16] = _u64(2)
    _rewrite_crc(bad_totals, fixture.footer_offset, _FOOTER_CRC_OFFSET)

    for payload in (unknown_chunk_kind, bad_totals):
        with pytest.raises(InvalidFormatError):
            list(iter_decompress(payload))


@pytest.mark.parametrize(
    ("field_offset", "value"),
    [(16, 0), (40, 4), (40, 6)],
)
def test_v3_rejects_zero_record_and_invalid_payload_lengths(field_offset: int, value: int) -> None:
    fixture = build_v3([[{"id": 1}]])

    with pytest.raises(InvalidFormatError):
        list(iter_decompress(_set_chunk_field(fixture, field_offset, value)))


def test_v3_rejects_malformed_messagepack_row_count_and_body_size_mismatches() -> None:
    fixture = build_v3([[{"id": 1}]])
    chunk_offset = fixture.chunk_offsets[0]
    original = fixture.payload
    frame = CompressionEngine().compress(b"\xc1")
    malformed = _chunk(0, 1, 1, len(frame), b"JZPK\x02" + frame)
    malformed_fixture = V3Fixture(
        original[:chunk_offset] + malformed + _footer(1, 1, len(frame), len(frame) + 5, 1),
        (chunk_offset,),
        chunk_offset + len(malformed),
    )
    row_mismatch = _set_chunk_field(fixture, 16, 2)
    row_data = bytearray(row_mismatch)
    row_data[fixture.footer_offset + 8 : fixture.footer_offset + 16] = _u64(2)
    _rewrite_crc(row_data, fixture.footer_offset, _FOOTER_CRC_OFFSET)
    body_mismatch = _set_chunk_field(fixture, 24, 2)
    body_data = bytearray(body_mismatch)
    body_data[fixture.footer_offset + 16 : fixture.footer_offset + 24] = _u64(2)
    _rewrite_crc(body_data, fixture.footer_offset, _FOOTER_CRC_OFFSET)

    for payload in (malformed_fixture.payload, row_data, body_data):
        with pytest.raises(InvalidFormatError):
            list(iter_decompress(payload))


def test_v3_rejects_invalid_inner_versions() -> None:
    fixture = build_v3([[{"id": 1}]])
    inner_offset = fixture.chunk_offsets[0] + 56
    bad_magic = bytearray(fixture.payload)
    bad_magic[inner_offset : inner_offset + 4] = b"NOPE"
    v1_inner = bytearray(fixture.payload)
    v1_inner[inner_offset + 4] = 1
    future_inner = bytearray(fixture.payload)
    future_inner[inner_offset + 4] = 99

    for payload in (bad_magic, v1_inner, future_inner):
        with pytest.raises(InvalidFormatError):
            list(iter_decompress(payload))


def test_v3_recovery_reports_a_corrupt_middle_chunk_and_keeps_later_chunks() -> None:
    chunks = [[{"id": 1}], [{"id": 2}, {"id": 22}], [{"id": 3}]]
    fixture = build_v3(chunks)
    corrupt = bytearray(fixture.payload)
    middle_header = fixture.chunk_offsets[1]
    payload_size = int.from_bytes(corrupt[middle_header + 40 : middle_header + 48], "big")
    corrupt[middle_header + 56 + payload_size - 1] ^= 1

    with pytest.raises(InvalidFormatError):
        list(iter_decompress(corrupt))

    events = list(iter_decompress_recover(corrupt))
    assert [type(event) for event in events] == [ChunkRecords, ChunkError, ChunkRecords]
    assert events[0].records == chunks[0]  # type: ignore[union-attr]
    assert events[1].sequence == 1
    assert isinstance(events[1].error, InvalidFormatError)  # type: ignore[union-attr]
    assert events[2].records == chunks[2]  # type: ignore[union-attr]
    assert [record for event in events if isinstance(event, ChunkRecords) for record in event.records] == [
        {"id": 1},
        {"id": 3},
    ]


def test_iter_decompress_requires_footer_completion_for_full_container_validation() -> None:
    fixture = build_v3([[{"id": 1}]])
    missing_footer = fixture.payload[: fixture.footer_offset]
    records = iter_decompress(missing_footer)

    assert next(records) == {"id": 1}
    with pytest.raises(InvalidFormatError, match="truncated"):
        next(records)


def test_v3_recovery_stops_when_chunk_framing_is_corrupt() -> None:
    fixture = build_v3([[{"id": 1}], [{"id": 2}]])
    corrupted = bytearray(fixture.payload)
    corrupted[fixture.chunk_offsets[1] + 52] ^= 1

    with pytest.raises(InvalidFormatError, match="CRC"):
        list(iter_decompress_recover(corrupted))


def test_v3_fails_with_typed_errors_for_arbitrary_bytes_and_unknown_versions() -> None:
    samples = [b"", b"J", b"JZPK\x03", b"JZPK\x99", bytes(range(128))]
    for sample in samples:
        with pytest.raises((InvalidFormatError, UnsupportedVersionError)):
            list(iter_decompress(sample))


def test_v3_writer_and_multi_chunk_fixture_builder_are_deterministic() -> None:
    chunks = [[{"id": 1}], [{"id": 2}]]

    assert build_v3(chunks).payload == build_v3(chunks).payload
    assert compress([{"id": 1}]) == compress([{"id": 1}])
    assert compress([{"id": 1}])[4] == 3
