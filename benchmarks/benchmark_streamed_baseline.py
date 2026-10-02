"""Compare JZPack with list and record-stream MessagePack/Zstandard baselines."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import msgpack
import zstandard as zstd

from benchmarks.corpus_wave3 import (
    DEFAULT_LARGE_STRING_BYTES,
    DEFAULT_SEED,
    build_records,
    corpus_fingerprint,
    iter_records,
    records_difference,
)
from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress, iter_decompress, write_records

BENCHMARK_NAME = "jzpack-streamed-msgpack-baseline"
FAILURE_EXIT_CODE = 3
METHODS = ("jzpack-list", "jzpack-writer", "msgpack-list-zstd", "msgpack-stream-zstd")
_REPOSITORY = Path(__file__).resolve().parents[1]
_MEMORY_WORKER = "--_memory-worker"
_WRITE_SIZE = 64 * 1024
_DECODE_INPUT_SIZE = 16 * 1024
_UNPACK_FEED_SIZE = 16 * 1024
_MAX_PENDING_MSGPACK_BYTES = 1024 * 1024
_OUTER_HEADER_BYTES = 16
_CHUNK_HEADER_BYTES = 56
RUNTIME_SOURCES = tuple(
    sorted(path.relative_to(_REPOSITORY).as_posix() for path in (_REPOSITORY / "jzpack").glob("*.py"))
)


class StreamFormatError(ValueError):
    """The comparison stream is incomplete or has data outside its declared array/frame."""


class _StopAfterFirstChunk(Exception):
    pass


class _WriteAllSink:
    """Adapt short-writing sinks to the all-bytes write expected by zstandard."""

    def __init__(self, sink: Any) -> None:
        self.sink = sink

    def write(self, data: bytes | bytearray | memoryview) -> int:
        view = memoryview(data)
        total = 0
        while total < len(view):
            result = self.sink.write(view[total:])
            if type(result) is not int:
                raise TypeError("sink.write must return an integer byte count")
            remaining = len(view) - total
            if result <= 0 or result > remaining:
                raise OSError("sink.write returned an invalid byte count")
            total += result
        return total

    def flush(self) -> None:
        flush = getattr(self.sink, "flush", None)
        if flush is not None:
            flush()


class _HashingDiscardSink:
    """Count and hash emitted bytes without retaining an archive."""

    def __init__(self) -> None:
        self.size_bytes = 0
        self._digest = hashlib.sha256()

    def write(self, data: bytes | bytearray | memoryview) -> int:
        view = memoryview(data)
        self._digest.update(view)
        self.size_bytes += len(view)
        return len(view)

    def flush(self) -> None:
        return None

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


class _FirstChunkSink:
    """Retain a JZ prefix, then stop the writer after its first complete data chunk."""

    def __init__(self) -> None:
        self._data = bytearray()
        self._chunk_end: int | None = None
        self.record_count: int | None = None

    def write(self, data: bytes | bytearray | memoryview) -> int:
        view = memoryview(data)
        self._data.extend(view)
        if self._chunk_end is None and len(self._data) >= _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES:
            header = self._data[_OUTER_HEADER_BYTES : _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES]
            self.record_count = int.from_bytes(header[16:24], "big")
            payload_size = int.from_bytes(header[40:48], "big")
            if self.record_count:
                self._chunk_end = _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES + payload_size
        if self._chunk_end is not None and len(self._data) >= self._chunk_end:
            raise _StopAfterFirstChunk
        return len(view)

    def getvalue(self) -> bytes:
        return bytes(self._data)


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _sha256(data: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _summary(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("at least one sample is required")
    return {
        "samples_seconds": samples,
        "median_seconds": statistics.median(samples),
        "minimum_seconds": ordered[0],
        "maximum_seconds": ordered[-1],
    }


def _write_streamed_msgpack_zstd(
    records: Iterable[dict[str, Any]],
    expected_count: int,
    sink: Any,
    level: int,
) -> int:
    """Write one MessagePack array as an incremental sequence in one checksummed frame."""
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("expected record count must be a non-negative integer")
    adapter = _WriteAllSink(sink)
    # ``buf_size`` is not accepted by the minimum supported msgpack 1.0.0.
    # Packing one record at a time already bounds the number of live packed rows.
    packer = msgpack.Packer(use_bin_type=True)
    count = 0
    compressor = zstd.ZstdCompressor(level=level, write_checksum=True)
    with compressor.stream_writer(adapter, write_size=_WRITE_SIZE, closefd=False) as writer:
        writer.write(packer.pack_array_header(expected_count))
        for record in records:
            if count >= expected_count:
                raise ValueError("input contains more records than declared")
            writer.write(packer.pack(record))
            count += 1
        if count != expected_count:
            raise ValueError("input contains fewer records than declared")
    return count


def iter_streamed_msgpack_zstd(
    payload: bytes | bytearray | memoryview,
    expected_count: int,
    *,
    input_chunk_size: int = _DECODE_INPUT_SIZE,
) -> Iterator[dict[str, Any]]:
    """Yield records from one checksummed frame, validating framing on exhaustion.

    The record count is supplied by the benchmark case and repeated in the MessagePack
    array header. Callers must exhaust the iterator to validate the final frame checksum,
    absence of trailing bytes, and all declared records.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("expected record count must be a non-negative integer")
    if isinstance(input_chunk_size, bool) or not isinstance(input_chunk_size, int) or input_chunk_size < 1:
        raise ValueError("input chunk size must be a positive integer")

    try:
        frame = zstd.get_frame_parameters(payload)
    except zstd.ZstdError as exc:
        raise StreamFormatError("invalid or missing Zstandard frame") from exc
    if not frame.has_checksum:
        raise StreamFormatError("Zstandard frame is missing its content checksum")

    decompressor = zstd.ZstdDecompressor().decompressobj(write_size=_UNPACK_FEED_SIZE)
    unpacker = msgpack.Unpacker(
        raw=False,
        strict_map_key=False,
        max_buffer_size=_MAX_PENDING_MSGPACK_BYTES,
    )
    view = memoryview(payload)
    offset = 0
    uncompressed_size = 0
    declared_count: int | None = None
    decoded_count = 0

    while offset < len(view):
        end = min(offset + input_chunk_size, len(view))
        compressed_block = view[offset:end]
        offset = end
        try:
            output = decompressor.decompress(compressed_block)
        except zstd.ZstdError as exc:
            raise StreamFormatError("invalid or incomplete Zstandard frame") from exc
        uncompressed_size += len(output)

        for part_start in range(0, len(output), _UNPACK_FEED_SIZE):
            part = output[part_start : part_start + _UNPACK_FEED_SIZE]
            if declared_count is not None and decoded_count == expected_count:
                continue
            unpacker.feed(part)
            if declared_count is None:
                try:
                    declared_count = unpacker.read_array_header()
                except msgpack.OutOfData:
                    continue
                if declared_count != expected_count:
                    raise StreamFormatError("MessagePack array count does not match the expected input")

            while decoded_count < declared_count:
                try:
                    record = unpacker.unpack()
                except msgpack.OutOfData:
                    break
                if type(record) is not dict or any(type(key) is not str for key in record):
                    raise StreamFormatError("each MessagePack array item must be a string-keyed map")
                decoded_count += 1
                yield record

        if decompressor.eof:
            if decompressor.unused_data or offset < len(view):
                raise StreamFormatError("trailing bytes after the Zstandard frame")
            break

    if not decompressor.eof:
        raise StreamFormatError("incomplete Zstandard frame")
    if declared_count is None:
        raise StreamFormatError("MessagePack array header is missing")
    if decoded_count != expected_count:
        raise StreamFormatError("MessagePack array ended before all records were decoded")
    if unpacker.tell() != uncompressed_size:
        raise StreamFormatError("trailing bytes after the declared MessagePack array")


