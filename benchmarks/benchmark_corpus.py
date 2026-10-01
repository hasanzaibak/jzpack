"""Comparative benchmarks for the versioned synthetic event corpus.

This CLI is separate from ``benchmark.py`` so its JSON and budget contract stay
stable for existing users of the original benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msgpack
import zstandard as zstd

from benchmarks.corpus import (
    CORPUS_VERSION,
    DEFAULT_SEED,
    PROFILE_NAMES,
    REFERENCE_INPUT_SIZE_DEFINITION,
    build_records,
    corpus_fingerprint,
)
from jzpack import JZPackCompressor
from jzpack import __version__ as jzpack_version

try:
    import orjson
except ImportError:  # Optional fast JSON comparison dependency.
    orjson = None

BENCHMARK_NAME = "jzpack-comparative-corpus"
BENCHMARK_FAILURE_EXIT_CODE = 3
_REPOSITORY = Path(__file__).resolve().parents[1]


def _dependency_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _same_value(expected: Any, actual: Any) -> bool:
    """Compare values recursively without Python's bool/int or float equality shortcuts."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, float):
        import struct

        return struct.pack(">d", expected) == struct.pack(">d", actual)
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(_same_value(expected[key], actual[key]) for key in expected)
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(_same_value(left, right) for left, right in zip(expected, actual))
    return expected == actual


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timing(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "samples_seconds": samples,
        "median_seconds": statistics.median(samples),
        "p95_seconds": ordered[p95_index],
        "minimum_seconds": ordered[0],
        "maximum_seconds": ordered[-1],
    }


def _allocation_peak(workload: Callable[[], Any]) -> float:
    tracemalloc.start()
    try:
        workload()
        _, peak = tracemalloc.get_traced_memory()
        return peak / (1024 * 1024)
    finally:
        tracemalloc.stop()


def _implementations(level: int) -> dict[str, dict[str, Callable[..., Any]]]:
    def new_jzpack() -> JZPackCompressor:
        return JZPackCompressor(compression_level=level)

    def new_msgpack_encoder() -> tuple[zstd.ZstdCompressor, zstd.ZstdDecompressor]:
        return zstd.ZstdCompressor(level=level, write_checksum=True), zstd.ZstdDecompressor()

    implementations: dict[str, dict[str, Callable[..., Any]]] = {
        "jzpack": {
            "new_context": new_jzpack,
            "encode": lambda context, records: context.compress(records),
            "decode": lambda context, payload: context.decompress(payload),
        },
        "msgpack-zstd": {
            "new_context": new_msgpack_encoder,
            "encode": lambda context, records: context[0].compress(msgpack.packb(records, use_bin_type=True)),
            "decode": lambda context, payload: msgpack.unpackb(
                context[1].decompress(payload, allow_extra_data=False), raw=False, strict_map_key=False
            ),
        },
    }
    if orjson is not None:
        implementations["orjson-zstd"] = {
            "new_context": new_msgpack_encoder,
            "encode": lambda context, records: context[0].compress(orjson.dumps(records)),
            "decode": lambda context, payload: orjson.loads(context[1].decompress(payload, allow_extra_data=False)),
        }
    return implementations


