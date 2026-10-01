"""Interleaved decode comparison for the validated single-run RLE fast path.

The baseline decoder is loaded from the pinned repository revision. Corpus
creation, compression, fingerprints, and exact output checks stay outside the
timed intervals.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import zstandard as zstd

from benchmarks.corpus_wave3 import (
    DEFAULT_SEED,
    build_records,
    corpus_fingerprint,
    records_difference,
)
from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress
from jzpack.encoders import RLEEncoder, _is_supported_integer
from jzpack.errors import ResourceLimitError
from jzpack.serializer import PayloadSerializer

BASE_REVISION = "3f2e37e1db3c3b9653fc52bc02330128d8157440"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("benchmarks/results/rle-single-run.json")
DIRECT_COUNTS = (1, 128, 4_096, 50_000, 250_000)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_bytes(revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _baseline_decode() -> tuple[Callable[..., list[Any]], bytes]:
    source = _source_bytes(BASE_REVISION, "jzpack/encoders.py")
    module = ast.parse(source, filename=f"{BASE_REVISION}:jzpack/encoders.py")
    rle_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "RLEEncoder"
    )
    decode_node = next(
        node
        for node in rle_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "decode"
    )
    decode_node.decorator_list = []
    namespace: dict[str, Any] = {
        "sys": sys,
        "ResourceLimitError": ResourceLimitError,
        "_is_supported_integer": _is_supported_integer,
    }
    baseline_module = ast.Module(body=[decode_node], type_ignores=[])
    ast.fix_missing_locations(baseline_module)
    exec(
        compile(baseline_module, f"{BASE_REVISION}:RLEEncoder.decode", "exec"),
        namespace,
    )
    return namespace["decode"], source


@contextmanager
def _using_decoder(decoder: Callable[..., list[Any]]) -> Iterator[None]:
    original = RLEEncoder.decode
    RLEEncoder.decode = staticmethod(decoder)
    try:
        yield
    finally:
        RLEEncoder.decode = staticmethod(original)


def _verify_direct_result(actual: Any, value: str, count: int) -> None:
    if type(actual) is not list or len(actual) != count:
        raise AssertionError("single-run decode returned the wrong list length")
    if any(item is not value for item in actual):
        raise AssertionError("single-run decode did not preserve repeated value identity")


def _payload_from_archive(archive: bytes) -> dict[str, Any]:
    if len(archive) < 72 or archive[:5] != b"JZPK\x03":
        raise AssertionError("compress() did not return a version 3 chunked archive")
    chunk_header = archive[16:72]
    if chunk_header[:4] != b"CHNK":
        raise AssertionError("archive does not start with a data chunk")
    payload_size = int.from_bytes(chunk_header[40:48], "big")
    inner_start = 72
    inner_end = inner_start + payload_size
    if inner_end > len(archive):
        raise AssertionError("archive's first chunk payload is truncated")
    return PayloadSerializer().deserialize(archive[inner_start:inner_end])


def _encoding_stats(archive: bytes) -> dict[str, int]:
    payload = _payload_from_archive(archive)
    rle_columns = [
        column
        for schema in payload["s"].values()
        for column in schema["c"]
        if column["t"] == 1
    ]
    return {
        "schema_count": len(payload["s"]),
        "schema_order_run_count": len(payload["o"]),
        "rle_column_count": len(rle_columns),
        "single_run_rle_column_count": sum(len(column["d"]) == 1 for column in rle_columns),
    }


def _public_cases(records_count: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    constant = [
        {"constant": "stable-value", "sequence": index}
        for index in range(records_count)
    ]
    return {
        "constant-column": constant,
        "nested-records": build_records("nested-records", records_count, seed),
        "schema-diverse": build_records("schema-diverse", records_count, seed),
    }


def _timing(samples_ns: list[int]) -> dict[str, Any]:
    return {
        "samples_ns": samples_ns,
        "median_ns": statistics.median(samples_ns),
        "minimum_ns": min(samples_ns),
        "maximum_ns": max(samples_ns),
    }


def _time_direct_once(
    decoder: Callable[..., list[Any]], encoded: list[list[Any]], value: str, count: int
) -> int:
    with _using_decoder(decoder):
        started = time.perf_counter_ns()
        actual = RLEEncoder.decode(encoded, max_output_size=count)
        elapsed = time.perf_counter_ns() - started
    _verify_direct_result(actual, value, count)
    return elapsed


def _time_public_once(
    decoder: Callable[..., list[Any]], archive: bytes, records: list[dict[str, Any]]
) -> int:
    with _using_decoder(decoder):
        started = time.perf_counter_ns()
        actual = decompress(archive, max_records=len(records))
        elapsed = time.perf_counter_ns() - started
    difference = records_difference(records, actual)
    if difference is not None:
        raise AssertionError(f"exact public round trip failed: {difference}")
    return elapsed


def _run_direct_cases(
    baseline: Callable[..., list[Any]],
    candidate: Callable[..., list[Any]],
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for count in DIRECT_COUNTS:
        value = "single-run-constant"
        encoded = [[value, count]]
        for _ in range(warmups):
            _time_direct_once(baseline, encoded, value, count)
            _time_direct_once(candidate, encoded, value, count)

        raw_pairs = []
        by_implementation: dict[str, list[int]] = {"baseline": [], "candidate": []}
        for pair_index in range(samples):
            order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
            pair: dict[str, Any] = {"pair": pair_index, "order": list(order), "elapsed_ns": {}}
            for name in order:
                decoder = baseline if name == "baseline" else candidate
                elapsed = _time_direct_once(decoder, encoded, value, count)
                by_implementation[name].append(elapsed)
                pair["elapsed_ns"][name] = elapsed
            raw_pairs.append(pair)

        baseline_timing = _timing(by_implementation["baseline"])
        candidate_timing = _timing(by_implementation["candidate"])
        workloads[str(count)] = {
            "record_count": count,
            "value_type": type(value).__name__,
            "validated_output_limit": count,
            "exact_result_validated_outside_timer": True,
            "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
            "candidate_change_percent": (
                candidate_timing["median_ns"] / baseline_timing["median_ns"] - 1
            )
            * 100,
            "paired_samples": raw_pairs,
        }
    return workloads


def _run_public_cases(
    baseline: Callable[..., list[Any]],
    candidate: Callable[..., list[Any]],
    records_count: int,
    seed: int,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    workloads: dict[str, Any] = {}
    for name, records in _public_cases(records_count, seed).items():
        input_size, input_hash = corpus_fingerprint(records)
        archive = compress(records)
        # Confirm both revisions decode the exact same prebuilt archive before
        # collecting paired timing samples.
        baseline_ns = _time_public_once(baseline, archive, records)
        candidate_ns = _time_public_once(candidate, archive, records)
        del baseline_ns, candidate_ns

        for _ in range(warmups):
            _time_public_once(baseline, archive, records)
            _time_public_once(candidate, archive, records)

        raw_pairs = []
        by_implementation: dict[str, list[int]] = {"baseline": [], "candidate": []}
        for pair_index in range(samples):
            order = ("baseline", "candidate") if pair_index % 2 == 0 else ("candidate", "baseline")
            pair: dict[str, Any] = {"pair": pair_index, "order": list(order), "elapsed_ns": {}}
            for implementation in order:
                decoder = baseline if implementation == "baseline" else candidate
                elapsed = _time_public_once(decoder, archive, records)
                by_implementation[implementation].append(elapsed)
                pair["elapsed_ns"][implementation] = elapsed
            raw_pairs.append(pair)

        baseline_timing = _timing(by_implementation["baseline"])
        candidate_timing = _timing(by_implementation["candidate"])
        workloads[name] = {
            "record_count": len(records),
            "reference_input_size_bytes": input_size,
            "reference_input_sha256": input_hash,
            "archive_size_bytes": len(archive),
            "archive_sha256": _sha256(archive),
            "archive_encodings": _encoding_stats(archive),
            "max_records_limit": len(records),
            "exact_round_trip_validated_outside_timer": True,
            "timings": {"baseline": baseline_timing, "candidate": candidate_timing},
            "candidate_change_percent": (
                candidate_timing["median_ns"] / baseline_timing["median_ns"] - 1
            )
            * 100,
            "paired_samples": raw_pairs,
        }
    return workloads


def _runtime_metadata() -> dict[str, Any]:
    def version(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "jzpack_version": jzpack_version,
        "msgpack_version": version("msgpack"),
        "zstandard_version": version("zstandard"),
        "zstandard_native_version": list(zstd.ZSTD_VERSION),
    }


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def run_comparison(samples: int = 15, warmups: int = 2, records_count: int = 10_000, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("sample count must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    if isinstance(records_count, bool) or not isinstance(records_count, int) or records_count < 1:
        raise ValueError("record count must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    baseline, baseline_source = _baseline_decode()
    candidate = RLEEncoder.decode
    candidate_source = (REPOSITORY_ROOT / "jzpack/encoders.py").read_bytes()
    script_source = Path(__file__).read_bytes()
    try:
        direct_workloads = _run_direct_cases(baseline, candidate, samples, warmups)
        public_workloads = _run_public_cases(
            baseline,
            candidate,
            records_count,
            seed,
            samples,
            warmups,
        )
    finally:
        RLEEncoder.decode = staticmethod(candidate)

    return {
        "benchmark": "jzpack-single-run-rle-decode",
        "schema_version": 1,
        "configuration": {
            "base_revision": BASE_REVISION,
            "records_per_public_case": records_count,
            "direct_run_counts": list(DIRECT_COUNTS),
            "samples_per_implementation": samples,
            "warmups_per_implementation": warmups,
            "seed": seed,
            "timer": "time.perf_counter_ns",
            "timing_unit": "nanoseconds",
            "pair_order": "baseline-first and candidate-first alternate by sample pair",
            "measurement_scope": [
                "RLEEncoder.decode on validated direct single-run payloads",
                "public decompress on prebuilt archives",
            ],
            "excluded_setup": [
                "record generation",
                "input fingerprinting",
                "archive compression",
                "baseline AST extraction",
                "exact result validation",
            ],
        },
        "metadata": {
            "runtime": _runtime_metadata(),
            "git_head": _git_head(),
            "baseline_encoder_source_sha256": _sha256(baseline_source),
            "candidate_encoder_source_sha256": _sha256(candidate_source),
            "benchmark_script_sha256": _sha256(script_source),
        },
        "direct_single_run_workloads": direct_workloads,
        "public_decompress_workloads": public_workloads,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    try:
        report = run_comparison(
            samples=arguments.samples,
            warmups=arguments.warmups,
            records_count=arguments.records,
            seed=arguments.seed,
        )
    except Exception as exc:
        print(f"RLE comparison failed: {type(exc).__name__}", file=sys.stderr)
        return 3

    output_path = arguments.output
    if not output_path.is_absolute():
        output_path = REPOSITORY_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for scope, workloads in (
        ("direct", report["direct_single_run_workloads"]),
        ("public", report["public_decompress_workloads"]),
    ):
        for name, workload in workloads.items():
            timings = workload["timings"]
            base_ms = timings["baseline"]["median_ns"] / 1_000_000
            candidate_ms = timings["candidate"]["median_ns"] / 1_000_000
            change = workload["candidate_change_percent"]
            print(f"{scope} {name}: base {base_ms:.4f} ms; candidate {candidate_ms:.4f} ms; change {change:+.1f}%")
    print(f"raw report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