def _encode_to_archive(method: str, records: list[dict[str, Any]], level: int) -> bytes:
    if method == "jzpack-list":
        return compress(records, level=level)
    if method == "msgpack-list-zstd":
        packed = msgpack.packb(records, use_bin_type=True)
        return zstd.ZstdCompressor(level=level, write_checksum=True).compress(packed)
    sink = io.BytesIO()
    if method == "jzpack-writer":
        write_records(records, sink, compression_level=level)
    elif method == "msgpack-stream-zstd":
        _write_streamed_msgpack_zstd(records, len(records), sink, level)
    else:
        raise ValueError(f"unknown method: {method}")
    return sink.getvalue()


def _timed_encode(method: str, records: list[dict[str, Any]], level: int) -> tuple[bytes, float]:
    started = time.perf_counter()
    if method == "jzpack-list":
        archive = compress(records, level=level)
        elapsed = time.perf_counter() - started
    elif method == "msgpack-list-zstd":
        packed = msgpack.packb(records, use_bin_type=True)
        archive = zstd.ZstdCompressor(level=level, write_checksum=True).compress(packed)
        elapsed = time.perf_counter() - started
    else:
        sink = io.BytesIO()
        if method == "jzpack-writer":
            write_records(records, sink, compression_level=level)
        elif method == "msgpack-stream-zstd":
            _write_streamed_msgpack_zstd(records, len(records), sink, level)
        else:
            raise ValueError(f"unknown method: {method}")
        elapsed = time.perf_counter() - started
        archive = sink.getvalue()
    return archive, elapsed


