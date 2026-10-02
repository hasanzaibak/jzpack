"""Contract tests for the record-stream MessagePack/Zstandard benchmark baseline."""

from __future__ import annotations

import gc
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import weakref
from pathlib import Path

import msgpack
import pytest
import zstandard as zstd

from benchmarks import benchmark_streamed_baseline as benchmark
from benchmarks.corpus_wave3 import build_records, records_difference

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "benchmarks" / "benchmark_streamed_baseline.py"


def _packed_array(records: list[dict[str, object]]) -> bytes:
    packer = msgpack.Packer(use_bin_type=True)
    return packer.pack_array_header(len(records)) + b"".join(packer.pack(row) for row in records)


def _frame(body: bytes, *, checksum: bool = True) -> bytes:
    return zstd.ZstdCompressor(write_checksum=checksum).compress(body)


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    path_parts = [str(REPOSITORY)]
    if environment.get("PYTHONPATH"):
        path_parts.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(path_parts)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def test_streamed_baseline_preserves_types_float_bits_and_missing_vs_null() -> None:
    nan = struct.unpack(">d", bytes.fromhex("7ff8000000000042"))[0]
    records: list[dict[str, object]] = [
        {
            "id": True,
            "nested": [None, -0.0, nan, {"literal.dot": b"\x00\xff"}],
            "unicode": "café 🛰️",
        },
        {"id": 1, "optional": None, "nested": [0.0]},
        {"id": 2, "nested": []},
    ]

    archive = benchmark._encode_to_archive("msgpack-stream-zstd", records, level=3)
    decoded = benchmark._decode_once("msgpack-stream-zstd", archive, len(records))

    assert records_difference(records, decoded) is None
    assert "optional" not in decoded[0]
    assert decoded[1]["optional"] is None
    assert zstd.get_frame_parameters(archive).has_checksum


@pytest.mark.parametrize(
    ("profile", "count", "string_bytes"),
    [
        ("schema-diverse", 257, 128),
        ("nested-records", 97, 128),
        ("large-strings", 5, 1024),
    ],
)
def test_streamed_baseline_round_trips_each_wave2_corpus_shape(
    profile: str, count: int, string_bytes: int
) -> None:
    records = build_records(profile, count, large_string_bytes=string_bytes)
    archive = benchmark._encode_to_archive("msgpack-stream-zstd", records, level=3)

    decoded = benchmark._decode_once("msgpack-stream-zstd", archive, count)

    assert records_difference(records, decoded) is None


def test_stream_encoder_serializes_a_reused_input_mapping_immediately() -> None:
    reused = {"index": 0}

    def records():
        for index in range(4):
            reused["index"] = index
            yield reused

    sink = io.BytesIO()
    benchmark._write_streamed_msgpack_zstd(records(), 4, sink, level=3)

    decoded = benchmark._decode_once("msgpack-stream-zstd", sink.getvalue(), 4)

    assert decoded == [{"index": 0}, {"index": 1}, {"index": 2}, {"index": 3}]


def test_stream_decoder_rejects_missing_truncated_and_bad_checksum_frames() -> None:
    records: list[dict[str, object]] = [{"id": 1}, {"id": 2}]
    archive = benchmark._encode_to_archive("msgpack-stream-zstd", records, level=3)
    corrupted = bytearray(archive)
    corrupted[-1] ^= 0x01

    with pytest.raises(benchmark.StreamFormatError, match="invalid or missing"):
        list(benchmark.iter_streamed_msgpack_zstd(b"", 2))
    with pytest.raises(benchmark.StreamFormatError, match="incomplete"):
        list(benchmark.iter_streamed_msgpack_zstd(archive[:-1], 2))
    with pytest.raises(benchmark.StreamFormatError, match="invalid or incomplete"):
        list(benchmark.iter_streamed_msgpack_zstd(corrupted, 2))


def test_checksum_failure_is_reported_after_a_complete_row_can_be_yielded() -> None:
    records: list[dict[str, object]] = [{"id": 1}, {"id": 2}]
    archive = bytearray(benchmark._encode_to_archive("msgpack-stream-zstd", records, level=3))
    archive[-1] ^= 0x01
    decoded = benchmark.iter_streamed_msgpack_zstd(archive, 2, input_chunk_size=1)

    assert next(decoded) == {"id": 1}
    with pytest.raises(benchmark.StreamFormatError, match="invalid or incomplete"):
        list(decoded)


