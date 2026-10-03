"""Compare the baseline row builder with the shallow-path decoder specialization.

Run from the repository root with the project benchmark dependencies installed:

    PYTHONPATH=. python benchmarks/compare_schema_flat_fastpath.py

The command uses fixed deterministic archives, validates exact values and dictionary
insertion order outside each timer, and writes raw timing and memory samples to
benchmarks/results/schema-flat-fastpath-d5653-candidate.json.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import pickle
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None

from benchmarks.corpus_wave3 import build_records, corpus_fingerprint, records_difference
from jzpack.compressor import JZPackCompressor
from jzpack.schema import SchemaReconstructor

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY / "benchmarks/results/schema-flat-fastpath-d5653-candidate.json"
TEMP_ROOT = Path(
    os.environ.get(
        "JZPACK_SCHEMA_BENCH_TMPDIR",
        str(Path(tempfile.gettempdir()) / "jzpack-schema-flat-fastpath-d5653"),
    )
)
BASE_REVISION = "d5653b806f952fc602474409d4d0be83311c33c3"
SEED = 1729
CASES = (("schema-diverse", 50_000), ("nested-records", 40_000), ("homogeneous", 50_000))
VARIANTS = ("baseline", "candidate")
TIMING_PROCESSES = 3
SAMPLES_PER_PROCESS = 5
WARMUPS = 1
MEMORY_PROCESSES = 3
EXPECTED_ARCHIVE_SHA256 = {
    "schema-diverse": "bb7ccbed457684dd133d676b1aea03c8a848334c08aeb975c62ed6cf75862a65",
    "nested-records": "053f385471174124c4e8ba6ba5aa87a3d21cd4ee0f3b246ad8de60c8f4acd8c4",
    "homogeneous": "9b4a2296b97a0895236dbf68a07958be46e5f355c4f472ccf947a37a1ba4ca3c",
}

CURRENT_RECONSTRUCTOR = SchemaReconstructor.reconstruct_records


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    ).stdout


def _check_key_order(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: type differs"
    if type(expected) is dict:
        if list(expected) != list(actual):
            return f"{path}: dictionary key insertion order differs"
        for key in expected:
            failure = _check_key_order(expected[key], actual[key], f"{path}.{key}")
            if failure:
                return failure
    elif type(expected) is list:
        if len(expected) != len(actual):
            return f"{path}: list length differs"
        for index, (left, right) in enumerate(zip(expected, actual)):
            failure = _check_key_order(left, right, f"{path}[{index}]")
            if failure:
                return failure
    elif type(expected) is tuple:
        if len(expected) != len(actual):
            return f"{path}: tuple length differs"
        for index, (left, right) in enumerate(zip(expected, actual)):
            failure = _check_key_order(left, right, f"{path}[{index}]")
            if failure:
                return failure
    return None


def _validate(expected: list[dict[str, Any]], actual: list[dict[str, Any]], label: str) -> None:
    failure = records_difference(expected, actual) or _check_key_order(expected, actual)
    if failure:
        raise AssertionError(f"{label}: {failure}")


def _process_peak_rss_bytes() -> int | None:
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) * (1 if sys.platform == "darwin" else 1024)


def _baseline_reconstruct_records(
    self: SchemaReconstructor,
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Baseline implementation from BASE_REVISION, retained for paired comparison."""
    keys = [self._normalize_path(key) for key in schema.get("keys", [])]
    columns = schema.get("columns", {})
    num_records = schema.get("count")

    if num_records is None:
        num_records = len(columns[keys[0]]) if keys and columns else 0

    if not columns:
        return [{} for _ in range(num_records)]

    for key in keys:
        if key not in columns or len(columns[key]) != num_records:
            raise ValueError("Invalid JZPK payload: column length does not match row count")

    return [self._build_record(columns, keys, index) for index in range(num_records)]


def _set_variant(name: str) -> None:
    SchemaReconstructor.reconstruct_records = (
        _baseline_reconstruct_records if name == "baseline" else CURRENT_RECONSTRUCTOR
    )


def _build_homogeneous(count: int) -> list[dict[str, Any]]:
    rows = build_records("schema-diverse", count, SEED)
    defaults: dict[str, Any] = {}
    for field in range(12):
        if field % 4 == 0:
            defaults[f"optional_{field:02d}"] = 0
        elif field % 4 == 1:
            defaults[f"optional_{field:02d}"] = "group-00"
        elif field % 4 == 2:
            defaults[f"optional_{field:02d}"] = False
        else:
            defaults[f"optional_{field:02d}"] = 0.0
    ordered_keys = ["event_id", "tenant", "score", *defaults]
    return [{key: row[key] if key in row else defaults[key] for key in ordered_keys} for row in rows]