def _decode_once(method: str, archive: bytes, expected_count: int) -> list[dict[str, Any]]:
    if method == "jzpack-list":
        decoded = decompress(archive)
    elif method == "jzpack-writer":
        decoded = list(iter_decompress(io.BytesIO(archive)))
    elif method == "msgpack-list-zstd":
        body = zstd.ZstdDecompressor().decompress(archive, allow_extra_data=False)
        decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    elif method == "msgpack-stream-zstd":
        decoded = list(iter_streamed_msgpack_zstd(archive, expected_count))
    else:
        raise ValueError(f"unknown method: {method}")
    if type(decoded) is not list or len(decoded) != expected_count:
        raise StreamFormatError("decoded record count does not match the benchmark input")
    return decoded


def _validate_round_trip(method: str, expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> None:
    difference = records_difference(expected, actual)
    if difference is not None:
        raise RuntimeError(f"{method} failed exact round trip: {difference}")


def _decode_first_partial_zstd(prefix: bytes, expected_count: int) -> dict[str, Any]:
    decompressor = zstd.ZstdDecompressor().decompressobj(write_size=_UNPACK_FEED_SIZE)
    body = decompressor.decompress(prefix)
    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False, max_buffer_size=_MAX_PENDING_MSGPACK_BYTES)
    unpacker.feed(body)
    count = unpacker.read_array_header()
    if count != expected_count:
        raise StreamFormatError("MessagePack array count does not match the first-record probe")
    record = unpacker.unpack()
    if type(record) is not dict or any(type(key) is not str for key in record):
        raise StreamFormatError("first MessagePack array item must be a string-keyed map")
    if decompressor.eof:
        raise StreamFormatError("first-record probe unexpectedly completed the whole frame")
    return record