def _benchmark_implementation(
    implementation: dict[str, Callable[..., Any]],
    records: list[dict[str, Any]],
    iterations: int,
    warmups: int,
) -> dict[str, Any]:
    expected_size, expected_checksum = corpus_fingerprint(records)
    source_before = expected_checksum
    timings: dict[str, Any] = {}
    memory: dict[str, Any] = {}
    compressed_reference: bytes | None = None
    compressed_hashes: list[str] = []
    compressed_sizes: list[int] = []

    for mode in ("cold", "reused"):
        shared_context = implementation["new_context"]() if mode == "reused" else None

        def encode() -> bytes:
            context = shared_context if shared_context is not None else implementation["new_context"]()
            return implementation["encode"](context, records)

        for _ in range(warmups):
            encode()

        compression_samples = []
        mode_payloads = []
        for _ in range(iterations):
            started = time.perf_counter()
            compressed = encode()
            compression_samples.append(time.perf_counter() - started)
            mode_payloads.append(compressed)
            compressed_sizes.append(len(compressed))
            compressed_hashes.append(_sha256(compressed))

        if len(set(compressed_hashes)) != 1:
            raise AssertionError("benchmark implementation produced non-deterministic output")
        compressed_reference = mode_payloads[0]
        timings[f"compression_{mode}"] = _timing(compression_samples)
        memory[f"compression_{mode}_peak_python_mib"] = _allocation_peak(encode)

        decode_context = implementation["new_context"]() if mode == "reused" else None

        def decode() -> Any:
            context = decode_context if decode_context is not None else implementation["new_context"]()
            return implementation["decode"](context, compressed_reference)

        for _ in range(warmups):
            if not _same_value(records, decode()):
                raise AssertionError("exact round-trip validation failed during warmup")

        decompression_samples = []
        for _ in range(iterations):
            started = time.perf_counter()
            restored = decode()
            decompression_samples.append(time.perf_counter() - started)
            if not _same_value(records, restored):
                raise AssertionError("exact round-trip validation failed")

        timings[f"decompression_{mode}"] = _timing(decompression_samples)
        memory[f"decompression_{mode}_peak_python_mib"] = _allocation_peak(decode)

    if corpus_fingerprint(records)[1] != source_before:
        raise AssertionError("benchmark implementation mutated the source records")

    return {
        "reference_input_size_bytes": expected_size,
        "reference_input_sha256": expected_checksum,
        "compressed_size_bytes": compressed_sizes[0],
        "compressed_size_samples_bytes": compressed_sizes,
        "compressed_output_sha256": compressed_hashes[0],
        "exact_round_trip_validated": True,
        "timings": timings,
        "python_allocation_peaks_mib": memory,
    }


def _process_peak_rss_worker(profile: str, records_count: int, seed: int, level: int) -> dict[str, Any]:
    try:
        import resource
    except ImportError:
        return {"available": False, "reason": "resource module is unavailable on this platform"}

    records = build_records(profile, records_count, seed)
    compressor = JZPackCompressor(compression_level=level)
    packed = compressor.compress(records)
    restored = compressor.decompress(packed)
    if not _same_value(records, restored):
        raise AssertionError("exact round-trip validation failed in RSS worker")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        peak_bytes = int(peak)
    else:
        peak_bytes = int(peak * 1024)
    return {
        "available": True,
        "peak_process_rss_mib": peak_bytes / (1024 * 1024),
        "scope": "fresh-process jzpack round trip, including generated records and restored records",
    }


