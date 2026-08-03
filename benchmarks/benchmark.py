from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import math
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Sequence
from typing import Any

from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress

BUDGET_FAILURE_EXIT_CODE = 1
BENCHMARK_FAILURE_EXIT_CODE = 3
REFERENCE_INPUT_SIZE_DEFINITION = (
    "sum of UTF-8 bytes in canonical JSON records (sorted keys, compact separators, no newline)"
)


def build_records(count: int) -> list[dict[str, object]]:
    if count < 0:
        raise ValueError("record count must be non-negative")
    statuses = ("active", "inactive", "pending", "error")
    return [
        {
            "id": index,
            "service": "api-gateway",
            "status": statuses[index % len(statuses)],
            "latency_ms": index % 100,
        }
        for index in range(count)
    ]


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


def _compression_level(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= parsed <= 22:
        raise argparse.ArgumentTypeError("must be between 1 and 22")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite, non-negative number")
    return parsed


def _dependency_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _canonical_json_size(records: Sequence[dict[str, object]]) -> int:
    return sum(
        len(
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        for record in records
    )


def _measure(workload: Callable[[], Any]) -> tuple[Any, float, float]:
    tracemalloc.start()
    try:
        started = time.perf_counter()
        result = workload()
        elapsed = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, elapsed, peak_bytes / (1024 * 1024)


def _timing_statistics(samples: list[float]) -> dict[str, object]:
    return {
        "average_seconds": statistics.mean(samples),
        "minimum_seconds": min(samples),
        "maximum_seconds": max(samples),
        "stdev_seconds": statistics.pstdev(samples),
        "samples_seconds": samples,
    }


def _budget_status(
    compression_average: float,
    decompression_average: float,
    peak_python_memory_mib: float,
    max_compress_seconds: float | None,
    max_decompress_seconds: float | None,
    max_memory_mib: float | None,
) -> dict[str, object]:
    checks = {
        "max-compress-seconds": (
            None if max_compress_seconds is None else compression_average <= max_compress_seconds
        ),
        "max-decompress-seconds": (
            None if max_decompress_seconds is None else decompression_average <= max_decompress_seconds
        ),
        "max-memory-mib": None if max_memory_mib is None else peak_python_memory_mib <= max_memory_mib,
    }
    failed_budgets = [name for name, passed in checks.items() if passed is False]
    return {
        "passed": not failed_budgets,
        "failed_budgets": failed_budgets,
        "checks": checks,
        "max_compress_seconds": max_compress_seconds,
        "max_decompress_seconds": max_decompress_seconds,
        "max_memory_mib": max_memory_mib,
    }


def run_benchmark(
    records: list[dict[str, object]],
    level: int,
    iterations: int,
    *,
    max_compress_seconds: float | None = None,
    max_decompress_seconds: float | None = None,
    max_memory_mib: float | None = None,
) -> dict[str, object]:
    """Run the measured round trips and return a JSON-safe result object."""
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if not 1 <= level <= 22:
        raise ValueError("level must be between 1 and 22")
    for name, budget in (
        ("max_compress_seconds", max_compress_seconds),
        ("max_decompress_seconds", max_decompress_seconds),
        ("max_memory_mib", max_memory_mib),
    ):
        if budget is not None and (not math.isfinite(budget) or budget < 0):
            raise ValueError(f"{name} must be finite and non-negative")

    expected_records = copy.deepcopy(records)
    reference_input_bytes = _canonical_json_size(records)
    compressed_sizes: list[int] = []
    compression_seconds: list[float] = []
    decompression_seconds: list[float] = []
    compression_peaks: list[float] = []
    decompression_peaks: list[float] = []

    for _ in range(iterations):
        compressed, elapsed, peak_mib = _measure(lambda: compress(records, level=level))
        compression_seconds.append(elapsed)
        compression_peaks.append(peak_mib)
        compressed_sizes.append(len(compressed))

        if records != expected_records:
            raise AssertionError("compression mutated the benchmark source records")

        restored, elapsed, peak_mib = _measure(lambda: decompress(compressed))
        decompression_seconds.append(elapsed)
        decompression_peaks.append(peak_mib)

        assert restored == expected_records, "round-trip validation failed"
        if records != expected_records:
            raise AssertionError("benchmark workload mutated the source records")

    peak_python_memory_mib = max(compression_peaks + decompression_peaks)
    budget_status = _budget_status(
        statistics.mean(compression_seconds),
        statistics.mean(decompression_seconds),
        peak_python_memory_mib,
        max_compress_seconds,
        max_decompress_seconds,
        max_memory_mib,
    )

    return {
        "benchmark": "jzpack",
        "configuration": {
            "records": len(records),
            "level": level,
            "iterations": iterations,
        },
        "dataset": {
            "record_count": len(records),
            "reference_input_size_bytes": reference_input_bytes,
            "reference_input_size_definition": REFERENCE_INPUT_SIZE_DEFINITION,
            "compressed_size_bytes": compressed_sizes[0],
            "compressed_size_min_bytes": min(compressed_sizes),
            "compressed_size_max_bytes": max(compressed_sizes),
            "compressed_size_samples_bytes": compressed_sizes,
        },
        "runtime": {
            "jzpack_version": jzpack_version,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {
                "msgpack": _dependency_version("msgpack"),
                "zstandard": _dependency_version("zstandard"),
            },
        },
        "timings": {
            "compression": _timing_statistics(compression_seconds),
            "decompression": _timing_statistics(decompression_seconds),
            "clock": "time.perf_counter seconds",
        },
        "memory": {
            "metric": "peak_python_memory_mib",
            "peak_python_memory_mib": peak_python_memory_mib,
            "compression_peak_python_memory_mib": max(compression_peaks),
            "decompression_peak_python_memory_mib": max(decompression_peaks),
            "compression_peak_samples_mib": compression_peaks,
            "decompression_peak_samples_mib": decompression_peaks,
            "includes": "Python allocations traced by tracemalloc during compression and decompression",
            "excludes": "native allocations such as zstandard workspace memory and total process RSS",
        },
        "round_trip_validated": True,
        "budget_status": budget_status,
    }


def _format_mib(byte_count: int | float) -> str:
    return f"{byte_count / (1024 * 1024):.2f} MiB"


def _print_human(result: dict[str, object]) -> None:
    configuration = result["configuration"]
    dataset = result["dataset"]
    timings = result["timings"]
    memory = result["memory"]
    budget_status = result["budget_status"]
    compression = timings["compression"]
    decompression = timings["decompression"]

    print(f"records: {configuration['records']:,}")
    print(f"iterations: {configuration['iterations']}")
    print(f"compression level: {configuration['level']}")
    print(f"compressed size: {_format_mib(dataset['compressed_size_bytes'])}")
    print(f"reference input size: {_format_mib(dataset['reference_input_size_bytes'])} (canonical JSON UTF-8)")
    print(
        "compression: "
        f"average {compression['average_seconds']:.6f}s, "
        f"min {compression['minimum_seconds']:.6f}s, "
        f"max {compression['maximum_seconds']:.6f}s, "
        f"stdev {compression['stdev_seconds']:.6f}s"
    )
    print(
        "decompression: "
        f"average {decompression['average_seconds']:.6f}s, "
        f"min {decompression['minimum_seconds']:.6f}s, "
        f"max {decompression['maximum_seconds']:.6f}s, "
        f"stdev {decompression['stdev_seconds']:.6f}s"
    )
    print(f"compression peak Python allocation: {memory['compression_peak_python_memory_mib']:.2f} MiB")
    print(f"decompression peak Python allocation: {memory['decompression_peak_python_memory_mib']:.2f} MiB")
    print(f"peak Python allocation: {memory['peak_python_memory_mib']:.2f} MiB")

    failed_budgets = budget_status["failed_budgets"]
    budget_limits = {
        "max-compress-seconds": budget_status["max_compress_seconds"],
        "max-decompress-seconds": budget_status["max_decompress_seconds"],
        "max-memory-mib": budget_status["max_memory_mib"],
    }
    if failed_budgets:
        print("budget status: FAILED")
        for name in failed_budgets:
            print(f"budget failed: {name} (limit: {budget_limits[name]})")
    elif any(limit is not None for limit in budget_limits.values()):
        print("budget status: PASSED")
    else:
        print("budget status: not requested")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark jzpack round trips. CPU budgets apply to average "
            "time measured with time.perf_counter(). The memory budget applies "
            "to the maximum peak_python_memory_mib across all iterations; this "
            "is tracemalloc Python allocation, not total process RSS."
        )
    )
    parser.add_argument(
        "--records",
        type=_non_negative_int,
        default=100_000,
        help="number of deterministic records to generate (default: 100000)",
    )
    parser.add_argument(
        "--level",
        type=_compression_level,
        default=3,
        help="zstandard compression level from 1 through 22 (default: 3)",
    )
    parser.add_argument(
        "--iterations",
        type=_positive_int,
        default=3,
        help="number of measured round trips (default: 3)",
    )
    parser.add_argument(
        "--max-compress-seconds",
        type=_non_negative_float,
        help="fail if average compression time exceeds this number of seconds",
    )
    parser.add_argument(
        "--max-decompress-seconds",
        type=_non_negative_float,
        help="fail if average decompression time exceeds this number of seconds",
    )
    parser.add_argument(
        "--max-memory-mib",
        type=_non_negative_float,
        help="fail if maximum peak_python_memory_mib exceeds this MiB value",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write exactly one machine-readable JSON object to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = build_records(args.records)
        result = run_benchmark(
            records,
            level=args.level,
            iterations=args.iterations,
            max_compress_seconds=args.max_compress_seconds,
            max_decompress_seconds=args.max_decompress_seconds,
            max_memory_mib=args.max_memory_mib,
        )
    except AssertionError as exc:
        print(f"benchmark execution failed: {exc}", file=sys.stderr)
        return BENCHMARK_FAILURE_EXIT_CODE
    except Exception as exc:
        print(f"benchmark execution failed: {exc}", file=sys.stderr)
        return BENCHMARK_FAILURE_EXIT_CODE

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        _print_human(result)

    budget_status = result["budget_status"]
    if budget_status["failed_budgets"]:
        if args.json:
            failed = ", ".join(budget_status["failed_budgets"])
            print(f"benchmark budget failed: {failed}", file=sys.stderr)
        return BUDGET_FAILURE_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