def _first_record_sample(method: str, records: list[dict[str, Any]], level: int) -> dict[str, Any]:
    if not records:
        return {
            "kind": "no-record",
            "elapsed_seconds": 0.0,
            "checksum_validated": True,
            "archive_complete": True,
            "records_in_first_chunk": None,
        }

    started = time.perf_counter()
    checksum_validated = True
    archive_complete = True
    first_chunk_records: int | None = None
    elapsed: float | None = None

    if method == "jzpack-writer":
        sink = _FirstChunkSink()
        try:
            write_records(records, sink, compression_level=level)
        except _StopAfterFirstChunk:
            pass
        else:
            raise RuntimeError("writer did not emit a complete first chunk")
        first_chunk_records = sink.record_count
        first_record = next(iter_decompress(io.BytesIO(sink.getvalue())))
        elapsed = time.perf_counter() - started
        kind = "first_record_from_complete_jz_chunk"
        archive_complete = False
    elif method == "msgpack-stream-zstd":
        sink = io.BytesIO()
        adapter = _WriteAllSink(sink)
        packer = msgpack.Packer(use_bin_type=True)
        writer = zstd.ZstdCompressor(level=level, write_checksum=True).stream_writer(
            adapter, write_size=_WRITE_SIZE, closefd=False
        )
        try:
            writer.write(packer.pack_array_header(len(records)))
            writer.write(packer.pack(records[0]))
            writer.flush(zstd.FLUSH_BLOCK)
            first_record = _decode_first_partial_zstd(sink.getvalue(), len(records))
            elapsed = time.perf_counter() - started
        finally:
            writer.close()
        kind = "first_complete_record_after_zstd_block_flush"
        checksum_validated = False
        archive_complete = False
    else:
        archive = _encode_to_archive(method, records, level)
        decoded = _decode_once(method, archive, len(records))
        first_record = decoded[0]
        elapsed = time.perf_counter() - started
        kind = "first_record_after_complete_archive_decode"

    if elapsed is None:
        raise RuntimeError("first-record probe ended without decoding a record")
    _validate_round_trip(method, [records[0]], [first_record])
    return {
        "kind": kind,
        "elapsed_seconds": elapsed,
        "checksum_validated": checksum_validated,
        "archive_complete": archive_complete,
        "records_in_first_chunk": first_chunk_records,
    }


def _encode_to_memory_sink(
    method: str,
    profile: str,
    count: int,
    seed: int,
    string_bytes: int,
    level: int,
    sink: _HashingDiscardSink,
) -> None:
    source = iter_records(profile, count, seed, large_string_bytes=string_bytes)
    if method == "jzpack-list":
        sink.write(compress(source, level=level))
    elif method == "jzpack-writer":
        write_records(source, sink, compression_level=level)
    elif method == "msgpack-list-zstd":
        records = list(source)
        packed = msgpack.packb(records, use_bin_type=True)
        sink.write(zstd.ZstdCompressor(level=level, write_checksum=True).compress(packed))
    elif method == "msgpack-stream-zstd":
        _write_streamed_msgpack_zstd(source, count, sink, level)
    else:
        raise ValueError(f"unknown method: {method}")


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _memory_worker(method: str, profile: str, count: int, seed: int, string_bytes: int, level: int) -> dict[str, Any]:
    warmup_count = min(count, 128)
    _encode_to_memory_sink(
        method, profile, warmup_count, seed, string_bytes, level, _HashingDiscardSink()
    )
    baseline_rss = _peak_rss_bytes()
    tracemalloc.start()
    try:
        sink = _HashingDiscardSink()
        started = time.perf_counter()
        _encode_to_memory_sink(method, profile, count, seed, string_bytes, level, sink)
        elapsed = time.perf_counter() - started
        _, traced_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_rss = _peak_rss_bytes()
    return {
        "method": method,
        "profile": profile,
        "record_count": count,
        "warmup_records": warmup_count,
        "archive_size_bytes": sink.size_bytes,
        "archive_sha256": sink.sha256,
        "elapsed_seconds_with_tracemalloc": elapsed,
        "python_tracemalloc_peak_bytes": traced_peak,
        "process_rss_peak_bytes_including_startup_and_warmup": peak_rss,
        "process_rss_peak_delta_after_warmup_bytes": (
            max(0, peak_rss - baseline_rss)
            if peak_rss is not None and baseline_rss is not None
            else None
        ),
        "rss_available": peak_rss is not None,
    }


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    paths = [str(_REPOSITORY)]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    return environment


def _memory_probes(cases: list[dict[str, Any]], samples: int, seed: int, level: int, string_bytes: int) -> list[dict[str, Any]]:
    reports = []
    for case in cases:
        profile = case["profile"]
        count = case["records"]
        per_record_bytes = case.get("large_string_bytes", string_bytes)
        for method in METHODS:
            runs = []
            for _ in range(samples):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    _MEMORY_WORKER,
                    method,
                    profile,
                    str(count),
                    str(seed),
                    str(per_record_bytes),
                    str(level),
                ]
                completed = subprocess.run(
                    command,
                    cwd=_REPOSITORY,
                    env=_subprocess_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"memory worker failed: {completed.stderr.strip()}")
                runs.append(json.loads(completed.stdout))
            reports.append(
                {"profile": profile, "record_count": count, "method": method, "samples": runs}
            )
    return reports


