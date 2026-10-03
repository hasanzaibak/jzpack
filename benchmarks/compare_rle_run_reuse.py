"""Compare cached RLE-run encoding with the pinned baseline on fixed inputs.

Corpus creation, fingerprints, archive hashing, and exact round-trip checks are
outside the timed intervals. Timings use plain ``perf_counter_ns`` calls and
alternate baseline/candidate order in each measured pair.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import ModuleType
from typing import Any

import msgpack
import zstandard as zstd

import jzpack as candidate
from benchmarks.corpus import DEFAULT_SEED, PROFILE_NAMES, build_records, corpus_fingerprint
from benchmarks.corpus_wave3 import records_difference
from jzpack.analyzer import _MAX_CACHED_RLE_RUNS
from jzpack.analyzer import ColumnEncoder as CandidateColumnEncoder
from jzpack.encoders import EncodingType
from tests.fidelity_oracle import is_faithful

BASE_REVISION = "995db735be80649945d10024238f2578e04c14e1"
REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("benchmarks/results/rle-run-reuse.json")
REPEATED_VALUE = "stable-value"
REPEATED_COLUMN_ROWS = 3_000
REPEATED_COLUMN_CALLS_PER_SAMPLE = 25
CORPUS_RECORDS_PER_PROFILE = 3_000
COMPRESSION_LEVEL = 3
ALLOCATION_ROWS = 100_000
ALLOCATION_SAMPLES = 3


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_show(revision: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    ).stdout


def _tracked_jzpack_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", BASE_REVISION, "--", "jzpack"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.splitlines()


def _source_hashes(contents: dict[str, bytes]) -> dict[str, str]:
    return {path: _sha256(contents[path]) for path in sorted(contents)}


def _load_baseline_package(temp_root: Path) -> tuple[ModuleType, dict[str, bytes]]:
    package_root = temp_root / "jzpack_baseline"
    contents: dict[str, bytes] = {}
    for path in _tracked_jzpack_paths():
        source = _git_show(BASE_REVISION, path)
        contents[path] = source
        destination = package_root / Path(path).relative_to("jzpack")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source)

    spec = importlib.util.spec_from_file_location(
        "jzpack_baseline",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the pinned baseline package")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, contents


def _candidate_sources(paths: list[str]) -> dict[str, bytes]:
    return {path: (REPOSITORY / path).read_bytes() for path in paths}


def _runtime_metadata() -> dict[str, Any]:
    def version(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    return {
        "python": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "msgpack": version("msgpack"),
        "zstandard": version("zstandard"),
        "zstandard_native": list(zstd.ZSTD_VERSION),
        "jzpack": candidate.__version__,
    }


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def _timing(samples_ns: list[int]) -> dict[str, Any]:
    return {
        "samples_ns": samples_ns,
        "median_ns": statistics.median(samples_ns),
        "minimum_ns": min(samples_ns),
        "maximum_ns": max(samples_ns),
    }


def _change_percent(baseline_ns: float, candidate_ns: float) -> float:
    return (candidate_ns / baseline_ns - 1) * 100


def _traced_peak_python_bytes(encoder: Any, values: list) -> tuple[int, dict[str, Any]]:
    tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        encoded = encoder.encode(values)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return max(0, peak - before), encoded


def _values_with_runs(row_count: int, run_count: int) -> list[str]:
    base_size, remainder = divmod(row_count, run_count)
    values: list[str] = []
    for run_index in range(run_count):
        value = f"run-{run_index}"
        values.extend([value] * (base_size + (run_index < remainder)))
    return values


def _integer_values_with_runs(row_count: int, run_count: int) -> list[int]:
    base_size, remainder = divmod(row_count, run_count)
    values: list[int] = []
    for run_index in range(run_count):
        values.extend([run_index**2] * (base_size + (run_index < remainder)))
    return values


def _measure_rle_peak_allocations(baseline_column_encoder: type) -> dict[str, Any]:
    encoders = {
        "baseline": baseline_column_encoder,
        "candidate": CandidateColumnEncoder,
    }
    cases = {
        "constant": (ALLOCATION_ROWS, 1, "string", EncodingType.RLE),
        "one-percent-runs": (
            ALLOCATION_ROWS,
            ALLOCATION_ROWS // 100,
            "string",
            EncodingType.RLE,
        ),
        "threshold-ten-percent-runs": (
            ALLOCATION_ROWS,
            int(ALLOCATION_ROWS * 0.1),
            "string",
            EncodingType.RLE,
        ),
        "first-over-threshold-integer-runs": (
            ALLOCATION_ROWS + 1,
            int((ALLOCATION_ROWS + 1) * 0.1) + 1,
            "integer",
            EncodingType.RAW,
        ),
    }
    workloads: dict[str, Any] = {}
    for case_name, (row_count, run_count, value_kind, expected_encoding) in cases.items():
        values = (
            _integer_values_with_runs(row_count, run_count)
            if value_kind == "integer"
            else _values_with_runs(row_count, run_count)
        )
        input_bytes = msgpack.packb(values, use_bin_type=True)
        peaks: dict[str, list[int]] = {"baseline": [], "candidate": []}
        raw_samples: list[dict[str, Any]] = []
        common_wire_bytes: bytes | None = None

        for sample_index in range(ALLOCATION_SAMPLES):
            order = (
                ("baseline", "candidate")
                if sample_index % 2 == 0
                else ("candidate", "baseline")
            )
            sample: dict[str, Any] = {"sample": sample_index, "order": list(order), "peak_traced_python_bytes": {}}
            outputs: dict[str, dict[str, Any]] = {}
            for name in order:
                encoder = encoders[name]()
                peak, encoded = _traced_peak_python_bytes(encoder, values)
                peaks[name].append(peak)
                sample["peak_traced_python_bytes"][name] = peak
                outputs[name] = encoded

            wire_bytes = {
                name: msgpack.packb(encoded, use_bin_type=True)
                for name, encoded in outputs.items()
            }
            if wire_bytes["baseline"] != wire_bytes["candidate"]:
                raise AssertionError(f"baseline and candidate wire bytes differ for {case_name}")
            if common_wire_bytes is None:
                common_wire_bytes = wire_bytes["baseline"]
            if wire_bytes["baseline"] != common_wire_bytes:
                raise AssertionError(f"column wire bytes changed between allocation samples for {case_name}")
            for name, encoded in outputs.items():
                if encoded["t"] != expected_encoding:
                    raise AssertionError(
                        f"{name} selected {encoded['t']} instead of {expected_encoding} for {case_name}"
                    )
                if not is_faithful(encoders[name]().decode(encoded), values):
                    raise AssertionError(f"{name} exact round trip failed for {case_name}")
            raw_samples.append(sample)

        baseline_peak = statistics.median(peaks["baseline"])
        candidate_peak = statistics.median(peaks["candidate"])
        workloads[case_name] = {
            "rows": len(values),
            "runs": run_count,
            "run_ratio": run_count / len(values),
            "value_kind": value_kind,
            "expected_encoding": int(expected_encoding),
            "input_size_bytes": len(input_bytes),
            "input_sha256": _sha256(input_bytes),
            "column_wire_size_bytes": len(common_wire_bytes or b""),
            "column_wire_sha256": _sha256(common_wire_bytes or b""),
            "column_wire_bytes_identical": True,
            "exact_round_trip_validated_outside_measurement": True,
            "peak_traced_python_bytes": {
                "baseline_samples": peaks["baseline"],
                "candidate_samples": peaks["candidate"],
                "baseline_median": baseline_peak,
                "candidate_median": candidate_peak,
                "candidate_change_percent": _change_percent(baseline_peak, candidate_peak),
            },
            "raw_samples": raw_samples,
        }
    return workloads


def _measure_repeated_column(
    baseline_column_encoder: type,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    values = [REPEATED_VALUE] * REPEATED_COLUMN_ROWS
    packed_input = msgpack.packb(values, use_bin_type=True)
    encoders = {
        "baseline": baseline_column_encoder(),
        "candidate": CandidateColumnEncoder(),
    }

    def timed_batch(name: str) -> tuple[int, dict[str, Any]]:
        encoder = encoders[name]
        started = time.perf_counter_ns()
        encoded: dict[str, Any] | None = None
        for _ in range(REPEATED_COLUMN_CALLS_PER_SAMPLE):
            encoded = encoder.encode(values)
        elapsed = time.perf_counter_ns() - started
        assert encoded is not None
        return elapsed, encoded

    for _ in range(warmups):
        baseline_ns, baseline_output = timed_batch("baseline")
        candidate_ns, candidate_output = timed_batch("candidate")
        del baseline_ns, candidate_ns
        if msgpack.packb(baseline_output, use_bin_type=True) != msgpack.packb(candidate_output, use_bin_type=True):
            raise AssertionError("baseline and candidate RLE column bytes differ during warmup")

    times: dict[str, list[int]] = {"baseline": [], "candidate": []}
    raw_pairs: list[dict[str, Any]] = []
    expected_wire_bytes: bytes | None = None
    for pair_index in range(samples):
        order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
        pair: dict[str, Any] = {"pair": pair_index, "order": list(order), "elapsed_ns": {}}
        outputs: dict[str, dict[str, Any]] = {}
        for name in order:
            elapsed, encoded = timed_batch(name)
            times[name].append(elapsed)
            pair["elapsed_ns"][name] = elapsed
            outputs[name] = encoded

        wire = {
            name: msgpack.packb(encoded, use_bin_type=True)
            for name, encoded in outputs.items()
        }
        if wire["baseline"] != wire["candidate"]:
            raise AssertionError("baseline and candidate RLE column bytes differ")
        if expected_wire_bytes is None:
            expected_wire_bytes = wire["baseline"]
        if wire["baseline"] != expected_wire_bytes:
            raise AssertionError("repeated-column wire bytes changed between samples")
        for name, encoded in outputs.items():
            restored = encoders[name].decode(encoded)
            if not is_faithful(restored, values):
                raise AssertionError(f"{name} repeated-column round trip differs")
        raw_pairs.append(pair)

    baseline_timing = _timing(times["baseline"])
    candidate_timing = _timing(times["candidate"])
    paired_changes = [
        _change_percent(pair["elapsed_ns"]["baseline"], pair["elapsed_ns"]["candidate"])
        for pair in raw_pairs
    ]
    return {
        "rows": len(values),
        "value_type": type(REPEATED_VALUE).__name__,
        "value": REPEATED_VALUE,
        "encode_calls_per_sample": REPEATED_COLUMN_CALLS_PER_SAMPLE,
        "input_size_bytes": len(packed_input),
        "input_sha256": _sha256(packed_input),
        "column_wire_size_bytes": len(expected_wire_bytes or b""),
        "column_wire_sha256": _sha256(expected_wire_bytes or b""),
        "column_wire_bytes_identical": True,
        "exact_round_trip_validated_outside_timer": True,
        "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
        "candidate_change_percent": _change_percent(
            baseline_timing["median_ns"], candidate_timing["median_ns"]
        ),
        "paired_median_change_percent": statistics.median(paired_changes),
        "paired_samples": raw_pairs,
    }


def _measure_public_repeated_string_archive(
    baseline_package: ModuleType,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    records = [{"value": REPEATED_VALUE} for _ in range(REPEATED_COLUMN_ROWS)]
    input_size, input_hash = corpus_fingerprint(records)
    compressors = {
        "baseline": baseline_package.JZPackCompressor(compression_level=COMPRESSION_LEVEL),
        "candidate": candidate.JZPackCompressor(compression_level=COMPRESSION_LEVEL),
    }

    def timed_compress(name: str) -> tuple[int, bytes]:
        started = time.perf_counter_ns()
        archive = compressors[name].compress(records)
        return time.perf_counter_ns() - started, archive

    def validate_pair(outputs: dict[str, bytes]) -> tuple[int, str]:
        if outputs["baseline"] != outputs["candidate"]:
            raise AssertionError("baseline and candidate repeated-string archives differ")
        digest = _sha256(outputs["baseline"])
        for name, archive in outputs.items():
            restored = (
                baseline_package.decompress(archive)
                if name == "baseline"
                else candidate.decompress(archive)
            )
            difference = records_difference(records, restored)
            if difference is not None:
                raise AssertionError(f"{name} exact repeated-string round trip failed: {difference}")
        return len(outputs["baseline"]), digest

    for _ in range(warmups):
        _, baseline_archive = timed_compress("baseline")
        _, candidate_archive = timed_compress("candidate")
        validate_pair({"baseline": baseline_archive, "candidate": candidate_archive})

    times: dict[str, list[int]] = {"baseline": [], "candidate": []}
    raw_pairs: list[dict[str, Any]] = []
    archive_size: int | None = None
    archive_hash: str | None = None
    for pair_index in range(samples):
        order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
        pair: dict[str, Any] = {"pair": pair_index, "order": list(order), "elapsed_ns": {}}
        outputs: dict[str, bytes] = {}
        for name in order:
            elapsed, archive = timed_compress(name)
            times[name].append(elapsed)
            pair["elapsed_ns"][name] = elapsed
            outputs[name] = archive
        size, digest = validate_pair(outputs)
        if archive_size is None:
            archive_size, archive_hash = size, digest
        if size != archive_size or digest != archive_hash:
            raise AssertionError("repeated-string archive bytes changed between samples")
        raw_pairs.append(pair)

    baseline_timing = _timing(times["baseline"])
    candidate_timing = _timing(times["candidate"])
    paired_changes = [
        _change_percent(pair["elapsed_ns"]["baseline"], pair["elapsed_ns"]["candidate"])
        for pair in raw_pairs
    ]
    return {
        "record_count": len(records),
        "reference_input_size_bytes": input_size,
        "reference_input_sha256": input_hash,
        "archive_size_bytes": archive_size,
        "archive_sha256": archive_hash,
        "archive_bytes_identical": True,
        "exact_round_trip_validated_outside_timer": True,
        "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
        "candidate_change_percent": _change_percent(
            baseline_timing["median_ns"], candidate_timing["median_ns"]
        ),
        "paired_median_change_percent": statistics.median(paired_changes),
        "paired_samples": raw_pairs,
    }


def _measure_corpus_profile(
    baseline_package: ModuleType,
    profile: str,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    records = build_records(profile, CORPUS_RECORDS_PER_PROFILE, DEFAULT_SEED)
    input_size, input_hash = corpus_fingerprint(records)
    compressors = {
        "baseline": baseline_package.JZPackCompressor(compression_level=COMPRESSION_LEVEL),
        "candidate": candidate.JZPackCompressor(compression_level=COMPRESSION_LEVEL),
    }

    def timed_compress(name: str) -> tuple[int, bytes]:
        started = time.perf_counter_ns()
        archive = compressors[name].compress(records)
        return time.perf_counter_ns() - started, archive

    def validate_pair(outputs: dict[str, bytes]) -> tuple[int, str]:
        baseline_archive = outputs["baseline"]
        candidate_archive = outputs["candidate"]
        if baseline_archive != candidate_archive:
            raise AssertionError(f"baseline and candidate archive bytes differ for {profile}")
        archive_hash = _sha256(baseline_archive)
        for name, archive in outputs.items():
            restored = (
                baseline_package.decompress(archive)
                if name == "baseline"
                else candidate.decompress(archive)
            )
            difference = records_difference(records, restored)
            if difference is not None:
                raise AssertionError(f"{name} exact round trip failed for {profile}: {difference}")
        return len(baseline_archive), archive_hash

    for _ in range(warmups):
        _, baseline_archive = timed_compress("baseline")
        _, candidate_archive = timed_compress("candidate")
        validate_pair({"baseline": baseline_archive, "candidate": candidate_archive})

    times: dict[str, list[int]] = {"baseline": [], "candidate": []}
    raw_pairs: list[dict[str, Any]] = []
    archive_size: int | None = None
    archive_hash: str | None = None
    for pair_index in range(samples):
        order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
        pair: dict[str, Any] = {"pair": pair_index, "order": list(order), "elapsed_ns": {}}
        outputs: dict[str, bytes] = {}
        for name in order:
            elapsed, archive = timed_compress(name)
            times[name].append(elapsed)
            pair["elapsed_ns"][name] = elapsed
            outputs[name] = archive
        size, digest = validate_pair(outputs)
        if archive_size is None:
            archive_size, archive_hash = size, digest
        if size != archive_size or digest != archive_hash:
            raise AssertionError(f"archive bytes changed between samples for {profile}")
        raw_pairs.append(pair)

    baseline_timing = _timing(times["baseline"])
    candidate_timing = _timing(times["candidate"])
    paired_changes = [
        _change_percent(pair["elapsed_ns"]["baseline"], pair["elapsed_ns"]["candidate"])
        for pair in raw_pairs
    ]
    return {
        "record_count": len(records),
        "reference_input_size_bytes": input_size,
        "reference_input_sha256": input_hash,
        "archive_size_bytes": archive_size,
        "archive_sha256": archive_hash,
        "archive_bytes_identical": True,
        "exact_round_trip_validated_outside_timer": True,
        "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
        "candidate_change_percent": _change_percent(
            baseline_timing["median_ns"], candidate_timing["median_ns"]
        ),
        "paired_median_change_percent": statistics.median(paired_changes),
        "paired_samples": raw_pairs,
    }


def run_comparison(samples: int = 15, warmups: int = 2) -> dict[str, Any]:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("sample count must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")

    paths = _tracked_jzpack_paths()
    candidate_sources = _candidate_sources(paths)
    with tempfile.TemporaryDirectory(prefix="jzpack-rle-baseline-") as temporary:
        baseline_package, baseline_sources = _load_baseline_package(Path(temporary))
        baseline_column_encoder = importlib.import_module("jzpack_baseline.analyzer").ColumnEncoder
        repeated_column = _measure_repeated_column(baseline_column_encoder, samples, warmups)
        public_repeated_string = _measure_public_repeated_string_archive(
            baseline_package,
            samples,
            warmups,
        )
        corpus_workloads = {
            profile: _measure_corpus_profile(baseline_package, profile, samples, warmups)
            for profile in PROFILE_NAMES
        }
        rle_peak_allocation_workloads = _measure_rle_peak_allocations(baseline_column_encoder)

    diff = subprocess.run(
        ["git", "diff", "--binary", BASE_REVISION, "--", "jzpack"],
        cwd=REPOSITORY,
        capture_output=True,
        check=True,
    ).stdout
    harness_source = Path(__file__).read_bytes()
    return {
        "benchmark": "jzpack-rle-run-reuse",
        "schema_version": 1,
        "base_revision": BASE_REVISION,
        "candidate_head": _git_head(),
        "source_hashes": {
            "baseline_jzpack_files_sha256": _source_hashes(baseline_sources),
            "candidate_jzpack_files_sha256": _source_hashes(candidate_sources),
            "candidate_patch_sha256": _sha256(diff),
            "corpus_v1_generator_sha256": _sha256((REPOSITORY / "benchmarks/corpus.py").read_bytes()),
            "exact_oracle_sha256": _sha256(
                (REPOSITORY / "benchmarks/corpus_wave3.py").read_bytes()
            ),
            "benchmark_script_sha256": _sha256(harness_source),
        },
        "environment": _runtime_metadata(),
        "configuration": {
            "timer": "time.perf_counter_ns",
            "timing_unit": "nanoseconds",
            "samples_per_variant": samples,
            "warmups_per_variant": warmups,
            "order": "baseline-first and candidate-first alternate by measured pair",
            "direct_case": {
                "rows": REPEATED_COLUMN_ROWS,
                "encode_calls_per_sample": REPEATED_COLUMN_CALLS_PER_SAMPLE,
                "input": "one prebuilt list containing the same built-in string in every row",
            },
            "public_repeated_string_case": {
                "records": REPEATED_COLUMN_ROWS,
                "input": "prebuilt records with the same single string field",
                "compressor_context": "one reused context per implementation",
                "compression_level": COMPRESSION_LEVEL,
            },
            "corpus": {
                "version": 1,
                "seed": DEFAULT_SEED,
                "records_per_profile": CORPUS_RECORDS_PER_PROFILE,
                "profiles": list(PROFILE_NAMES),
                "compression_level": COMPRESSION_LEVEL,
                "compressor_context": "one reused context per implementation and profile",
            },
            "allocation_measurement": {
                "metric": "tracemalloc peak bytes above pre-call traced current bytes",
                "scope": "traced Python allocations only; not RSS or native allocations",
                "nominal_rows_per_case": ALLOCATION_ROWS,
                "samples_per_variant": ALLOCATION_SAMPLES,
                "maximum_cached_rle_runs": _MAX_CACHED_RLE_RUNS,
                "order": "baseline-first and candidate-first alternate by sample",
                "excluded_from_measurement": [
                    "input generation and input serialization",
                    "output serialization and byte comparison",
                    "exact round-trip validation",
                ],
                "separate_from_timed_benchmarks": True,
                "cases": {
                    "constant": "1 run in 100,000 rows",
                    "one-percent-runs": "1,000 runs in 100,000 rows",
                    "threshold-ten-percent-runs": "10,000 runs in 100,000 rows; inclusive RLE limit",
                    "first-over-threshold-integer-runs": (
                        "10,001 integer runs in 100,001 rows; above the inclusive RLE limit; RAW fallback"
                    ),
                },
            },
            "excluded_from_timers": [
                "input generation and fingerprinting",
                "archive hashing and byte comparison",
                "column wire serialization and byte comparison",
                "exact round-trip validation",
            ],
            "acceptance": "baseline and candidate column bytes and public archives are identical",
        },
        "repeated_string_column": repeated_column,
        "public_repeated_string_archive": public_repeated_string,
        "corpus_workloads": corpus_workloads,
        "rle_peak_allocation_workloads": rle_peak_allocation_workloads,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_comparison(samples=args.samples, warmups=args.warmups)
    output_path = args.output if args.output.is_absolute() else REPOSITORY / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    workloads = {
        "repeated-string-column": report["repeated_string_column"],
        "public-repeated-string-archive": report["public_repeated_string_archive"],
        **report["corpus_workloads"],
    }
    for name, workload in workloads.items():
        timings = workload["timings"]
        base_ms = timings["baseline"]["median_ns"] / 1_000_000
        candidate_ms = timings["candidate"]["median_ns"] / 1_000_000
        change = workload["candidate_change_percent"]
        print(f"{name}: baseline {base_ms:.4f} ms; candidate {candidate_ms:.4f} ms; change {change:+.1f}%")
    for name, workload in report["rle_peak_allocation_workloads"].items():
        allocation = workload["peak_traced_python_bytes"]
        base_kib = allocation["baseline_median"] / 1024
        candidate_kib = allocation["candidate_median"] / 1024
        change = allocation["candidate_change_percent"]
        print(
            f"{name} traced Python peak: baseline {base_kib:.1f} KiB; "
            f"candidate {candidate_kib:.1f} KiB; change {change:+.1f}%"
        )
    print(f"raw report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
