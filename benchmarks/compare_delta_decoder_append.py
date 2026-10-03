"""Paired comparison for the list-backed DeltaEncoder.decode append fast path.

Run from the repository root with the benchmark dependencies installed:

    PYTHONPATH=. python benchmarks/compare_delta_decoder_append.py

The baseline decoder is loaded from the pinned source revision. The report
stores raw alternating samples, corpus/archive fingerprints, and allocation
peaks for both direct codec calls and whole-package decompression.
"""

from __future__ import annotations

import argparse
import ast
import gc
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import time
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zstandard as zstd

from benchmarks.benchmark_corpus import _same_value
from benchmarks.corpus import DEFAULT_SEED, PROFILE_NAMES, build_records, corpus_fingerprint
from jzpack import JZPackCompressor, encoders
from jzpack import __version__ as jzpack_version
from jzpack.encoders import DeltaEncoder
from jzpack.errors import ResourceLimitError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "cfdce1d7f2afe1e251ca26753b06a74fe6fa8aa4"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "benchmarks/results/delta-decoder-append-paired.json"
RECORD_COUNTS = (10_000, 50_000)
PAIR_COUNT = 15
WARMUPS = 2
DIRECT_FIELDS = ("sequence", "timestamp_ns", "counter")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    ).stdout


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _baseline_decoder() -> tuple[Callable[..., list[Any]], bytes]:
    source = _git("show", f"{BASE_REVISION}:jzpack/encoders.py")
    module = ast.parse(source, filename=f"{BASE_REVISION}:jzpack/encoders.py")
    delta_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DeltaEncoder"
    )
    decode_node = next(
        node for node in delta_class.body if isinstance(node, ast.FunctionDef) and node.name == "decode"
    )
    decode_node.decorator_list = []
    namespace: dict[str, Any] = {
        "Any": Any,
        "ResourceLimitError": ResourceLimitError,
        "MIN_SUPPORTED_INTEGER": encoders.MIN_SUPPORTED_INTEGER,
        "MAX_SUPPORTED_INTEGER": encoders.MAX_SUPPORTED_INTEGER,
    }
    baseline_module = ast.Module(body=[decode_node], type_ignores=[])
    ast.fix_missing_locations(baseline_module)
    exec(compile(baseline_module, f"{BASE_REVISION}:DeltaEncoder.decode", "exec"), namespace)
    return namespace["decode"], source


@contextmanager
def _using_decoder(decoder: Callable[..., list[Any]]) -> Iterator[None]:
    original = DeltaEncoder.decode
    DeltaEncoder.decode = staticmethod(decoder)
    try:
        yield
    finally:
        DeltaEncoder.decode = staticmethod(original)


def _timing(samples: list[int]) -> dict[str, Any]:
    return {
        "samples_ns": samples,
        "median_ns": statistics.median(samples),
        "minimum_ns": min(samples),
        "maximum_ns": max(samples),
    }


def _paired_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    changes = [
        (pair["elapsed_ns"]["candidate"] / pair["elapsed_ns"]["baseline"] - 1.0) * 100.0
        for pair in samples
    ]
    return {
        "paired_change_percent_samples": changes,
        "paired_change_percent_median": statistics.median(changes),
        "paired_candidate_faster_pairs": sum(change < 0 for change in changes),
    }


def _allocation_peak(workload: Callable[[], Any]) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        workload()
        _, peak = tracemalloc.get_traced_memory()
        return peak
    finally:
        tracemalloc.stop()


def _time_direct_once(
    decoder: Callable[..., list[Any]], base: Any, deltas: list[Any], expected: list[Any]
) -> int:
    with _using_decoder(decoder):
        started = time.perf_counter_ns()
        actual = DeltaEncoder.decode(base, deltas, max_output_size=len(expected))
        elapsed = time.perf_counter_ns() - started
    if not _same_value(expected, actual):
        raise AssertionError("direct DeltaEncoder.decode result differs")
    return elapsed


