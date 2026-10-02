"""Alternate paired prebuilt/lazy writer timings for the ASCII-size change.

The baseline package is reconstructed from one repository commit. Each timed
call writes to a plain BytesIO; corpus construction, imports, decoding, and
hashing stay outside the timer for prebuilt-list calls. Lazy calls intentionally
include generator work and are reported separately.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import jzpack as candidate

REPOSITORY = Path(__file__).resolve().parents[1]
CORPUS_SOURCE = REPOSITORY / "benchmarks" / "corpus_wave3.py"
CORPUS_SOURCE_SHA256 = "596960e45e66cf63e13b47ea08f010dd97e65d0669d1301e13e47f78f912d221"
CORPUS_HARNESS = REPOSITORY / "benchmarks" / "benchmark_corpus_wave3.py"
CORPUS_HARNESS_SHA256 = "7f3aca615ef0df8a6ca92901708ce115263eb6b295c1dd908244e5fa08438b44"
BASE_REVISION = "3f2e37e1db3c3b9653fc52bc02330128d8157440"
RUNTIME_SOURCES = (
    "jzpack/writer.py",
    "jzpack/compressor.py",
    "jzpack/schema.py",
    "jzpack/analyzer.py",
    "jzpack/encoders.py",
    "jzpack/serializer.py",
    "jzpack/chunks.py",
)
CASES = {"schema-diverse": 50_000, "nested-records": 40_000}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _git_show(revision: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    ).stdout


def _load_corpus() -> ModuleType:
    if _file_sha256(CORPUS_SOURCE) != CORPUS_SOURCE_SHA256:
        raise RuntimeError("wave-3 corpus generator differs from its reviewed source")
    if _file_sha256(CORPUS_HARNESS) != CORPUS_HARNESS_SHA256:
        raise RuntimeError("wave-3 harness differs from its reviewed source")
    spec = importlib.util.spec_from_file_location("writer_ascii_corpus", CORPUS_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load wave-3 corpus generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_baseline(temp_root: Path) -> tuple[ModuleType, dict[str, str]]:
    package_root = temp_root / "jzpack_before"
    shutil.copytree(REPOSITORY / "jzpack", package_root)
    baseline_hashes: dict[str, str] = {}
    for source in RUNTIME_SOURCES:
        baseline = _git_show(BASE_REVISION, source)
        baseline_hashes[source] = _sha256(baseline)
        if source == "jzpack/writer.py":
            (package_root / "writer.py").write_bytes(baseline)
        else:
            if baseline != (REPOSITORY / source).read_bytes():
                raise RuntimeError(f"runtime source changed outside the writer: {source}")

    spec = importlib.util.spec_from_file_location(
        "jzpack_before",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load baseline package")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, baseline_hashes


def _one_encode(module: ModuleType, corpus: ModuleType, profile: str, mode: str, records: list[dict[str, Any]]) -> tuple[float, bytes]:
    sink = io.BytesIO()
    if mode == "prebuilt-list":
        input_records: Any = records
    elif mode == "lazy-iter-records":
        input_records = corpus.iter_records(profile, len(records), corpus.DEFAULT_SEED)
    else:
        raise ValueError(f"unknown input mode: {mode}")

    started = time.perf_counter()
    written = module.write_records(input_records, sink, compression_level=3)
    elapsed = time.perf_counter() - started
    payload = sink.getvalue()
    if written != len(payload):
        raise RuntimeError("writer byte count does not match output bytes")
    return elapsed, payload


def _exact_round_trip(module: ModuleType, corpus: ModuleType, records: list[dict[str, Any]], payload: bytes) -> None:
    decoded = list(module.iter_decompress(io.BytesIO(payload)))
    difference = corpus.records_difference(records, decoded)
    if difference is not None:
        raise RuntimeError(f"writer failed exact round trip: {difference}")


def _paired_case(
    before: ModuleType,
    after: ModuleType,
    corpus: ModuleType,
    profile: str,
    count: int,
    mode: str,
    samples: int,
) -> dict[str, Any]:
    records = corpus.build_records(profile, count, corpus.DEFAULT_SEED)
    input_bytes, input_hash = corpus.corpus_fingerprint(records)
    variants = {"before": before, "after": after}
    elapsed = {name: [] for name in variants}
    output_hashes = {name: [] for name in variants}
    output_sizes = {name: [] for name in variants}
    common_archive_hash: str | None = None

    for sample in range(samples):
        order = ("before", "after") if sample % 2 == 0 else ("after", "before")
        pair: dict[str, bytes] = {}
        for name in order:
            seconds, payload = _one_encode(variants[name], corpus, profile, mode, records)
            _exact_round_trip(variants[name], corpus, records, payload)
            digest = _sha256(payload)
            if common_archive_hash is None:
                common_archive_hash = digest
            if digest != common_archive_hash:
                raise RuntimeError("before and after writer archives are not byte-identical")
            elapsed[name].append(seconds)
            output_hashes[name].append(digest)
            output_sizes[name].append(len(payload))
            pair[name] = payload
        if pair["before"] != pair["after"]:
            raise RuntimeError("before and after writer outputs differ despite matching hashes")

    before_median = statistics.median(elapsed["before"])
    after_median = statistics.median(elapsed["after"])
    return {
        "profile": profile,
        "record_count": count,
        "input_mode": mode,
        "input_generation_in_timed_region": mode == "lazy-iter-records",
        "canonical_input_bytes": input_bytes,
        "input_sha256": input_hash,
        "paired_order": ["before/after", "after/before"],
        "before_samples_seconds": elapsed["before"],
        "after_samples_seconds": elapsed["after"],
        "before_median_seconds": before_median,
        "after_median_seconds": after_median,
        "after_over_before_median_ratio": after_median / before_median,
        "output_bytes_samples": output_sizes["before"],
        "archive_sha256_samples": output_hashes["before"],
        "before_after_output_bytes_identical": True,
        "exact_round_trip_validated": True,
    }


def run(samples: int) -> dict[str, Any]:
    corpus = _load_corpus()
    with tempfile.TemporaryDirectory(prefix="jzpack-ascii-baseline-") as temporary:
        before, before_hashes = _load_baseline(Path(temporary))
        after_hashes = {source: _file_sha256(REPOSITORY / source) for source in RUNTIME_SOURCES}
        if before_hashes["jzpack/writer.py"] == after_hashes["jzpack/writer.py"]:
            raise RuntimeError("candidate writer does not differ from the baseline")

        cases = []
        for profile, count in CASES.items():
            for mode in ("prebuilt-list", "lazy-iter-records"):
                cases.append(_paired_case(before, candidate, corpus, profile, count, mode, samples))

    try:
        zstandard = importlib.metadata.version("zstandard")
    except importlib.metadata.PackageNotFoundError:
        zstandard = "unavailable"
    try:
        msgpack = importlib.metadata.version("msgpack")
    except importlib.metadata.PackageNotFoundError:
        msgpack = "unavailable"
    script_path = Path(__file__).resolve()
    return {
        "format": "jzpack-writer-ascii-paired-v1",
        "purpose": "alternating paired encode timing; platform/workload evidence only",
        "base_revision": BASE_REVISION,
        "candidate_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "baseline_runtime_source_sha256": before_hashes,
        "candidate_runtime_source_sha256": after_hashes,
        "comparison_harness_sha256": _file_sha256(script_path),
        "wave3_sources": {
            "corpus_generator_sha256": CORPUS_SOURCE_SHA256,
            "comparison_harness_sha256": CORPUS_HARNESS_SHA256,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "msgpack": msgpack,
            "zstandard": zstandard,
            "jzpack": candidate.__version__,
        },
        "configuration": {
            "compression_level": 3,
            "samples_per_variant": samples,
            "warmups": 0,
            "sink": "plain io.BytesIO; written byte count and output hash calculated after timer",
            "prebuilt_list": "same list object is passed to both packages; corpus construction occurs before timer",
            "lazy_iter_records": "same frozen generator function and deterministic arguments; record generation occurs in timer",
            "order": "alternates baseline-first and candidate-first by sample; modes/profile order fixed",
            "round_trip": "outside timer; wave-3 recursive type and float64-bit oracle on every archive",
            "acceptance": "every before/after archive must be byte-identical",
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON path for paired timings")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if args.samples < 1 or args.samples > 10:
        parser.error("--samples must be between 1 and 10")
    result = run(args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