def test_stream_decoder_requires_a_checksum_and_rejects_trailing_frames_or_bytes() -> None:
    records: list[dict[str, object]] = [{"id": 1}]
    body = _packed_array(records)
    checksummed = _frame(body)
    unchecksummed = _frame(body, checksum=False)
    second_frame = _frame(_packed_array([{"id": 2}]))

    with pytest.raises(benchmark.StreamFormatError, match="missing its content checksum"):
        list(benchmark.iter_streamed_msgpack_zstd(unchecksummed, 1))
    with pytest.raises(benchmark.StreamFormatError, match="trailing bytes"):
        list(benchmark.iter_streamed_msgpack_zstd(checksummed + b"tail", 1))
    with pytest.raises(benchmark.StreamFormatError, match="trailing bytes"):
        list(benchmark.iter_streamed_msgpack_zstd(checksummed + second_frame, 1))


def test_stream_decoder_rejects_incomplete_or_extra_messagepack_array_data() -> None:
    one_record = {"id": 1}
    one = msgpack.packb(one_record, use_bin_type=True)
    extra_complete = _frame(msgpack.Packer().pack_array_header(1) + one + msgpack.packb(2))
    extra_incomplete = _frame(msgpack.Packer().pack_array_header(1) + one + b"\x82")
    missing_item = _frame(msgpack.Packer().pack_array_header(2) + one)
    incomplete_item = _frame(msgpack.Packer().pack_array_header(1) + b"\x82")

    with pytest.raises(benchmark.StreamFormatError, match="trailing bytes after the declared"):
        list(benchmark.iter_streamed_msgpack_zstd(extra_complete, 1))
    with pytest.raises(benchmark.StreamFormatError, match="trailing bytes after the declared"):
        list(benchmark.iter_streamed_msgpack_zstd(extra_incomplete, 1))
    with pytest.raises(benchmark.StreamFormatError, match="ended before all records"):
        list(benchmark.iter_streamed_msgpack_zstd(missing_item, 2))
    with pytest.raises(benchmark.StreamFormatError, match="ended before all records"):
        list(benchmark.iter_streamed_msgpack_zstd(incomplete_item, 1))
    with pytest.raises(benchmark.StreamFormatError, match="array count"):
        list(benchmark.iter_streamed_msgpack_zstd(_frame(_packed_array([one_record])), 2))


class _ShortWritingSink:
    def __init__(self, max_write: int) -> None:
        self.buffer = io.BytesIO()
        self.max_write = max_write
        self.calls = 0
        self.closed = False

    def write(self, data: bytes | bytearray | memoryview) -> int:
        self.calls += 1
        view = memoryview(data)
        size = min(self.max_write, len(view))
        self.buffer.write(view[:size])
        return size

    def getvalue(self) -> bytes:
        return self.buffer.getvalue()


@pytest.mark.parametrize("max_write", [1, 3, 17])
def test_stream_encoder_retries_positive_short_writes_and_keeps_sink_open(max_write: int) -> None:
    records: list[dict[str, object]] = [{"index": index} for index in range(9)]
    sink = _ShortWritingSink(max_write)

    benchmark._write_streamed_msgpack_zstd(records, len(records), sink, level=3)

    assert sink.calls > 1
    assert not sink.closed
    assert records_difference(records, benchmark._decode_once("msgpack-stream-zstd", sink.getvalue(), 9)) is None


@pytest.mark.parametrize(
    ("result", "exception"),
    [(None, TypeError), (True, TypeError), (0, OSError), (-1, OSError)],
)
def test_stream_encoder_rejects_invalid_sink_write_counts(result: object, exception: type[Exception]) -> None:
    class BadSink:
        def write(self, data: bytes | memoryview) -> object:
            return result

    with pytest.raises(exception):
        benchmark._write_streamed_msgpack_zstd([{"id": 1}], 1, BadSink(), level=3)