def _case_definitions(suite: str, string_bytes: int) -> list[dict[str, Any]]:
    if suite == "smoke":
        return [
            {"profile": "schema-diverse", "records": 37},
            {"profile": "nested-records", "records": 37},
            {"profile": "large-strings", "records": 3, "large_string_bytes": 128},
        ]
    if suite == "wave-4":
        return [
            {"profile": "schema-diverse", "records": 50_000},
            {"profile": "nested-records", "records": 40_000},
            {"profile": "large-strings", "records": 512, "large_string_bytes": string_bytes},
        ]
    raise ValueError("suite must be smoke or wave-4")


def _git_metadata() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_REPOSITORY, capture_output=True, text=True, check=False
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=_REPOSITORY, capture_output=True, text=True, check=False
    )
    return {
        "head": head.stdout.strip() if head.returncode == 0 else "unavailable",
        "working_tree_dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def run_benchmark(
    *,
    suite: str = "wave-4",
    samples: int = 3,
    warmups: int = 1,
    seed: int = DEFAULT_SEED,
    level: int = 3,
    large_string_bytes: int = DEFAULT_LARGE_STRING_BYTES,
    memory_samples: int = 3,
    measure_memory: bool = True,
) -> dict[str, Any]:
    if suite not in ("smoke", "wave-4"):
        raise ValueError("suite must be smoke or wave-4")
    for name, value in (("samples", samples), ("memory_samples", memory_samples)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 22:
        raise ValueError("level must be an integer from 1 through 22")

    cases = _case_definitions(suite, large_string_bytes)
    workloads: list[dict[str, Any]] = []
    for case in cases:
        profile = case["profile"]
        count = case["records"]
        per_record_bytes = case.get("large_string_bytes", large_string_bytes)
        records = build_records(profile, count, seed, large_string_bytes=per_record_bytes)
        input_bytes, input_hash = corpus_fingerprint(records)
        encoded: dict[str, dict[str, Any]] = {}
        decoded_timings: dict[str, list[float]] = {method: [] for method in METHODS}
        first_timings: dict[str, list[dict[str, Any]]] = {method: [] for method in METHODS}

        for method in METHODS:
            for _ in range(warmups):
                archive = _encode_to_archive(method, records, level)
                _validate_round_trip(method, records, _decode_once(method, archive, count))

        for sample_index in range(samples):
            order = list(METHODS)
            offset = sample_index % len(order)
            order = order[offset:] + order[:offset]
            for method in order:
                archive, elapsed = _timed_encode(method, records, level)
                item = encoded.setdefault(
                    method,
                    {
                        "archive_size_samples_bytes": [],
                        "archive_sha256_samples": [],
                        "encode_samples_seconds": [],
                        "exact_round_trip_validated": True,
                    },
                )
                item["archive_size_samples_bytes"].append(len(archive))
                item["archive_sha256_samples"].append(_sha256(archive))
                item["encode_samples_seconds"].append(elapsed)

                start_decode = time.perf_counter()
                decoded = _decode_once(method, archive, count)
                decoded_timings[method].append(time.perf_counter() - start_decode)
                _validate_round_trip(method, records, decoded)
                # Do not charge destruction of the prior method's output to the
                # next decode timer, or retain two decoded lists into the next encode.
                del decoded, archive
                first_timings[method].append(_first_record_sample(method, records, level))

        implementations = {}
        for method in METHODS:
            item = encoded[method]
            hashes = item["archive_sha256_samples"]
            sizes = item["archive_size_samples_bytes"]
            implementations[method] = {
                "archive_size_samples_bytes": sizes,
                "archive_size_bytes": sizes[0],
                "archive_sha256_samples": hashes,
                "archive_sha256": hashes[0],
                "deterministic_output": len(set(hashes)) == 1,
                "exact_round_trip_validated": item["exact_round_trip_validated"],
                "encode": _summary(item["encode_samples_seconds"]),
                "decode": _summary(decoded_timings[method]),
                "first_record_availability": first_timings[method],
            }

        workloads.append(
            {
                "profile": profile,
                "record_count": count,
                "large_string_bytes_per_record": per_record_bytes if profile == "large-strings" else None,
                "canonical_input_size_bytes": input_bytes,
                "input_sha256": input_hash,
                "implementations": implementations,
            }
        )

    memory = (
        _memory_probes(cases, memory_samples, seed, level, large_string_bytes)
        if measure_memory
        else []
    )
    if measure_memory:
        hashes = {
            (workload["profile"], workload["record_count"], method): result["archive_sha256"]
            for workload in workloads
            for method, result in workload["implementations"].items()
        }
        for probe in memory:
            expected_hash = hashes[(probe["profile"], probe["record_count"], probe["method"])]
            for sample in probe["samples"]:
                if sample["archive_sha256"] != expected_hash:
                    raise RuntimeError("memory probe output differs from timed archive output")

    return {
        "benchmark": BENCHMARK_NAME,
        "corpus": {
            "version": 2,
            "seed": seed,
            "generator": "benchmarks.corpus_wave3, full canonical fingerprints retained",
        },
        "configuration": {
            "suite": suite,
            "samples": samples,
            "warmups": warmups,
            "compression_level": level,
            "large_string_bytes": large_string_bytes,
            "memory_samples_per_profile": memory_samples if measure_memory else 0,
            "memory_measured_in_isolated_processes": measure_memory,
            "encode_hashing_in_timer": False,
            "first_record_probe": (
                "the streamed MessagePack probe forces FLUSH_BLOCK after the first row; this is a separate "
                "forced-flush availability experiment, not the normal encode policy"
            ),
        },
        "source": {
            **_git_metadata(),
            "benchmark_source_sha256": _source_sha256(Path(__file__).resolve()),
            "corpus_source_sha256": _source_sha256(Path(__file__).with_name("corpus_wave3.py")),
            "runtime_source_sha256": {
                path: _source_sha256(_REPOSITORY / path) for path in RUNTIME_SOURCES
            },
        },
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "jzpack_version": jzpack_version,
            "dependencies": {
                "msgpack": _distribution_version("msgpack"),
                "zstandard": _distribution_version("zstandard"),
                "zstandard_library": ".".join(map(str, zstd.ZSTD_VERSION)),
            },
        },
        "workloads": workloads,
        "memory_probes": memory,
    }


