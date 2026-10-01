"""Paired baseline/candidate decode timings for deterministic corpus profiles.

This development benchmark retrieves the baseline reconstruction method from
the pinned Git revision and verifies the complete source-file hash before use.
It times only ``decompress``; corpus creation, compression, and exact-value
validation are outside each timed interval.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import zstandard as zstd

from benchmarks.corpus import DEFAULT_SEED, PROFILE_NAMES, build_records, corpus_fingerprint
from jzpack import JZPackCompressor
from jzpack import __version__ as jzpack_version
from jzpack.schema import SchemaReconstructor
from tests.fidelity_oracle import is_faithful

BASE_REVISION = "3e87f770be076cac166caf7fd35de22c81c3782b"
BASE_SCHEMA_SHA256 = "31d875e4cbc48b4c2f5b1a1fb6f9350ef7e46cf11f6fbf4f54f5d33f17952971"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("benchmarks/results/cpu-wave3-decode-compare.json")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_method() -> Callable[..., dict[str, Any]]:
    result = subprocess.run(
        ["git", "show", f"{BASE_REVISION}:jzpack/schema.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    )
    source = result.stdout
    actual_hash = _sha256(source)
    if actual_hash != BASE_SCHEMA_SHA256:
        raise RuntimeError("pinned baseline schema source hash does not match the expected revision")

    namespace: dict[str, Any] = {"__name__": "_jzpack_cpu_wave3_baseline_schema"}
    exec(compile(source, f"{BASE_REVISION}:jzpack/schema.py", "exec"), namespace)
    return namespace["SchemaReconstructor"]._build_record


def _runtime_metadata() -> dict[str, Any]:
    try:
        msgpack_version = importlib.metadata.version("msgpack")
    except importlib.metadata.PackageNotFoundError:
        msgpack_version = "unavailable"
    try:
        zstandard_version = importlib.metadata.version("zstandard")
    except importlib.metadata.PackageNotFoundError:
        zstandard_version = "unavailable"

    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "jzpack_version": jzpack_version,
        "msgpack_version": msgpack_version,
        "zstandard_version": zstandard_version,
        "zstandard_native_version": list(zstd.ZSTD_VERSION),
    }


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _time_decode(
    decoder: JZPackCompressor,
    archive: bytes,
    records: list[dict[str, Any]],
    method: Callable[..., dict[str, Any]],
) -> int:
    SchemaReconstructor._build_record = method
    started_ns = time.perf_counter_ns()
    restored = decoder.decompress(archive)
    elapsed_ns = time.perf_counter_ns() - started_ns
    if not is_faithful(restored, records):
        raise AssertionError("exact round-trip validation failed")
    return elapsed_ns


def _timing(samples_ns: list[int]) -> dict[str, Any]:
    return {
        "samples_ns": samples_ns,
        "median_ns": statistics.median(samples_ns),
        "minimum_ns": min(samples_ns),
        "maximum_ns": max(samples_ns),
    }


def run_comparison(
    profiles: tuple[str, ...] = PROFILE_NAMES,
    records_count: int = 10_000,
    samples: int = 15,
    warmups: int = 1,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if not profiles or any(profile not in PROFILE_NAMES for profile in profiles):
        raise ValueError("profiles must name one or more supported corpus profiles")
    if isinstance(records_count, bool) or records_count < 1:
        raise ValueError("record count must be at least 1")
    if isinstance(samples, bool) or samples < 1:
        raise ValueError("sample count must be at least 1")
    if isinstance(warmups, bool) or warmups < 0:
        raise ValueError("warmups must be non-negative")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    baseline_method = _baseline_method()
    candidate_method = SchemaReconstructor._build_record
    baseline_source = subprocess.run(
        ["git", "show", f"{BASE_REVISION}:jzpack/schema.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    candidate_source = (REPOSITORY_ROOT / "jzpack/schema.py").read_bytes()
    harness_source = Path(__file__).read_bytes()

    workloads: dict[str, Any] = {}
    try:
        for profile in profiles:
            records = build_records(profile, records_count, seed)
            input_size, input_checksum = corpus_fingerprint(records)
            archive = JZPackCompressor(fast=False).compress(records)
            decoder = JZPackCompressor(fast=False)

            methods = (
                ("baseline", baseline_method),
                ("candidate", candidate_method),
            )
            for _, method in methods:
                for _ in range(warmups):
                    _time_decode(decoder, archive, records, method)

            samples_by_method: dict[str, list[int]] = {"baseline": [], "candidate": []}
            for sample_index in range(samples):
                ordered_methods = methods if sample_index % 2 == 0 else tuple(reversed(methods))
                for label, method in ordered_methods:
                    samples_by_method[label].append(_time_decode(decoder, archive, records, method))

            baseline_timing = _timing(samples_by_method["baseline"])
            candidate_timing = _timing(samples_by_method["candidate"])
            baseline_median = baseline_timing["median_ns"]
            candidate_median = candidate_timing["median_ns"]
            workloads[profile] = {
                "record_count": len(records),
                "reference_input_size_bytes": input_size,
                "reference_input_sha256": input_checksum,
                "archive_size_bytes": len(archive),
                "archive_sha256": _sha256(archive),
                "exact_round_trip_validated": True,
                "timings": {
                    "baseline": baseline_timing,
                    "candidate": candidate_timing,
                },
                "candidate_change_percent": (candidate_median / baseline_median - 1) * 100,
            }
    finally:
        SchemaReconstructor._build_record = candidate_method

    return {
        "benchmark": "jzpack-paired-reconstruction-decode",
        "schema_version": 1,
        "configuration": {
            "base_revision": BASE_REVISION,
            "profiles": list(profiles),
            "records_per_profile": records_count,
            "samples_per_method": samples,
            "warmups_per_method": warmups,
            "seed": seed,
            "timer": "time.perf_counter_ns",
            "timing_unit": "nanoseconds",
            "measurement_scope": "public JZPackCompressor.decompress only",
            "excluded_setup": [
                "deterministic record generation",
                "reference fingerprinting",
                "archive compression",
                "exact-value comparison",
            ],
            "order_policy": "baseline-first and candidate-first alternate by sample pair",
        },
        "metadata": {
            "runtime": _runtime_metadata(),
            "git_head": _git_head(),
            "baseline_schema_sha256": _sha256(baseline_source),
            "candidate_schema_sha256": _sha256(candidate_source),
            "benchmark_script_sha256": _sha256(harness_source),
        },
        "workloads": workloads,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", choices=PROFILE_NAMES, default=PROFILE_NAMES)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_comparison(
        profiles=tuple(args.profiles),
        records_count=args.records,
        samples=args.samples,
        warmups=args.warmups,
        seed=args.seed,
    )
    output_path = args.output if args.output.is_absolute() else REPOSITORY_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for profile, workload in report["workloads"].items():
        timings = workload["timings"]
        base_ms = timings["baseline"]["median_ns"] / 1_000_000
        candidate_ms = timings["candidate"]["median_ns"] / 1_000_000
        change = workload["candidate_change_percent"]
        print(f"{profile}: baseline {base_ms:.4f} ms; candidate {candidate_ms:.4f} ms; change {change:+.1f}%")
    print(f"raw report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