def test_stream_encoder_rejects_a_declared_count_mismatch() -> None:
    with pytest.raises(ValueError, match="fewer records"):
        benchmark._write_streamed_msgpack_zstd(iter([{"id": 1}]), 2, io.BytesIO(), level=3)
    with pytest.raises(ValueError, match="more records"):
        benchmark._write_streamed_msgpack_zstd(iter([{"id": 1}, {"id": 2}]), 1, io.BytesIO(), level=3)


def test_timed_encode_hashing_occurs_after_the_timer_stops(monkeypatch) -> None:
    records: list[dict[str, object]] = [{"id": index} for index in range(3)]
    clock_calls = 0
    original_sha256 = hashlib.sha256

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return float(clock_calls)

    def sha256(data=b""):
        assert clock_calls >= 2
        return original_sha256(data)

    monkeypatch.setattr(benchmark.time, "perf_counter", clock)
    monkeypatch.setattr(benchmark.hashlib, "sha256", sha256)

    archive, elapsed = benchmark._timed_encode("msgpack-stream-zstd", records, level=3)
    digest = benchmark._sha256(archive)

    assert elapsed == 1.0
    assert len(digest) == 64


def test_memory_probes_hash_every_method_with_the_same_discard_sink() -> None:
    records = build_records("schema-diverse", 37)
    archive_hashes = {
        method: benchmark._sha256(benchmark._encode_to_archive(method, records, 3))
        for method in benchmark.METHODS
    }

    for method in benchmark.METHODS:
        sink = benchmark._HashingDiscardSink()
        benchmark._encode_to_memory_sink(
            method,
            "schema-diverse",
            len(records),
            benchmark.DEFAULT_SEED,
            128,
            3,
            sink,
        )
        assert sink.sha256 == archive_hashes[method]
        assert sink.size_bytes > 0


def test_first_record_probes_report_different_integrity_boundaries_explicitly() -> None:
    records = build_records("nested-records", 11)

    writer = benchmark._first_record_sample("jzpack-writer", records, level=3)
    streamed = benchmark._first_record_sample("msgpack-stream-zstd", records, level=3)
    list_mode = benchmark._first_record_sample("msgpack-list-zstd", records, level=3)

    assert writer["kind"] == "first_record_from_complete_jz_chunk"
    assert writer["checksum_validated"] is True
    assert writer["archive_complete"] is False
    assert streamed["kind"] == "first_complete_record_after_zstd_block_flush"
    assert streamed["checksum_validated"] is False
    assert streamed["archive_complete"] is False
    assert list_mode["kind"] == "first_record_after_complete_archive_decode"
    assert list_mode["checksum_validated"] is True
    assert list_mode["archive_complete"] is True


@pytest.mark.parametrize("method", ["jzpack-writer", "msgpack-list-zstd", "msgpack-stream-zstd"])
def test_first_record_validation_is_outside_availability_timer(monkeypatch, method: str) -> None:
    records = build_records("schema-diverse", 3)
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return float(clock_calls)

    def validate(*args) -> None:
        assert clock_calls >= 2

    monkeypatch.setattr(benchmark.time, "perf_counter", clock)
    monkeypatch.setattr(benchmark, "_validate_round_trip", validate)

    result = benchmark._first_record_sample(method, records, level=3)

    assert result["elapsed_seconds"] == 1.0


def test_streamed_first_record_timer_stops_before_writer_close(monkeypatch) -> None:
    records = [{"id": 1}, {"id": 2}]
    clock_calls = 0

    class FakeWriter:
        def write(self, data: bytes) -> int:
            return len(data)

        def flush(self, _mode: int) -> None:
            return None

        def close(self) -> None:
            perf_counter()

    class FakeCompressor:
        def __init__(self, **_kwargs) -> None:
            pass

        def stream_writer(self, *_args, **_kwargs) -> FakeWriter:
            return FakeWriter()

    def perf_counter() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return float(clock_calls)

    def decode_first(_prefix: bytes, _count: int) -> dict[str, int]:
        return records[0]

    def validate(*_args) -> None:
        assert clock_calls == 3

    monkeypatch.setattr(benchmark.time, "perf_counter", perf_counter)
    monkeypatch.setattr(benchmark.zstd, "ZstdCompressor", FakeCompressor)
    monkeypatch.setattr(benchmark, "_decode_first_partial_zstd", decode_first)
    monkeypatch.setattr(benchmark, "_validate_round_trip", validate)

    result = benchmark._first_record_sample("msgpack-stream-zstd", records, level=3)

    assert result["elapsed_seconds"] == 1.0