def build_parser() -> argparse.ArgumentParser:
    class _RedactingArgumentParser(argparse.ArgumentParser):
        def error(self, _message: str) -> None:
            self.print_usage(sys.stderr)
            self.exit(2, f"{self.prog}: error: invalid command-line arguments\n")

    parser = _RedactingArgumentParser(
        description="Compare JZPack APIs with full-list and streamed MessagePack/Zstandard."
    )
    parser.add_argument("--suite", choices=("smoke", "wave-4"), default="wave-4")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--large-string-bytes", type=int, default=DEFAULT_LARGE_STRING_BYTES)
    parser.add_argument("--memory-samples", type=int, default=3)
    parser.add_argument("--skip-memory", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def _main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == _MEMORY_WORKER:
        try:
            _, method, profile, count, seed, string_bytes, level = argv
            result = _memory_worker(method, profile, int(count), int(seed), int(string_bytes), int(level))
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        except Exception as exc:
            print(f"memory worker failed: {type(exc).__name__}", file=sys.stderr)
            return FAILURE_EXIT_CODE

    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run_benchmark(
            suite=arguments.suite,
            samples=arguments.samples,
            warmups=arguments.warmups,
            seed=arguments.seed,
            level=arguments.level,
            large_string_bytes=arguments.large_string_bytes,
            memory_samples=arguments.memory_samples,
            measure_memory=not arguments.skip_memory,
        )
        serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
        if arguments.output is None:
            print(serialized)
        else:
            arguments.output.write_text(serialized + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}", file=sys.stderr)
        return FAILURE_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(_main())