def _build_case(name: str, count: int) -> list[dict[str, Any]]:
    if name == "homogeneous":
        return _build_homogeneous(count)
    return build_records(name, count, SEED)


def _audit_contracts() -> dict[str, Any]:
    records = [
        {"z": 2**64 - 1, "branch": {"second": -0.0, "first": 7}, "literal.dot": None, "empty": {}},
        {"branch": {"first": 1}, "z": -(2**63)},
        {},
    ]
    archive = JZPackCompressor(fast=False).compress(records)
    _set_variant("baseline")
    expected = JZPackCompressor(fast=False).decompress(archive)
    if records_difference(records, expected):
        raise AssertionError("baseline contract archive changed record values")
    _set_variant("candidate")
    _validate(expected, JZPackCompressor(fast=False).decompress(archive), "public round trip")

    cases: dict[str, dict[str, Any]] = {
        "ordered_duplicate_flat_paths": {
            "keys": [("second",), ("first",), ("second",)],
            "columns": {("second",): [2, 4], ("first",): [1, 3]},
            "count": 2,
        },
        "empty_rows": {"keys": [("unused",)], "columns": {}, "count": 2},
        "zero_rows": {"keys": [("value",)], "columns": {("value",): []}, "count": 0},
        "bad_column_length": {"keys": [("value",)], "columns": {("value",): [1]}, "count": 2},
        "nested_mixed_order": {
            "keys": [("branch", "second"), ("z",), ("branch", "first")],
            "columns": {
                ("branch", "second"): [1, 2],
                ("z",): [3, 4],
                ("branch", "first"): [5, 6],
            },
            "count": 2,
        },
    }

    reconstructor = SchemaReconstructor()

    def outcome(method, schema):
        try:
            return ("ok", method(reconstructor, schema))
        except Exception as error:
            return ("error", type(error).__name__, str(error))

    for name, schema in cases.items():
        baseline = outcome(_baseline_reconstruct_records, schema)
        candidate = outcome(CURRENT_RECONSTRUCTOR, schema)
        if baseline[0] != candidate[0]:
            raise AssertionError(f"candidate changed {name} success/error status")
        if baseline[0] == "error":
            if baseline != candidate:
                raise AssertionError(f"candidate changed {name} exception: {baseline!r} != {candidate!r}")
        else:
            _validate(baseline[1], candidate[1], f"direct contract {name}")

    _set_variant("candidate")
    return {
        "public_round_trip": "passed: record order, exact types/float64 bits, nested/dotted paths, null/missing, empty maps",
        "direct_schema_contracts": {name: "candidate matches baseline values/order or exception type/message" for name in cases},
        "ordering_scope": "candidate preserves baseline schema-path iteration order; original input mapping key order is not a round-trip contract",
    }


def _worker(case_dir: Path, variant: str, mode: str, repeats: int = 0) -> dict[str, Any]:
    _set_variant(variant)
    archive = (case_dir / "archive.jzpk").read_bytes()
    decoder = JZPackCompressor(fast=False)
    if mode == "memory":
        gc.collect()
        tracemalloc.start()
        current_before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        rss_before = _process_peak_rss_bytes()
        result = decoder.decompress(archive)
        _current_after, peak = tracemalloc.get_traced_memory()
        rss_after = _process_peak_rss_bytes()
        measurement = {
            "decoded_records": len(result),
            "tracemalloc_peak_delta_bytes": max(0, peak - current_before),
            "process_peak_rss_delta_bytes": (
                max(0, rss_after - rss_before)
                if rss_before is not None and rss_after is not None
                else None
            ),
        }
        tracemalloc.stop()
        with (case_dir / "records.pickle").open("rb") as stream:
            expected = pickle.load(stream)
        _validate(expected, result, f"{variant} memory result")
        measurement["exact_validation"] = "passed outside memory measurement"
        return measurement

    with (case_dir / "records.pickle").open("rb") as stream:
        expected = pickle.load(stream)
    for _ in range(WARMUPS):
        _validate(expected, decoder.decompress(archive), f"{variant} warmup")
    samples: list[int] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        actual = decoder.decompress(archive)
        elapsed = time.perf_counter_ns() - started
        _validate(expected, actual, f"{variant} timed result")
        samples.append(elapsed)
    return {
        "samples_ns": samples,
        "median_ns": int(statistics.median(samples)),
        "exact_validations": len(samples) + WARMUPS,
        "key_order_validation": "passed outside timer",
    }


def _worker_main(argv: list[str]) -> None:
    mode, variant, case_name, case_dir, repeats = argv
    result = _worker(Path(case_dir), variant, mode, int(repeats))
    result.update({"worker_mode": mode, "variant": variant, "case": case_name})
    print(json.dumps(result, sort_keys=True))