def _isolated_process_rss(profile: str, count: int, seed: int, level: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_rss-worker",
        profile,
        str(count),
        str(seed),
        str(level),
    ]
    process = subprocess.run(command, cwd=_REPOSITORY, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError("isolated RSS benchmark worker failed") from None
    return json.loads(process.stdout)


def run_benchmark(
    profiles: tuple[str, ...] = PROFILE_NAMES,
    records_count: int = 10_000,
    iterations: int = 3,
    warmups: int = 1,
    level: int = 3,
    seed: int = DEFAULT_SEED,
    *,
    measure_rss: bool = True,
) -> dict[str, Any]:
    if not profiles or any(profile not in PROFILE_NAMES for profile in profiles):
        raise ValueError("profiles must name one or more supported corpus profiles")
    if isinstance(records_count, bool) or records_count < 1:
        raise ValueError("record count must be at least 1")
    if isinstance(iterations, bool) or iterations < 1:
        raise ValueError("iterations must be at least 1")
    if isinstance(warmups, bool) or warmups < 0:
        raise ValueError("warmups must be non-negative")
    if isinstance(level, bool) or not 1 <= level <= 22:
        raise ValueError("level must be between 1 and 22")

    runtime = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "jzpack_version": jzpack_version,
        "dependencies": {
            "msgpack": _dependency_version("msgpack"),
            "zstandard": _dependency_version("zstandard"),
            "orjson": _dependency_version("orjson"),
        },
        "zstandard_native_version": ".".join(str(part) for part in zstd.ZSTD_VERSION),
    }
    implementations = _implementations(level)
    results: dict[str, Any] = {}
    for profile in profiles:
        records = build_records(profile, records_count, seed)
        reference_size, fingerprint = corpus_fingerprint(records)
        implementation_results = {
            name: _benchmark_implementation(implementation, records, iterations, warmups)
            for name, implementation in implementations.items()
        }
        entry = {
            "record_count": len(records),
            "reference_input_size_bytes": reference_size,
            "reference_input_sha256": fingerprint,
            "implementations": implementation_results,
        }
        if measure_rss:
            entry["jzpack_process_memory"] = _isolated_process_rss(profile, records_count, seed, level)
        results[profile] = entry

    return {
        "benchmark": BENCHMARK_NAME,
        "corpus": {
            "name": "deterministic-synthetic-json-events",
            "version": CORPUS_VERSION,
            "seed": seed,
            "record_count_per_profile": records_count,
            "profiles": list(profiles),
            "reference_input_size_definition": REFERENCE_INPUT_SIZE_DEFINITION,
        },
        "configuration": {"level": level, "iterations": iterations, "warmups": warmups},
        "runtime": runtime,
        "rss_metric": (
            "resource.getrusage(RUSAGE_SELF).ru_maxrss from one isolated jzpack round-trip process per profile"
            if measure_rss
            else "not measured"
        ),
        "workloads": results,
    }


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _level(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 22:
        raise argparse.ArgumentTypeError("must be between 1 and 22")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare jzpack and row-oriented baselines on deterministic event data.")
    parser.add_argument("--records", type=_positive_int, default=10_000)
    parser.add_argument("--iterations", type=_positive_int, default=3)
    parser.add_argument("--warmups", type=_non_negative_int, default=1)
    parser.add_argument("--level", type=_level, default=3)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--profiles", nargs="+", choices=PROFILE_NAMES, default=list(PROFILE_NAMES))
    parser.add_argument("--skip-rss", action="store_true", help="skip isolated process RSS measurement")
    parser.add_argument("--json", action="store_true", help="write one machine-readable JSON result to stdout")
    return parser


def _main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--_rss-worker":
        _, profile, count, seed, level = argv
        print(json.dumps(_process_peak_rss_worker(profile, int(count), int(seed), int(level)), sort_keys=True))
        return 0

    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run_benchmark(
            tuple(arguments.profiles),
            arguments.records,
            arguments.iterations,
            arguments.warmups,
            arguments.level,
            arguments.seed,
            measure_rss=not arguments.skip_rss,
        )
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}", file=sys.stderr)
        return BENCHMARK_FAILURE_EXIT_CODE

    if arguments.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        _print_human(result)
    return 0


def _print_human(result: dict[str, Any]) -> None:
    print(f"benchmark: {result['benchmark']} (corpus v{result['corpus']['version']})")
    print(f"records/profile: {result['corpus']['record_count_per_profile']:,}")
    for profile, workload in result["workloads"].items():
        print(f"\n{profile}: {workload['record_count']:,} records, {workload['reference_input_size_bytes']:,} canonical JSON bytes")
        for name, implementation in workload["implementations"].items():
            encode = implementation["timings"]["compression_reused"]
            decode = implementation["timings"]["decompression_reused"]
            print(
                f"  {name}: {implementation['compressed_size_bytes']:,} bytes; "
                f"encode median {encode['median_seconds']:.6f}s (p95 {encode['p95_seconds']:.6f}s); "
                f"decode median {decode['median_seconds']:.6f}s (p95 {decode['p95_seconds']:.6f}s)"
            )


if __name__ == "__main__":
    raise SystemExit(_main())