def _run_direct_cases(
    records_by_count: dict[int, list[dict[str, Any]]],
    baseline: Callable[..., list[Any]],
    candidate: Callable[..., list[Any]],
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for count, records in records_by_count.items():
        for field in DIRECT_FIELDS:
            values = [record[field] for record in records]
            base, deltas = DeltaEncoder.encode(values)
            timings = {"baseline": [], "candidate": []}
            for _ in range(WARMUPS):
                _time_direct_once(baseline, base, deltas, values)
                _time_direct_once(candidate, base, deltas, values)

            paired_samples = []
            for pair in range(PAIR_COUNT):
                order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                elapsed: dict[str, int] = {}
                for name in order:
                    decoder = baseline if name == "baseline" else candidate
                    elapsed[name] = _time_direct_once(decoder, base, deltas, values)
                    timings[name].append(elapsed[name])
                paired_samples.append({"pair": pair, "order": list(order), "elapsed_ns": elapsed})

            peaks: dict[str, int] = {}
            for name, decoder in (("baseline", baseline), ("candidate", candidate)):
                def decode() -> list[Any]:
                    with _using_decoder(decoder):
                        return DeltaEncoder.decode(base, deltas, max_output_size=len(values))

                result = decode()
                if not _same_value(values, result):
                    raise AssertionError("direct allocation probe result differs")
                peaks[name] = _allocation_peak(decode)

            baseline_timing = _timing(timings["baseline"])
            candidate_timing = _timing(timings["candidate"])
            workloads[f"{field}:{count}"] = {
                "field": field,
                "record_count": count,
                "input_type": type(values[0]).__name__,
                "delta_count": len(deltas),
                "validated_output_limit": len(values),
                "expected_output_sha256": _sha256(
                    json.dumps(values, separators=(",", ":")).encode("utf-8")
                ),
                "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
                "ratio_of_sample_medians_change_percent": (
                    candidate_timing["median_ns"] / baseline_timing["median_ns"] - 1.0
                )
                * 100.0,
                "paired_samples": paired_samples,
                **_paired_metrics(paired_samples),
                "tracemalloc_peak_bytes": peaks,
                "exact_result_validated_outside_timer": True,
            }
    return workloads


def _time_public_once(
    decoder: Callable[..., list[Any]],
    compressor: JZPackCompressor,
    archive: bytes,
    records: list[dict[str, Any]],
) -> int:
    with _using_decoder(decoder):
        started = time.perf_counter_ns()
        actual = compressor.decompress(archive)
        elapsed = time.perf_counter_ns() - started
    if not _same_value(records, actual):
        raise AssertionError("whole-package exact round trip differs")
    return elapsed


def _delta_decode_stats(
    decoder: Callable[..., list[Any]],
    compressor: JZPackCompressor,
    archive: bytes,
    expected: list[dict[str, Any]],
) -> dict[str, int | str]:
    stats = {"calls": 0, "delta_values": 0}

    def counting_decoder(base: Any, deltas: list[Any], max_output_size: int | None = None) -> list[Any]:
        stats["calls"] += 1
        stats["delta_values"] += len(deltas)
        return decoder(base, deltas, max_output_size)

    with _using_decoder(counting_decoder):
        actual = compressor.decompress(archive)
    if not _same_value(expected, actual):
        raise AssertionError("whole-package output differs during call-count probe")
    output_size, output_sha = corpus_fingerprint(actual)
    return {**stats, "output_size_bytes": output_size, "output_sha256": output_sha}


def _run_public_cases(
    records_by_count: dict[int, list[dict[str, Any]]],
    baseline: Callable[..., list[Any]],
    candidate: Callable[..., list[Any]],
    level: int,
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for count, records in records_by_count.items():
        for profile in PROFILE_NAMES:
            if profile == "integer-series":
                workload_records = records
            else:
                workload_records = build_records(profile, count, DEFAULT_SEED)
            reference_size, reference_sha = corpus_fingerprint(workload_records)
            compressor = JZPackCompressor(compression_level=level)
            archive = compressor.compress(workload_records)
            archive_sha = _sha256(archive)
            baseline_stats = _delta_decode_stats(baseline, compressor, archive, workload_records)
            candidate_stats = _delta_decode_stats(candidate, compressor, archive, workload_records)
            for _ in range(WARMUPS):
                _time_public_once(baseline, compressor, archive, workload_records)
                _time_public_once(candidate, compressor, archive, workload_records)

            timings = {"baseline": [], "candidate": []}
            paired_samples = []
            for pair in range(PAIR_COUNT):
                order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                elapsed: dict[str, int] = {}
                for name in order:
                    decoder = baseline if name == "baseline" else candidate
                    elapsed[name] = _time_public_once(decoder, compressor, archive, workload_records)
                    timings[name].append(elapsed[name])
                paired_samples.append({"pair": pair, "order": list(order), "elapsed_ns": elapsed})

            peaks: dict[str, int] = {}
            for name, decoder in (("baseline", baseline), ("candidate", candidate)):
                def decode() -> list[dict[str, Any]]:
                    with _using_decoder(decoder):
                        return compressor.decompress(archive)

                actual = decode()
                if not _same_value(workload_records, actual):
                    raise AssertionError("whole-package allocation probe result differs")
                peaks[name] = _allocation_peak(decode)

            baseline_timing = _timing(timings["baseline"])
            candidate_timing = _timing(timings["candidate"])
            workloads[f"{profile}:{count}"] = {
                "profile": profile,
                "record_count": len(workload_records),
                "reference_input_size_bytes": reference_size,
                "reference_input_sha256": reference_sha,
                "archive_size_bytes": len(archive),
                "archive_sha256": archive_sha,
                "delta_decode_calls": baseline_stats["calls"],
                "delta_values_decoded": baseline_stats["delta_values"],
                "candidate_delta_decode_calls": candidate_stats["calls"],
                "baseline_output_size_bytes": baseline_stats["output_size_bytes"],
                "candidate_output_size_bytes": candidate_stats["output_size_bytes"],
                "baseline_output_sha256": baseline_stats["output_sha256"],
                "candidate_output_sha256": candidate_stats["output_sha256"],
                "exact_output_hash_match": baseline_stats["output_sha256"]
                == candidate_stats["output_sha256"],
                "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
                "ratio_of_sample_medians_change_percent": (
                    candidate_timing["median_ns"] / baseline_timing["median_ns"] - 1.0
                )
                * 100.0,
                "paired_samples": paired_samples,
                **_paired_metrics(paired_samples),
                "tracemalloc_peak_bytes": peaks,
                "exact_result_validated_outside_timer": True,
            }
    return workloads


def run_comparison(level: int = 3) -> dict[str, Any]:
    baseline, baseline_source = _baseline_decoder()
    candidate = DeltaEncoder.decode
    records_by_count = {
        count: build_records("integer-series", count, DEFAULT_SEED) for count in RECORD_COUNTS
    }
    source_patch = _git("diff", "--binary", "--", "jzpack/encoders.py", "tests/test_codec_fidelity.py")
    current_source = (REPOSITORY_ROOT / "jzpack/encoders.py").read_bytes()
    script_source = Path(__file__).read_bytes()
    input_hashes = {
        str(count): corpus_fingerprint(records)[1] for count, records in records_by_count.items()
    }
    return {
        "benchmark": "jzpack-delta-decoder-append-paired",
        "created_utc": datetime.now(UTC).isoformat(),
        "baseline_revision": BASE_REVISION,
        "baseline_source_sha256": _sha256(baseline_source),
        "implementation_patch_sha256": _sha256(source_patch),
        "candidate_source_sha256": _sha256(current_source),
        "benchmark_script_sha256": _sha256(script_source),
        "corpus": {
            "name": "deterministic-synthetic-json-events",
            "version": 1,
            "seed": DEFAULT_SEED,
            "profiles": list(PROFILE_NAMES),
            "record_counts": list(RECORD_COUNTS),
            "integer_series_input_sha256": input_hashes,
        },
        "configuration": {
            "compression_level": level,
            "alternating_pairs_per_case": PAIR_COUNT,
            "warmups_per_case": WARMUPS,
            "order": "alternating baseline-first and candidate-first by pair",
            "exact_oracle": "benchmarks.benchmark_corpus._same_value, outside timed intervals",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "jzpack_version": jzpack_version,
            "dependencies": {
                package: _version(package) for package in ("msgpack", "zstandard", "orjson")
            },
            "zstandard_native_version": ".".join(str(part) for part in zstd.ZSTD_VERSION),
        },
        "memory_metric": (
            "tracemalloc peak bytes for one direct decode or whole-package decompress; inputs are prebuilt, "
            "reconstructed outputs included, native allocations excluded; RSS not measured"
        ),
        "direct_integer_column_decodes": _run_direct_cases(records_by_count, baseline, candidate),
        "whole_package_decompress": _run_public_cases(records_by_count, baseline, candidate, level),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, choices=range(1, 23), default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_comparison(args.level)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote paired DeltaEncoder.decode comparison to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