def _run_worker(mode: str, variant: str, case: str, case_dir: Path, repeats: int = 0) -> dict[str, Any]:
    command = [sys.executable, str(Path(__file__).resolve()), "worker", mode, variant, case, str(case_dir), str(repeats)]
    process = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, check=False)
    if process.returncode:
        raise RuntimeError(f"{mode}/{variant}/{case} failed: {process.stderr.strip()}")
    return json.loads(process.stdout)


def _main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        _worker_main(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--contracts-only", action="store_true")
    args = parser.parse_args()

    head_revision = _git("rev-parse", "HEAD").decode().strip()
    merge_base = _git("merge-base", BASE_REVISION, head_revision).decode().strip()
    if merge_base != BASE_REVISION:
        raise RuntimeError(f"benchmark requires HEAD descended from base {BASE_REVISION}")
    contract_audit = _audit_contracts()
    if args.contracts_only:
        print(json.dumps(contract_audit, indent=2))
        return

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    workloads: dict[str, dict[str, Any]] = {}
    for case, count in CASES:
        case_dir = TEMP_ROOT / case
        case_dir.mkdir(parents=True, exist_ok=True)
        records = _build_case(case, count)
        input_bytes, input_sha = corpus_fingerprint(records)
        archive = JZPackCompressor(fast=False).compress(records)
        archive_sha = _sha256(archive)
        if archive_sha != EXPECTED_ARCHIVE_SHA256[case]:
            raise RuntimeError(f"{case} archive SHA-256 changed: {archive_sha}")
        archive_path = case_dir / "archive.jzpk"
        records_path = case_dir / "records.pickle"
        archive_path.write_bytes(archive)
        _set_variant("baseline")
        decoded_baseline = JZPackCompressor(fast=False).decompress(archive)
        difference = records_difference(records, decoded_baseline)
        if difference:
            raise AssertionError(f"baseline {case} archive values differ: {difference}")
        with records_path.open("wb") as stream:
            pickle.dump(decoded_baseline, stream, protocol=5)
        _set_variant("candidate")
        _validate(decoded_baseline, JZPackCompressor(fast=False).decompress(archive), f"candidate {case} preflight")
        signature_count = len({tuple(sorted(record.keys())) for record in records})
        workloads[case] = {
            "record_count": count,
            "distinct_top_level_key_sets": signature_count,
            "canonical_input_bytes": input_bytes,
            "input_sha256": input_sha,
            "archive_size_bytes": len(archive),
            "archive_sha256": archive_sha,
            "archive_sha256_matches_prechange_baseline": True,
            "baseline_round_trip_values_and_candidate_order_checked_before_timing": True,
        }
        del decoded_baseline, records, archive
        gc.collect()

    timing: dict[str, dict[str, list[dict[str, Any]]]] = {
        case: {variant: [] for variant in VARIANTS} for case, _ in CASES
    }
    case_names = [case for case, _ in CASES]
    for process_index in range(TIMING_PROCESSES):
        ordered_cases = case_names[process_index % len(case_names):] + case_names[:process_index % len(case_names)]
        if process_index % 2:
            ordered_cases.reverse()
        for case in ordered_cases:
            ordered_variants = list(VARIANTS)
            if (process_index + case_names.index(case)) % 2:
                ordered_variants.reverse()
            for variant in ordered_variants:
                timing[case][variant].append(
                    _run_worker("time", variant, case, TEMP_ROOT / case, SAMPLES_PER_PROCESS)
                )

    memory: dict[str, dict[str, list[dict[str, Any]]]] = {
        case: {variant: [] for variant in VARIANTS} for case, _ in CASES
    }
    for process_index in range(MEMORY_PROCESSES):
        ordered_cases = case_names[process_index % len(case_names):] + case_names[:process_index % len(case_names)]
        if process_index % 2:
            ordered_cases.reverse()
        ordered_variants = list(VARIANTS)
        if process_index % 2:
            ordered_variants.reverse()
        for case in ordered_cases:
            for variant in ordered_variants:
                memory[case][variant].append(_run_worker("memory", variant, case, TEMP_ROOT / case))

    for case, _ in CASES:
        for variant in VARIANTS:
            runs = timing[case][variant]
            samples = [sample for run in runs for sample in run["samples_ns"]]
            workloads[case].setdefault("variants", {})[variant] = {
                "timing_runs": runs,
                "timing_summary": {
                    "sample_count": len(samples),
                    "median_ns": int(statistics.median(samples)),
                    "minimum_ns": min(samples),
                    "maximum_ns": max(samples),
                    "process_medians_ns": [run["median_ns"] for run in runs],
                },
                "memory_runs": memory[case][variant],
                "memory_summary": {
                    "tracemalloc_peak_delta_bytes": int(
                        statistics.median(run["tracemalloc_peak_delta_bytes"] for run in memory[case][variant])
                    ),
                    "process_peak_rss_delta_bytes": (
                        int(
                            statistics.median(
                                run["process_peak_rss_delta_bytes"]
                                for run in memory[case][variant]
                                if run["process_peak_rss_delta_bytes"] is not None
                            )
                        )
                        if any(
                            run["process_peak_rss_delta_bytes"] is not None
                            for run in memory[case][variant]
                        )
                        else None
                    ),
                },
            }
        base_ns = workloads[case]["variants"]["baseline"]["timing_summary"]["median_ns"]
        candidate = workloads[case]["variants"]["candidate"]
        candidate["median_change_vs_baseline_percent"] = (
            candidate["timing_summary"]["median_ns"] / base_ns - 1
        ) * 100

    base_schema = _git("show", f"{BASE_REVISION}:jzpack/schema.py")
    source_paths = ["jzpack/schema.py", "tests/test_schema_reconstruction_fastpath.py"]
    patch = _git("diff", "--binary", BASE_REVISION, "--", *source_paths)
    source_hashes = {path: _sha256_file(REPOSITORY / path) for path in source_paths}
    report = {
        "benchmark": "jzpack-shallow-schema-reconstruction",
        "schema_version": 1,
        "provenance": {
            "base_revision": BASE_REVISION,
            "head_revision": head_revision,
            "branch": _git("branch", "--show-current").decode().strip(),
            "candidate_snapshot": (
                "working tree snapshot pinned by source and patch hashes"
                if _git("status", "--porcelain")
                else "committed tree pinned by revision and source hashes"
            ),
            "python_executable_basename": Path(sys.executable).name,
            "python_version": sys.version,
            "platform": platform.platform(),
            "jzpack_version": __import__("jzpack").__version__,
            "msgpack": importlib.metadata.version("msgpack"),
            "zstandard": importlib.metadata.version("zstandard"),
            "zstandard_native": list(__import__("zstandard").ZSTD_VERSION),
            "benchmark_script": "benchmarks/compare_schema_flat_fastpath.py",
            "benchmark_script_sha256": _sha256_file(Path(__file__).resolve()),
            "corpus_generator_sha256": _sha256_file(REPOSITORY / "benchmarks/corpus_wave3.py"),
            "baseline_schema_sha256": _sha256(base_schema),
            "candidate_source_sha256": source_hashes,
            "implementation_and_test_patch_sha256": _sha256(patch),
            "tracked_candidate_files": source_paths,
            "repository_worktree_status_at_capture": _git("status", "--short").decode().splitlines(),
        },
        "configuration": {
            "seed": SEED,
            "corpus": "benchmarks.corpus_wave3.build_records; homogeneous control fills absent optional values from schema-diverse corpus",
            "timer": "time.perf_counter_ns around JZPackCompressor.decompress only",
            "validation": "records_difference exact types/values/float64 bits/full row order plus recursive dict insertion order, all after timer",
            "archive_generation": "once per fixed corpus outside timers; each archive SHA-256 must match the pre-change baseline",
            "timing_processes_per_case_and_variant": TIMING_PROCESSES,
            "samples_per_process": SAMPLES_PER_PROCESS,
            "warmups_per_process": WARMUPS,
            "memory_processes_per_case_and_variant": MEMORY_PROCESSES,
            "memory": "fresh process; archive preloaded; one decode; tracemalloc peak and, when resource is available, process RSS high-water delta measured separately",
            "variants": {
                "baseline": f"reconstruction method from {BASE_REVISION}",
                "candidate": "checked-in shallow-path specialization; nested paths use current existing builder",
            },
            "memory_limitations": "tracemalloc excludes native allocations; ru_maxrss is a process high-water delta and can understate marginal allocation",
        },
        "contract_audit": contract_audit,
        "workloads": workloads,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    summary = {
        case: {
            variant: {
                "median_ms": round(data["timing_summary"]["median_ns"] / 1e6, 3),
                "change_pct": round(data.get("median_change_vs_baseline_percent", 0), 2),
                "tracemalloc_peak_mb": round(data["memory_summary"]["tracemalloc_peak_delta_bytes"] / 1e6, 3),
                "rss_delta_mb": (
                    round(data["memory_summary"]["process_peak_rss_delta_bytes"] / 1e6, 3)
                    if data["memory_summary"]["process_peak_rss_delta_bytes"] is not None
                    else None
                ),
            }
            for variant, data in workloads[case]["variants"].items()
        }
        for case, _ in CASES
    }
    print(json.dumps({"report_path": str(args.output), "report_sha256": _sha256_file(args.output), "summary": summary}, indent=2))


if __name__ == "__main__":
    _main()