def test_smoke_report_keeps_raw_samples_hashes_and_comparable_decode_results() -> None:
    report = benchmark.run_benchmark(
        suite="smoke", samples=1, warmups=0, large_string_bytes=128, measure_memory=False
    )

    assert report["benchmark"] == "jzpack-streamed-msgpack-baseline"
    assert report["configuration"]["encode_hashing_in_timer"] is False
    assert "forces FLUSH_BLOCK" in report["configuration"]["first_record_probe"]
    runtime_hashes = report["source"]["runtime_source_sha256"]
    assert set(runtime_hashes) == set(benchmark.RUNTIME_SOURCES)
    assert all(len(digest) == 64 for digest in runtime_hashes.values())
    assert len(report["workloads"]) == 3
    for workload in report["workloads"]:
        implementations = workload["implementations"]
        assert set(implementations) == set(benchmark.METHODS)
        for result in implementations.values():
            assert result["exact_round_trip_validated"] is True
            assert result["deterministic_output"] is True
            assert len(result["archive_sha256"]) == 64
            assert len(result["encode"]["samples_seconds"]) == 1
            assert len(result["decode"]["samples_seconds"]) == 1
            assert len(result["first_record_availability"]) == 1


def test_previous_decoded_list_is_released_before_next_encode_timer(monkeypatch) -> None:
    previous_decoded = None

    class WeakList(list):
        pass

    def timed_encode(_method, _records, _level):
        if previous_decoded is not None:
            gc.collect()
            assert previous_decoded() is None
        return b"archive", 0.001

    def decode_once(_method, _archive, _expected_count):
        nonlocal previous_decoded
        decoded = WeakList([{"id": 1}])
        previous_decoded = weakref.ref(decoded)
        return decoded

    monkeypatch.setattr(benchmark, "_case_definitions", lambda *_args: [{"profile": "schema-diverse", "records": 1}])
    monkeypatch.setattr(benchmark, "_timed_encode", timed_encode)
    monkeypatch.setattr(benchmark, "_decode_once", decode_once)
    monkeypatch.setattr(benchmark, "_validate_round_trip", lambda *_args: None)
    monkeypatch.setattr(benchmark, "_first_record_sample", lambda *_args: {"elapsed_seconds": 0.0})

    benchmark.run_benchmark(
        suite="smoke", samples=1, warmups=0, measure_memory=False
    )


def test_streamed_baseline_cli_json_and_safe_errors(tmp_path: Path) -> None:
    success = _run_cli("--suite", "smoke", "--samples", "1", "--warmups", "0", "--skip-memory")
    invalid = _run_cli("--samples", "0", "--skip-memory")
    unknown = _run_cli("--private-option-value")
    invalid_choice = _run_cli("--suite", "private-suite-value")
    invalid_integer = _run_cli("--samples", "private-integer-value")
    help_result = _run_cli("--help")
    private_path = tmp_path / "private-path-token" / "report.json"
    path_failure = _run_cli(
        "--suite", "smoke", "--samples", "1", "--warmups", "0", "--skip-memory",
        "--output", str(private_path),
    )

    assert success.returncode == 0, success.stderr
    assert len(success.stdout.splitlines()) == 1
    assert json.loads(success.stdout)["benchmark"] == "jzpack-streamed-msgpack-baseline"
    assert invalid.returncode == benchmark.FAILURE_EXIT_CODE
    assert invalid.stdout == ""
    assert invalid.stderr == "benchmark failed: ValueError\n"
    for result, private_value in (
        (unknown, "private-option-value"),
        (invalid_choice, "private-suite-value"),
        (invalid_integer, "private-integer-value"),
    ):
        assert result.returncode == 2
        assert private_value not in result.stdout + result.stderr
    assert help_result.returncode == 0
    assert "--suite" in help_result.stdout
    assert path_failure.returncode == benchmark.FAILURE_EXIT_CODE
    assert path_failure.stderr == "benchmark failed: FileNotFoundError\n"
    assert str(private_path) not in path_failure.stdout + path_failure.stderr
