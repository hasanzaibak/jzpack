"""Measure the exact-dict schema-layout experiment through public compression.

Run from the repository root with the project benchmark dependencies installed:

    python benchmarks/compare_compress_schema_layout.py --output benchmarks/results/compress-schema-layout-capture-1-20261004.json

Each timed sample runs in a fresh process. Inputs are generated and fingerprinted
once, then loaded from the same pickle outside the timer by each baseline and
candidate worker. Archive comparison and exact round-trip validation are also
outside the timer.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import pickle
import platform
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from benchmarks.corpus_wave3 import DEFAULT_SEED, build_records, corpus_fingerprint, records_difference

REPOSITORY = Path(__file__).resolve().parents[1]
BASE_REVISION = "47c338ab278fd631d776de5928ae87291ef66248"
CASES = (
    ("nested-records", 40_000, 16_384),
    ("large-strings", 512, 16_384),
    ("schema-diverse", 50_000, 16_384),
)
DEFAULT_SAMPLES = 9
DEFAULT_WARMUPS = 1


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


def _source_hashes(package_root: Path) -> dict[str, str]:
    return {
        path.relative_to(package_root).as_posix(): _sha256_file(path)
        for path in sorted((package_root / "jzpack").rglob("*.py"))
    }


def _worker(variant: str, case_name: str, records_path: Path, archive_path: Path, warmups: int) -> dict[str, Any]:
    import jzpack
    from jzpack.compressor import JZPackCompressor

    with records_path.open("rb") as stream:
        records = pickle.load(stream)
    compressor = JZPackCompressor(fast=False)
    warm_archives = [compressor.compress(records) for _ in range(warmups)]

    started = time.perf_counter_ns()
    archive = compressor.compress(records)
    elapsed_ns = time.perf_counter_ns() - started

    if warm_archives and any(warm_archive != archive for warm_archive in warm_archives):
        raise AssertionError("warmup and timed archive bytes differ")
    restored = compressor.decompress(archive)
    difference = records_difference(records, restored)
    if difference is not None:
        raise AssertionError(f"{variant}/{case_name} exact round trip failed: {difference}")
    archive_path.write_bytes(archive)

    return {
        "variant": variant,
        "case": case_name,
        "elapsed_ns": elapsed_ns,
        "archive_size_bytes": len(archive),
        "archive_sha256": _sha256(archive),
        "warmups": warmups,
        "warmup_bytes_identical": True,
        "exact_round_trip_validated_outside_timer": True,
        "runtime_import": "candidate" if Path(jzpack.__file__).resolve().is_relative_to(REPOSITORY) else "baseline",
    }


def _worker_main(argv: list[str]) -> None:
    variant, case_name, records_path, archive_path, warmups = argv
    result = _worker(variant, case_name, Path(records_path), Path(archive_path), int(warmups))
    print(json.dumps(result, sort_keys=True))


def _run_worker(
    variant: str,
    case_name: str,
    records_path: Path,
    archive_path: Path,
    baseline_root: Path,
    warmups: int,
) -> dict[str, Any]:
    environment = os.environ.copy()
    import_path = [str(REPOSITORY)]
    if variant == "baseline":
        import_path.insert(0, str(baseline_root))
    environment["PYTHONPATH"] = os.pathsep.join(import_path)
    process = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "worker",
            variant,
            case_name,
            str(records_path),
            str(archive_path),
            str(warmups),
        ],
        cwd=archive_path.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(f"{variant}/{case_name} worker failed: {process.stderr.strip()}")
    result = json.loads(process.stdout)
    expected_import = variant
    if result["runtime_import"] != expected_import:
        raise AssertionError(f"{variant} worker imported {result['runtime_import']} package")
    return result


def _build_inputs(root: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for name, count, large_string_bytes in CASES:
        records = build_records(name, count, DEFAULT_SEED, large_string_bytes=large_string_bytes)
        canonical_bytes, canonical_sha256 = corpus_fingerprint(records)
        records_path = root / f"{name}.pickle"
        records_path.write_bytes(pickle.dumps(records, protocol=5))
        cases[name] = {
            "records_path": records_path,
            "record_count": len(records),
            "canonical_input_bytes": canonical_bytes,
            "canonical_input_sha256": canonical_sha256,
            "pickle_input_bytes": records_path.stat().st_size,
            "pickle_input_sha256": _sha256_file(records_path),
            "large_string_bytes": large_string_bytes if name == "large-strings" else None,
        }
        del records
    return cases


def _measure_case(
    name: str,
    case: dict[str, Any],
    baseline_root: Path,
    samples: int,
    warmups: int,
    output_root: Path,
) -> dict[str, Any]:
    times: dict[str, list[int]] = {"baseline": [], "candidate": []}
    pairs: list[dict[str, Any]] = []
    expected_digest: str | None = None
    expected_size: int | None = None

    for pair_index in range(samples):
        order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
        pair: dict[str, Any] = {"pair": pair_index + 1, "order": list(order), "elapsed_ns": {}}
        archive_bytes: dict[str, bytes] = {}
        for variant in order:
            archive_path = output_root / f"{name}-{pair_index:02d}-{variant}.jzpk"
            result = _run_worker(
                variant,
                name,
                case["records_path"],
                archive_path,
                baseline_root,
                warmups,
            )
            raw_archive = archive_path.read_bytes()
            if len(raw_archive) != result["archive_size_bytes"] or _sha256(raw_archive) != result["archive_sha256"]:
                raise AssertionError(f"{variant}/{name} worker archive report does not match its bytes")
            if expected_digest is None:
                expected_digest = result["archive_sha256"]
                expected_size = result["archive_size_bytes"]
            if result["archive_sha256"] != expected_digest or result["archive_size_bytes"] != expected_size:
                raise AssertionError(f"{variant}/{name} archive changed between fresh-process samples")
            if not result["exact_round_trip_validated_outside_timer"]:
                raise AssertionError(f"{variant}/{name} exact round-trip oracle was not run")
            times[variant].append(result["elapsed_ns"])
            pair["elapsed_ns"][variant] = result["elapsed_ns"]
            pair[f"{variant}_archive_sha256"] = result["archive_sha256"]
            pair[f"{variant}_round_trip_validated"] = result["exact_round_trip_validated_outside_timer"]
            archive_bytes[variant] = raw_archive

        if archive_bytes["baseline"] != archive_bytes["candidate"]:
            raise AssertionError(f"baseline and candidate {name} archive bytes differ")
        pair["archive_bytes_identical"] = True
        pairs.append(pair)

    baseline_median = statistics.median(times["baseline"])
    candidate_median = statistics.median(times["candidate"])
    paired_speedups = [
        100 * (pair["elapsed_ns"]["baseline"] - pair["elapsed_ns"]["candidate"])
        / pair["elapsed_ns"]["baseline"]
        for pair in pairs
    ]
    return {
        "record_count": case["record_count"],
        "canonical_input_bytes": case["canonical_input_bytes"],
        "canonical_input_sha256": case["canonical_input_sha256"],
        "pickle_input_bytes": case["pickle_input_bytes"],
        "pickle_input_sha256": case["pickle_input_sha256"],
        "large_string_bytes": case["large_string_bytes"],
        "archive_size_bytes": expected_size,
        "archive_sha256": expected_digest,
        "archive_bytes_identical_for_all_pairs": True,
        "timings_ns": {
            "baseline_median": int(baseline_median),
            "candidate_median": int(candidate_median),
            "baseline_samples": times["baseline"],
            "candidate_samples": times["candidate"],
        },
        "candidate_median_speedup_percent": 100 * (baseline_median - candidate_median) / baseline_median,
        "paired_median_speedup_percent": statistics.median(paired_speedups),
        "paired_samples": pairs,
        "exact_round_trip_validated_outside_timer_for_every_sample": True,
    }


def run_capture(samples: int, warmups: int) -> dict[str, Any]:
    if samples < 1 or warmups < 0:
        raise ValueError("samples must be positive and warmups must be non-negative")
    head = _git("rev-parse", "HEAD").decode().strip()
    merge_base = _git("merge-base", BASE_REVISION, head).decode().strip()
    if merge_base != BASE_REVISION:
        raise RuntimeError(f"benchmark HEAD must descend from base revision {BASE_REVISION}")

    current_diff = _git("diff", "--binary", BASE_REVISION, "--", "jzpack")
    with tempfile.TemporaryDirectory(prefix="jzpack-compress-schema-layout-") as temporary:
        root = Path(temporary)
        baseline_root = root / "baseline"
        baseline_root.mkdir()
        archive = _git("archive", "--format=tar", BASE_REVISION, "jzpack")
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as source:
            source.extractall(baseline_root, filter="data")

        base_hashes = _source_hashes(baseline_root)
        candidate_hashes = _source_hashes(REPOSITORY)
        changed_runtime_sources = sorted(
            path for path in base_hashes if base_hashes[path] != candidate_hashes.get(path)
        )
        if changed_runtime_sources != ["jzpack/schema.py"]:
            raise RuntimeError(f"unexpected runtime source changes: {changed_runtime_sources}")

        input_root = root / "inputs"
        output_root = root / "outputs"
        input_root.mkdir()
        output_root.mkdir()
        cases = _build_inputs(input_root)
        workloads = {
            name: _measure_case(name, case, baseline_root, samples, warmups, output_root)
            for name, case in cases.items()
        }

    return {
        "benchmark": "jzpack-public-compress-schema-layout",
        "schema_version": 1,
        "base_revision": BASE_REVISION,
        "candidate_head": head,
        "candidate_runtime_patch_sha256": _sha256(current_diff),
        "runtime_source_hashes": {"baseline": base_hashes, "candidate": candidate_hashes},
        "changed_runtime_sources": changed_runtime_sources,
        "source_hashes": {
            "benchmark_script_sha256": _sha256_file(Path(__file__).resolve()),
            "corpus_generator_sha256": _sha256_file(REPOSITORY / "benchmarks/corpus_wave3.py"),
            "test_schema_contracts_sha256": _sha256_file(REPOSITORY / "tests/test_schema_contracts.py"),
        },
        "environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "msgpack": importlib.metadata.version("msgpack"),
            "zstandard": importlib.metadata.version("zstandard"),
        },
        "configuration": {
            "timer": "time.perf_counter_ns around JZPackCompressor(fast=False).compress(records)",
            "process_isolation": "one fresh process per variant, case, and paired sample",
            "warmups_per_process": warmups,
            "samples_per_variant_and_case": samples,
            "pair_order": "baseline-first and candidate-first alternate by pair",
            "input_setup": "corpus generated and fingerprinted once; all workers load the same prebuilt pickle outside the timer",
            "validation": "exact archive bytes compared after each pair; exact round trip checked outside each timer with corpus records_difference",
            "seed": DEFAULT_SEED,
            "cases": [
                {"profile": name, "records": count, "large_string_bytes": size}
                for name, count, size in CASES
            ],
            "acceptance_gate": {
                "nested-records": "at least 8% median paired speedup in each of two independent captures",
                "large-strings": "at least 8% median paired speedup in each of two independent captures",
                "schema-diverse": "no more than 2% median regression in either capture",
            },
        },
        "workloads": workloads,
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        _worker_main(sys.argv[2:])
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY / "benchmarks/results/compress-schema-layout-capture.json",
    )
    args = parser.parse_args()
    report = run_capture(args.samples, args.warmups)
    output = args.output if args.output.is_absolute() else REPOSITORY / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for name, workload in report["workloads"].items():
        timings = workload["timings_ns"]
        print(
            f"{name}: baseline {timings['baseline_median'] / 1_000_000:.3f} ms; "
            f"candidate {timings['candidate_median'] / 1_000_000:.3f} ms; "
            f"paired median speedup {workload['paired_median_speedup_percent']:+.2f}%"
        )
    print(f"raw report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
