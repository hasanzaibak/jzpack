"""Scale and shape comparisons for the isolated wave-3 corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import msgpack
import zstandard as zstd

from benchmarks.corpus_wave3 import (
    CORPUS_VERSION,
    DEFAULT_LARGE_STRING_BYTES,
    DEFAULT_SEED,
    PROFILE_NAMES,
    build_records,
    corpus_fingerprint,
    iter_records,
    records_difference,
)
from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress, iter_decompress, write_records

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # Optional, never a package runtime dependency.
    pa = None
    pq = None

BENCHMARK_NAME = "jzpack-corpus-wave-3"
FAILURE_EXIT_CODE = 3
_REPOSITORY = Path(__file__).resolve().parents[1]
_OUTER_HEADER_BYTES = 16
_CHUNK_HEADER_BYTES = 56
_WORKER_FLAG = "--_memory-worker"


class _ObservedSink:
    """Hash and count sink that can optionally retain output and time chunk one."""

    def __init__(self, *, retain: bool, started_at: float | None = None) -> None:
        self._buffer = io.BytesIO() if retain else None
        self._digest = hashlib.sha256()
        self._prefix = bytearray()
        self._bytes_written = 0
        self._started_at = started_at
        self.first_chunk_seconds: float | None = None
        self.first_chunk_records: int | None = None

    def write(self, data: bytes | bytearray | memoryview) -> int:
        view = memoryview(data)
        written = len(view)
        self._digest.update(view)
        if self._buffer is not None:
            self._buffer.write(view)
        if len(self._prefix) < _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES:
            needed = _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES - len(self._prefix)
            self._prefix.extend(view[:needed])
        self._bytes_written += written
        if (
            self.first_chunk_seconds is None
            and self._started_at is not None
            and len(self._prefix) >= _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES
        ):
            chunk_header = self._prefix[_OUTER_HEADER_BYTES : _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES]
            payload_bytes = int.from_bytes(chunk_header[40:48], "big")
            record_count = int.from_bytes(chunk_header[16:24], "big")
            first_chunk_end = _OUTER_HEADER_BYTES + _CHUNK_HEADER_BYTES + payload_bytes
            if record_count > 0 and self._bytes_written >= first_chunk_end:
                self.first_chunk_seconds = time.perf_counter() - self._started_at
                self.first_chunk_records = record_count
        return written

    def output_bytes(self) -> bytes:
        if self._buffer is None:
            raise RuntimeError("this sink discards its output")
        return self._buffer.getvalue()

    @property
    def size_bytes(self) -> int:
        return self._bytes_written

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timing_summary(samples: list[float]) -> dict[str, Any]:
    if not samples:
        raise ValueError("timing summary requires at least one sample")
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "samples_seconds": samples,
        "median_seconds": statistics.median(samples),
        "p95_seconds": ordered[p95_index],
        "minimum_seconds": ordered[0],
        "maximum_seconds": ordered[-1],
    }


def _encode_once(method: str, records: list[dict[str, Any]], level: int) -> tuple[bytes, dict[str, Any]]:
    phases: dict[str, Any] = {}
    if method == "jzpack-memory":
        return compress(records, level=level), phases
    if method == "msgpack-zstd":
        pack_started = time.perf_counter()
        packed = msgpack.packb(records, use_bin_type=True)
        phases["messagepack_pack_seconds"] = time.perf_counter() - pack_started
        compress_started = time.perf_counter()
        output = zstd.ZstdCompressor(level=level, write_checksum=True).compress(packed)
        phases["zstd_compress_seconds"] = time.perf_counter() - compress_started
        return output, phases
    if method == "jzpack-writer":
        started = time.perf_counter()
        sink = _ObservedSink(retain=True, started_at=started)
        write_records(records, sink, compression_level=level)
        if records and sink.first_chunk_seconds is None:
            raise RuntimeError("writer did not expose a complete data chunk")
        return sink.output_bytes(), {
            "first_chunk_seconds": sink.first_chunk_seconds,
            "first_chunk_records": sink.first_chunk_records,
        }
    if method == "parquet-zstd":
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is unavailable")
        started = time.perf_counter()
        table = pa.Table.from_pylist(records)
        phases["table_build_seconds"] = time.perf_counter() - started
        sink = pa.BufferOutputStream()
        write_started = time.perf_counter()
        pq.write_table(table, sink, compression="zstd", compression_level=level, use_dictionary=True)
        phases["parquet_write_seconds"] = time.perf_counter() - write_started
        export_started = time.perf_counter()
        payload = sink.getvalue().to_pybytes()
        phases["output_export_seconds"] = time.perf_counter() - export_started
        return payload, phases
    raise ValueError(f"unknown implementation: {method}")


def _decode_once(method: str, payload: bytes) -> list[dict[str, Any]]:
    if method == "jzpack-memory":
        return decompress(payload)
    if method == "msgpack-zstd":
        decoded = zstd.ZstdDecompressor().decompress(payload, allow_extra_data=False)
        return msgpack.unpackb(decoded, raw=False, strict_map_key=False)
    if method == "jzpack-writer":
        return list(iter_decompress(io.BytesIO(payload)))
    if method == "parquet-zstd":
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is unavailable")
        table = pq.read_table(pa.BufferReader(payload))
        return table.to_pylist()
    raise ValueError(f"unknown implementation: {method}")


def _validate_round_trip(
    method: str, expected: list[dict[str, Any]], payload: bytes
) -> None:
    actual = _decode_once(method, payload)
    difference = records_difference(expected, actual)
    if difference is not None:
        raise RuntimeError(f"{method} failed exact round trip: {difference}")


def _parquet_eligibility(records: list[dict[str, Any]]) -> dict[str, Any]:
    if pa is None or pq is None:
        return {"available": False, "eligible": False, "reason": "pyarrow is not installed"}
    try:
        payload, _ = _encode_once("parquet-zstd", records, 3)
        difference = records_difference(records, _decode_once("parquet-zstd", payload))  # type: ignore[arg-type]
    except Exception as exc:
        return {
            "available": True,
            "eligible": False,
            "reason": f"exact round-trip preflight raised {type(exc).__name__}",
        }
    if difference is not None:
        return {"available": True, "eligible": False, "reason": difference}
    return {"available": True, "eligible": True, "reason": None}


def _implementation_samples(
    method: str,
    records: list[dict[str, Any]],
    samples: int,
    warmups: int,
    level: int,
) -> dict[str, Any]:
    encode_samples: list[float] = []
    decode_samples: list[float] = []
    first_output_samples: list[float] = []
    first_chunk_record_counts: list[int] = []
    sizes: list[int] = []
    hashes: list[str] = []
    phases: dict[str, list[float]] = {}

    for _ in range(warmups):
        payload, _ = _encode_once(method, records, level)
        _validate_round_trip(method, records, payload)

    for _ in range(samples):
        started = time.perf_counter()
        payload, sample_phases = _encode_once(method, records, level)
        elapsed = time.perf_counter() - started
        encode_samples.append(elapsed)
        sizes.append(len(payload))
        hashes.append(hashlib.sha256(payload).hexdigest())
        for name, value in sample_phases.items():
            if name.endswith("_seconds"):
                phases.setdefault(name, []).append(value)
        if method == "jzpack-writer":
            record_count = sample_phases["first_chunk_records"]
            if not isinstance(record_count, int) or record_count <= 0:
                raise RuntimeError("writer did not report records in its first complete chunk")
            first_chunk_record_counts.append(record_count)
        first_output_samples.append(
            sample_phases["first_chunk_seconds"] if method == "jzpack-writer" else elapsed
        )

        started = time.perf_counter()
        decoded = _decode_once(method, payload)
        decode_samples.append(time.perf_counter() - started)
        difference = records_difference(records, decoded)
        if difference is not None:
            raise RuntimeError(f"{method} failed exact round trip: {difference}")

    deterministic = len(set(hashes)) == 1
    return {
        "archive_size_samples_bytes": sizes,
        "archive_size_bytes": sizes[0],
        "archive_sha256_samples": hashes,
        "archive_sha256": hashes[0],
        "deterministic_output": deterministic,
        "exact_round_trip_validated": True,
        "encode": _timing_summary(encode_samples),
        "decode": _timing_summary(decode_samples),
        "first_output": {
            "kind": "first_complete_nonempty_v3_chunk" if method == "jzpack-writer" else "complete_archive",
            "samples_seconds": first_output_samples,
            "median_seconds": statistics.median(first_output_samples),
            "records_in_first_chunk_samples": first_chunk_record_counts if method == "jzpack-writer" else None,
        },
        "phase_samples_seconds": phases,
    }


def _case_definitions(suite: str, large_string_bytes: int) -> list[dict[str, Any]]:
    if suite == "smoke":
        return [
            {"profile": "schema-diverse", "records": 37},
            {"profile": "nested-records", "records": 37},
            {"profile": "large-strings", "records": 3, "large_string_bytes": 128},
        ]
    return [
        {"profile": "schema-diverse", "records": 10_000},
        {"profile": "schema-diverse", "records": 50_000},
        {"profile": "nested-records", "records": 10_000},
        {"profile": "nested-records", "records": 40_000},
        {"profile": "large-strings", "records": 128, "large_string_bytes": large_string_bytes},
        {"profile": "large-strings", "records": 512, "large_string_bytes": large_string_bytes},
    ]


def _distinct_schema_count(records: Iterable[dict[str, Any]]) -> int:
    return len({tuple(sorted(record)) for record in records})


def _run_memory_worker(method: str, profile: str, count: int, seed: int, string_bytes: int, level: int) -> dict[str, Any]:
    started = time.perf_counter()
    if method == "jzpack-memory":
        payload = compress(
            iter_records(profile, count, seed, large_string_bytes=string_bytes), level=level
        )
        result = {"archive_size_bytes": len(payload), "archive_sha256": hashlib.sha256(payload).hexdigest()}
    elif method == "jzpack-writer":
        sink = _ObservedSink(retain=False, started_at=started)
        write_records(
            iter_records(profile, count, seed, large_string_bytes=string_bytes),
            sink,
            compression_level=level,
        )
        result = {
            "archive_size_bytes": sink.size_bytes,
            "archive_sha256": sink.sha256,
            "first_chunk_seconds": sink.first_chunk_seconds,
            "first_chunk_records": sink.first_chunk_records,
        }
    elif method == "msgpack-zstd":
        records = build_records(profile, count, seed, large_string_bytes=string_bytes)
        packed = msgpack.packb(records, use_bin_type=True)
        payload = zstd.ZstdCompressor(level=level, write_checksum=True).compress(packed)
        result = {"archive_size_bytes": len(payload), "archive_sha256": hashlib.sha256(payload).hexdigest()}
    elif method == "parquet-zstd":
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is unavailable")
        records = build_records(profile, count, seed, large_string_bytes=string_bytes)
        payload, _ = _encode_once(method, records, level)
        result = {"archive_size_bytes": len(payload), "archive_sha256": hashlib.sha256(payload).hexdigest()}
    else:
        raise ValueError(f"unknown memory worker implementation: {method}")
    result["elapsed_seconds_with_tracemalloc"] = time.perf_counter() - started
    return result


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _memory_worker(method: str, profile: str, count: int, seed: int, string_bytes: int, level: int) -> dict[str, Any]:
    warmup_count = min(count, 128)
    _run_memory_worker(method, profile, warmup_count, seed, string_bytes, level)
    baseline_rss = _peak_rss_bytes()
    tracemalloc.start()
    try:
        result = _run_memory_worker(method, profile, count, seed, string_bytes, level)
        _, traced_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    peak_rss = _peak_rss_bytes()
    result.update(
        {
            "method": method,
            "profile": profile,
            "record_count": count,
            "warmup_records": warmup_count,
            "python_tracemalloc_peak_bytes": traced_peak,
            "process_rss_peak_bytes_including_startup_and_warmup": peak_rss,
            "process_rss_peak_delta_after_warmup_bytes": (
                max(0, peak_rss - baseline_rss)
                if peak_rss is not None and baseline_rss is not None
                else None
            ),
            "rss_available": peak_rss is not None,
        }
    )
    return result


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    path_parts = [str(_REPOSITORY)]
    if environment.get("PYTHONPATH"):
        path_parts.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(path_parts)
    return environment


def _run_isolated_memory_samples(
    cases: list[dict[str, Any]], samples: int, seed: int, level: int, string_bytes: int
) -> list[dict[str, Any]]:
    largest_case: dict[str, dict[str, Any]] = {}
    for case in cases:
        profile = case["profile"]
        if profile not in largest_case or case["records"] > largest_case[profile]["records"]:
            largest_case[profile] = case

    reports: list[dict[str, Any]] = []
    for profile, case in largest_case.items():
        records = build_records(
            profile,
            case["records"],
            seed,
            large_string_bytes=case.get("large_string_bytes", string_bytes),
        )
        eligibility = _parquet_eligibility(records)
        methods = ["jzpack-memory", "jzpack-writer", "msgpack-zstd"]
        if eligibility["eligible"]:
            methods.append("parquet-zstd")
        for method in methods:
            samples_for_method = []
            for _ in range(samples):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    _WORKER_FLAG,
                    method,
                    profile,
                    str(case["records"]),
                    str(seed),
                    str(case.get("large_string_bytes", string_bytes)),
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
                    raise RuntimeError(f"isolated memory worker failed: {completed.stderr.strip()}")
                samples_for_method.append(json.loads(completed.stdout))
            reports.append(
                {
                    "profile": profile,
                    "record_count": case["records"],
                    "method": method,
                    "samples": samples_for_method,
                }
            )
    return reports


def run_benchmark(
    *,
    suite: str = "wave-3",
    samples: int = 3,
    warmups: int = 1,
    seed: int = DEFAULT_SEED,
    level: int = 3,
    large_string_bytes: int = DEFAULT_LARGE_STRING_BYTES,
    memory_samples: int = 3,
    measure_memory: bool = True,
) -> dict[str, Any]:
    if suite not in ("smoke", "wave-3"):
        raise ValueError("suite must be smoke or wave-3")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("samples must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    if isinstance(memory_samples, bool) or not isinstance(memory_samples, int) or memory_samples < 1:
        raise ValueError("memory_samples must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 22:
        raise ValueError("level must be an integer from 1 through 22")

    cases = _case_definitions(suite, large_string_bytes)
    workloads: list[dict[str, Any]] = []
    for case in cases:
        profile = case["profile"]
        count = case["records"]
        string_bytes = case.get("large_string_bytes", large_string_bytes)
        records = build_records(profile, count, seed, large_string_bytes=string_bytes)
        input_bytes, fingerprint = corpus_fingerprint(records)
        parquet_status = _parquet_eligibility(records)
        methods = ["jzpack-memory", "jzpack-writer", "msgpack-zstd"]
        if parquet_status["eligible"]:
            methods.append("parquet-zstd")
        implementations = {
            method: _implementation_samples(method, records, samples, warmups, level)
            for method in methods
        }
        if profile == "schema-diverse" and parquet_status["eligible"]:
            raise RuntimeError("schema-diverse corpus unexpectedly passed Parquet exactness preflight")
        workloads.append(
            {
                "profile": profile,
                "record_count": count,
                "large_string_bytes_per_record": string_bytes if profile == "large-strings" else None,
                "canonical_input_size_bytes": input_bytes,
                "input_sha256": fingerprint,
                "distinct_top_level_schemas": _distinct_schema_count(records),
                "parquet_eligibility": parquet_status,
                "implementations": implementations,
            }
        )

    memory = (
        _run_isolated_memory_samples(cases, memory_samples, seed, level, large_string_bytes)
        if measure_memory
        else []
    )
    if measure_memory:
        expected_hashes = {
            (workload["profile"], workload["record_count"], method): result["archive_sha256"]
            for workload in workloads
            for method, result in workload["implementations"].items()
        }
        for probe in memory:
            expected_hash = expected_hashes[(probe["profile"], probe["record_count"], probe["method"])]
            for sample in probe["samples"]:
                if sample["archive_sha256"] != expected_hash:
                    raise RuntimeError(
                        f"isolated memory output differs from timed output for {probe['method']}"
                    )
    return {
        "benchmark": BENCHMARK_NAME,
        "corpus": {
            "version": CORPUS_VERSION,
            "seed": seed,
            "profiles": list(PROFILE_NAMES),
            "generator": "deterministic splitmix/hash-shake synthetic records",
        },
        "configuration": {
            "suite": suite,
            "samples": samples,
            "warmups": warmups,
            "compression_level": level,
            "large_string_bytes": large_string_bytes,
            "memory_samples_per_largest_profile_case": memory_samples if measure_memory else 0,
            "memory_measured_in_isolated_processes": measure_memory,
        },
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "jzpack_version": jzpack_version,
            "benchmark_source_sha256": _source_sha256(Path(__file__).resolve()),
            "corpus_source_sha256": _source_sha256(Path(__file__).with_name("corpus_wave3.py")),
            "dependencies": {
                "msgpack": _distribution_version("msgpack"),
                "zstandard": _distribution_version("zstandard"),
                "pyarrow": _distribution_version("pyarrow"),
            },
        },
        "workloads": workloads,
        "memory_probes": memory,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare larger deterministic event corpora and streaming output.")
    parser.add_argument("--suite", choices=("smoke", "wave-3"), default="wave-3")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--large-string-bytes", type=int, default=DEFAULT_LARGE_STRING_BYTES)
    parser.add_argument("--memory-samples", type=int, default=3)
    parser.add_argument("--skip-memory", action="store_true")
    return parser


def _main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == _WORKER_FLAG:
        try:
            _, method, profile, count, seed, string_bytes, level = argv
            result = _memory_worker(method, profile, int(count), int(seed), int(string_bytes), int(level))
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        except Exception as exc:
            print(f"benchmark worker failed: {type(exc).__name__}", file=sys.stderr)
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
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
