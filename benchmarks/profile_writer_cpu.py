"""Capture sanitized cProfile hotspots for the frozen wave-3 writer workloads.

This is a diagnostic, not a timing benchmark. It profiles one encode pass per
case and method; cProfile overhead makes elapsed seconds unsuitable as product
performance claims. Prebuilt-list methods exclude corpus generation. The lazy
writer case includes ``iter_records`` generation and is reported separately.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import platform
import pstats
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress, iter_decompress, write_records

REPOSITORY = Path(__file__).resolve().parents[1]
CORPUS_SOURCE = REPOSITORY / "benchmarks" / "corpus_wave3.py"
EXPECTED_CORPUS_SOURCE_SHA256 = "596960e45e66cf63e13b47ea08f010dd97e65d0669d1301e13e47f78f912d221"
EXPECTED_BENCHMARK_SOURCE_SHA256 = "7f3aca615ef0df8a6ca92901708ce115263eb6b295c1dd908244e5fa08438b44"
BENCHMARK_SOURCE = REPOSITORY / "benchmarks" / "benchmark_corpus_wave3.py"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_module() -> Any:
    spec = importlib.util.spec_from_file_location("wave3_corpus", CORPUS_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen wave-3 corpus generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _function_rows(profiler: cProfile.Profile, sort_key: str) -> list[dict[str, Any]]:
    stats = pstats.Stats(profiler)
    if sort_key == "cumulative":
        key_index = 3
    else:
        key_index = 2
    entries = sorted(stats.stats.items(), key=lambda item: item[1][key_index], reverse=True)
    rows: list[dict[str, Any]] = []
    for (filename, line, name), (primitive_calls, calls, self_seconds, cumulative_seconds, _) in entries:
        if filename.startswith("~"):
            clean_file = "[extension/builtin]"
        else:
            source_path = Path(filename).resolve()
            try:
                clean_file = source_path.relative_to(REPOSITORY).as_posix()
            except ValueError:
                if source_path == CORPUS_SOURCE.resolve():
                    clean_file = "benchmarks/corpus_wave3.py"
                else:
                    continue
        rows.append(
            {
                "file": clean_file,
                "line": line,
                "function": name,
                "primitive_calls": primitive_calls,
                "calls": calls,
                "self_seconds": round(self_seconds, 6),
                "cumulative_seconds": round(cumulative_seconds, 6),
            }
        )
        if len(rows) == 30:
            break
    return rows


def _profile(label: str, encode: Callable[[], bytes]) -> tuple[bytes, dict[str, Any]]:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    payload = encode()
    profiler.disable()
    return payload, {
        "label": label,
        "profiled_wall_seconds": round(time.perf_counter() - started, 6),
        "top_cumulative": _function_rows(profiler, "cumulative"),
        "top_self": _function_rows(profiler, "self"),
    }


def _profile_writer(records: Any, level: int = 3) -> tuple[bytes, int]:
    sink = io.BytesIO()
    written = write_records(records, sink, compression_level=level)
    payload = sink.getvalue()
    if written != len(payload):
        raise RuntimeError("writer byte count does not match captured archive")
    return payload, written


def run() -> dict[str, Any]:
    corpus = _corpus_module()
    corpus_sha = _sha256(CORPUS_SOURCE)
    benchmark_sha = _sha256(BENCHMARK_SOURCE)
    if corpus_sha != EXPECTED_CORPUS_SOURCE_SHA256:
        raise RuntimeError("wave-3 corpus source differs from the reviewed frozen source")
    if benchmark_sha != EXPECTED_BENCHMARK_SOURCE_SHA256:
        raise RuntimeError("wave-3 harness differs from the reviewed frozen source")

    cases: list[dict[str, Any]] = []
    for profile_name, count in CASES.items():
        records = corpus.build_records(profile_name, count, corpus.DEFAULT_SEED)
        input_bytes, input_hash = corpus.corpus_fingerprint(records)
        profile_rows: list[dict[str, Any]] = []

        memory_payload, memory_profile = _profile(
            f"{profile_name}:compress(prebuilt-list)",
            lambda: compress(records, level=3),
        )
        profile_rows.append(memory_profile)
        if corpus.records_difference(records, decompress(memory_payload)) is not None:
            raise RuntimeError(f"memory API exact round trip failed for {profile_name}")

        writer_payload, writer_profile = _profile(
            f"{profile_name}:write_records(prebuilt-list,BytesIO)",
            lambda: _profile_writer(records)[0],
        )
        profile_rows.append(writer_profile)
        if corpus.records_difference(records, iter_decompress(io.BytesIO(writer_payload))) is not None:
            raise RuntimeError(f"prebuilt-list writer exact round trip failed for {profile_name}")

        lazy_payload, lazy_profile = _profile(
            f"{profile_name}:write_records(lazy-iter_records,BytesIO)",
            lambda: _profile_writer(
                corpus.iter_records(profile_name, count, corpus.DEFAULT_SEED)
            )[0],
        )
        profile_rows.append(lazy_profile)
        if corpus.records_difference(records, iter_decompress(io.BytesIO(lazy_payload))) is not None:
            raise RuntimeError(f"lazy writer exact round trip failed for {profile_name}")

        cases.append(
            {
                "profile": profile_name,
                "records": count,
                "seed": corpus.DEFAULT_SEED,
                "canonical_input_bytes": input_bytes,
                "input_sha256": input_hash,
                "methods": [
                    _archive_summary("compress(prebuilt-list)", memory_payload),
                    _archive_summary("write_records(prebuilt-list,BytesIO)", writer_payload),
                    _archive_summary("write_records(lazy-iter_records,BytesIO)", lazy_payload),
                ],
                "profiles": profile_rows,
            }
        )

    try:
        zstandard_native = importlib.metadata.version("zstandard")
    except importlib.metadata.PackageNotFoundError:
        zstandard_native = "unavailable"
    return {
        "format": "jzpack-writer-cprofile-v1",
        "purpose": "single-pass CPU hotspot diagnosis; not a wall-clock benchmark",
        "source_revision": _git_head(),
        "runtime_source_sha256": {path: _sha256(REPOSITORY / path) for path in RUNTIME_SOURCES},
        "profile_harness_sha256": _sha256(Path(__file__).resolve()),
        "wave3_sources": {
            "corpus_generator_sha256": corpus_sha,
            "comparison_harness_sha256": benchmark_sha,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "jzpack": jzpack_version,
            "msgpack": importlib.metadata.version("msgpack"),
            "zstandard": zstandard_native,
        },
        "configuration": {
            "compression_level": 3,
            "input_mode_comparison": "compress and writer each receive the same prebuilt list; lazy writer separately includes corpus generation",
            "sink": "un-instrumented io.BytesIO; no per-write hashing or first-chunk timer",
            "samples_per_case_and_method": 1,
            "round_trip": "each archive checked after profiling with wave-3 recursive type and float64-bit oracle",
            "container_bytes": "memory and writer output are summarized independently; byte equality is not required because chunk policies differ",
        },
        "cases": cases,
    }


def _archive_summary(label: str, payload: bytes) -> dict[str, Any]:
    return {
        "label": label,
        "archive_bytes": len(payload),
        "archive_sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON destination for sanitized cProfile evidence")
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
