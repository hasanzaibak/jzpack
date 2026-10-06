"""Held-out Zstandard-level sweep on three pinned JSON-compatible corpora."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zstandard as zstd

from jzpack import __version__ as jzpack_version
from jzpack import compress, decompress

if __package__:
    from .corpus_wave3 import corpus_fingerprint, records_difference
else:  # pragma: no cover - supports direct script execution for the worker.
    from corpus_wave3 import corpus_fingerprint, records_difference

_REPOSITORY = Path(__file__).resolve().parents[1]
_DATA_DIR = Path.home() / ".cache" / "jzpack" / "realistic-levels"

LEVELS = (1, 3, 9)
WARMUPS_PER_LEVEL = 1
ORDER_CYCLES = ((1, 3, 9), (3, 9, 1), (9, 1, 3)) * 3
TIMING_SAMPLES_PER_LEVEL = len(ORDER_CYCLES)

SYNTHEA_COMMIT = "9959d9178ea28f4ec10f17ee238b6fabe6eb0de5"
SYNTHEA_URL = (
    "https://raw.githubusercontent.com/synthetichealth/synthea-sample-data/"
    f"{SYNTHEA_COMMIT}/downloads/latest/synthea_sample_data_fhir_latest.zip"
)
SYNTHEA_SHA256 = "56cb9e49f7ba6ad4e61c40aa80999f8c10a710823fed1becdf2502053777a521"
SYNTHEA_FILENAME = "synthea_sample_data_fhir_latest.zip"
SYNTHEA_BUNDLE_COUNT = 16
SYNTHEA_INPUT_SHA256 = "cce7427ce7638392dfea5efab3d7e6d56833c8b30fb9e566f7f6ecfedfc85697"

NATURAL_EARTH_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"
NATURAL_EARTH_FILENAME = "ne_110m_admin_0_countries.geojson"
NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    f"{NATURAL_EARTH_COMMIT}/geojson/{NATURAL_EARTH_FILENAME}"
)
NATURAL_EARTH_SHA256 = "6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f"
NATURAL_EARTH_INPUT_SHA256 = "f327febdd4d44394f21eed54e424b19115719348d1425803a17334b1f7458d05"

USGS_FILENAME = "usgs-earthquakes-2024-01.geojson"
USGS_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
    "&starttime=2024-01-01&endtime=2024-02-01&minmagnitude=4.5"
    "&orderby=time-asc&limit=20000"
)
USGS_CAPTURE_SHA256 = "9661fe4b9b69ea65ce72eed8c8f0514dc497abf3ba6c5dcb5fb538b25b54bcb5"
USGS_INPUT_SHA256 = "73bfb295a1ce5539dc1272d05f3d19bde7ce77b789b02327974515ae070fd915"

EXPECTED_INPUT_SHA256 = {
    "synthea-fhir-r4": SYNTHEA_INPUT_SHA256,
    "natural-earth-countries": NATURAL_EARTH_INPUT_SHA256,
    "usgs-earthquakes-2024-01": USGS_INPUT_SHA256,
}
CORPUS_NAMES = tuple(EXPECTED_INPUT_SHA256)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "jzpack-realistic-level-benchmark/1"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".partial")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary_path.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _ensure_source(path: Path, url: str, expected_sha256: str | None) -> str:
    if not path.exists():
        _download(url, path)
    actual_sha256 = _file_sha256(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RuntimeError(f"source fingerprint mismatch for {path.name}: {actual_sha256}")
    return actual_sha256


def _load_synthea(data_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = data_dir / SYNTHEA_FILENAME
    source_sha256 = _ensure_source(path, SYNTHEA_URL, SYNTHEA_SHA256)
    records: list[dict[str, Any]] = []
    bundle_hashes: list[str] = []
    bundle_names: list[str] = []
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".json"))
        for name in members:
            raw = archive.read(name)
            value = json.loads(raw)
            if (
                type(value) is dict
                and value.get("resourceType") == "Bundle"
                and type(value.get("entry")) is list
            ):
                records.append(value)
                bundle_hashes.append(_sha256(raw))
                bundle_names.append(name)
                if len(records) == SYNTHEA_BUNDLE_COUNT:
                    break
    if len(records) != SYNTHEA_BUNDLE_COUNT:
        raise RuntimeError(f"expected {SYNTHEA_BUNDLE_COUNT} Synthea bundles, found {len(records)}")
    return records, {
        "source_url": SYNTHEA_URL,
        "repository_commit": SYNTHEA_COMMIT,
        "raw_source_sha256": source_sha256,
        "selected_bundle_count": len(records),
        "selection": "lexicographically first 16 ZIP members whose JSON resourceType is Bundle",
        "ordered_selected_member_names_sha256": _sha256("\n".join(bundle_names).encode("utf-8")),
        "ordered_selected_bundle_sha256": bundle_hashes,
        "reuse_terms": "A Synthea project contributor confirmed generated data could be used for the proposed course use.",
        "reuse_terms_url": "https://github.com/synthetichealth/synthea/discussions/1495",
    }


def _load_feature_collection(
    data_dir: Path,
    *,
    filename: str,
    url: str,
    expected_source_sha256: str | None,
    source_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = data_dir / filename
    raw_source_sha256 = _ensure_source(path, url, expected_source_sha256)
    document = json.loads(path.read_bytes())
    if type(document) is not dict or document.get("type") != "FeatureCollection":
        raise RuntimeError(f"{filename} is not a GeoJSON FeatureCollection")
    features = document.get("features")
    if type(features) is not list or any(type(feature) is not dict for feature in features):
        raise RuntimeError(f"{filename} has invalid GeoJSON features")
    return features, {
        "source_url": url,
        "raw_source_sha256": raw_source_sha256,
        "record_selection": "ordered FeatureCollection.features; collection metadata excluded",
        **source_metadata,
    }


def _load_corpus(name: str, data_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if name == "synthea-fhir-r4":
        records, source = _load_synthea(data_dir)
    elif name == "natural-earth-countries":
        records, source = _load_feature_collection(
            data_dir,
            filename=NATURAL_EARTH_FILENAME,
            url=NATURAL_EARTH_URL,
            expected_source_sha256=NATURAL_EARTH_SHA256,
            source_metadata={
                "repository_commit": NATURAL_EARTH_COMMIT,
                "public_domain": True,
                "reuse_terms_url": "https://www.naturalearthdata.com/about/terms-of-use/",
            },
        )
    elif name == "usgs-earthquakes-2024-01":
        records, source = _load_feature_collection(
            data_dir,
            filename=USGS_FILENAME,
            url=USGS_URL,
            expected_source_sha256=None,
            source_metadata={
                "query_window_utc": {"start": "2024-01-01", "end": "2024-02-01"},
                "minimum_magnitude": 4.5,
                "query_response_generated_timestamp_may_change": True,
                "capture_raw_source_sha256": USGS_CAPTURE_SHA256,
                "public_domain": True,
                "reuse_terms_url": "https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted",
            },
        )
    else:
        raise ValueError(f"unknown corpus: {name}")

    input_bytes, input_sha256 = corpus_fingerprint(records)
    expected_sha256 = EXPECTED_INPUT_SHA256[name]
    if input_sha256 != expected_sha256:
        raise RuntimeError(f"normalized input fingerprint mismatch for {name}: {input_sha256}")
    source.update(
        {
            "record_count": len(records),
            "canonical_input_bytes": input_bytes,
            "ordered_canonical_input_sha256": input_sha256,
        }
    )
    return records, source


def _max_rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(value if sys.platform == "darwin" else value * 1024)
    return peak_bytes / (1024 * 1024)


def _rss_worker(name: str, level: int, data_dir: Path) -> dict[str, Any]:
    records, source = _load_corpus(name, data_dir)
    peak_before_encode_mib = _max_rss_mib()
    archive = compress(records, level=level)
    peak_after_encode_mib = _max_rss_mib()
    actual = decompress(archive)
    difference = records_difference(records, actual)
    if difference is not None:
        raise RuntimeError(f"RSS worker exact round-trip failed: {difference}")
    return {
        "level": level,
        "archive_bytes": len(archive),
        "archive_sha256": _sha256(archive),
        "peak_rss_before_encode_mib": peak_before_encode_mib,
        "peak_process_rss_after_encode_mib": peak_after_encode_mib,
        "exact_round_trip_validated": True,
        "scope": "isolated process high-water RSS after encode and before decode; includes input load, interpreter, and imports",
        "input_sha256": source["ordered_canonical_input_sha256"],
    }


def _isolated_rss(name: str, level: int, data_dir: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.benchmark_realistic_levels",
        "--_rss-worker",
        name,
        "--level",
        str(level),
        "--data-dir",
        str(data_dir),
    ]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(_REPOSITORY)
        if not existing_pythonpath
        else os.pathsep.join((str(_REPOSITORY), existing_pythonpath))
    )
    result = subprocess.run(
        command,
        cwd=_REPOSITORY,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"isolated RSS worker failed for {name}, level {level}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _git_value(*args: str) -> str:
    environment_key = {
        ("rev-parse", "HEAD"): "JZPACK_BENCHMARK_REPOSITORY_HEAD",
        ("branch", "--show-current"): "JZPACK_BENCHMARK_REPOSITORY_BRANCH",
        ("status", "--porcelain", "--", "jzpack"): "JZPACK_BENCHMARK_PACKAGE_RUNTIME_STATUS",
    }.get(args)
    if environment_key and environment_key in os.environ:
        return os.environ[environment_key]
    try:
        result = subprocess.run(
            ["git", *args], cwd=_REPOSITORY, capture_output=True, check=True, text=True
        )
    except FileNotFoundError:
        return "unavailable"
    return result.stdout.strip()


def _runtime_source_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted((_REPOSITORY / "jzpack").rglob("*.py")):
        relative = path.relative_to(_REPOSITORY).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "jzpack": jzpack_version,
        "msgpack": importlib.metadata.version("msgpack"),
        "python_zstandard": importlib.metadata.version("zstandard"),
        "zstandard_library": ".".join(str(part) for part in zstd.ZSTD_VERSION),
        "os_cpu_count": os.cpu_count(),
        "container_image": os.environ.get("JZPACK_BENCHMARK_CONTAINER_IMAGE"),
        "container_cpu_limit": os.environ.get("JZPACK_BENCHMARK_CPU_LIMIT"),
        "container_memory_limit": os.environ.get("JZPACK_BENCHMARK_MEMORY_LIMIT"),
    }


def _capture(data_dir: Path) -> dict[str, Any]:
    corpora: dict[str, Any] = {}
    # Linux carries ru_maxrss through exec; collect these workers before the
    # timing process loads any large input so they cannot inherit a later peak.
    rss_by_corpus = {
        name: {level: _isolated_rss(name, level, data_dir) for level in LEVELS}
        for name in CORPUS_NAMES
    }
    order = tuple(level for cycle in ORDER_CYCLES for level in cycle)
    for name in CORPUS_NAMES:
        records, source = _load_corpus(name, data_dir)
        initial_fingerprint = corpus_fingerprint(records)
        timings: dict[int, list[float]] = {level: [] for level in LEVELS}
        output_hashes: dict[int, set[str]] = {level: set() for level in LEVELS}
        output_sizes: dict[int, set[int]] = {level: set() for level in LEVELS}

        for level in LEVELS:
            warmup_archive = compress(records, level=level)
            difference = records_difference(records, decompress(warmup_archive))
            if difference is not None:
                raise RuntimeError(f"warmup exact round-trip failed for {name}, level {level}: {difference}")

        for level in order:
            started = time.perf_counter()
            archive = compress(records, level=level)
            timings[level].append(time.perf_counter() - started)
            archive_sha256 = _sha256(archive)
            output_hashes[level].add(archive_sha256)
            output_sizes[level].add(len(archive))
            difference = records_difference(records, decompress(archive))
            if difference is not None:
                raise RuntimeError(f"timed exact round-trip failed for {name}, level {level}: {difference}")

        if corpus_fingerprint(records) != initial_fingerprint:
            raise RuntimeError(f"input corpus was mutated during measurement: {name}")

        results: list[dict[str, Any]] = []
        for level in LEVELS:
            if len(output_hashes[level]) != 1 or len(output_sizes[level]) != 1:
                raise RuntimeError(f"non-deterministic archive for {name}, level {level}")
            rss = rss_by_corpus[name][level]
            if rss["archive_bytes"] != next(iter(output_sizes[level])):
                raise RuntimeError(f"RSS worker archive size differed for {name}, level {level}")
            if rss["archive_sha256"] != next(iter(output_hashes[level])):
                raise RuntimeError(f"RSS worker archive hash differed for {name}, level {level}")
            samples = timings[level]
            results.append(
                {
                    "level": level,
                    "encode_time_seconds_samples": samples,
                    "encode_time_seconds_median": statistics.median(samples),
                    "encode_time_seconds_minimum": min(samples),
                    "encode_time_seconds_maximum": max(samples),
                    "archive_bytes": next(iter(output_sizes[level])),
                    "archive_sha256": next(iter(output_hashes[level])),
                    "exact_round_trip_validated_for_warmup_and_all_samples": True,
                    "isolated_peak_rss": rss,
                }
            )
        corpora[name] = {"source": source, "results": results}

    return {
        "schema_version": 1,
        "capture_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": {
            "public_api": "jzpack.compress(records, level=level)",
            "levels": list(LEVELS),
            "warmups_per_level": WARMUPS_PER_LEVEL,
            "timed_samples_per_level": TIMING_SAMPLES_PER_LEVEL,
            "order_cycles": [list(cycle) for cycle in ORDER_CYCLES],
            "timing_scope": "compress() only; source loading, decompression, and exact oracle excluded",
            "round_trip": "decompress() plus recursive type, structure, order, and float-bit oracle after every warmup and timed encode",
            "archive_determinism_verified_per_level": True,
            "input_mutation_checked": True,
            "timing_process_count": 1,
            "rss_workers_run_before_timing": True,
            "rss_method": "one fresh subprocess per corpus and level using resource.getrusage(RUSAGE_SELF).ru_maxrss; workers run before the timing process loads any corpus",
        },
        "source": {
            "repository_head": _git_value("rev-parse", "HEAD"),
            "repository_branch": _git_value("branch", "--show-current"),
            "package_runtime_source_sha256": _runtime_source_sha256(),
            "benchmark_harness_sha256": _file_sha256(Path(__file__).resolve()),
            "corpus_fingerprint_and_oracle_sha256": _file_sha256(
                _REPOSITORY / "benchmarks" / "corpus_wave3.py"
            ),
            "package_runtime_files_clean": _git_value("status", "--porcelain", "--", "jzpack") == "",
            "raw_data_committed": False,
        },
        "environment": _environment(),
        "corpora": corpora,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=_DATA_DIR, help="external cache for pinned source files")
    parser.add_argument("--output", type=Path, help="write the result JSON here instead of stdout")
    parser.add_argument("--_rss-worker", choices=CORPUS_NAMES, help=argparse.SUPPRESS)
    parser.add_argument("--level", type=int, choices=LEVELS, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args._rss_worker:
        if args.level is None:
            parser.error("the RSS worker requires --level")
        print(json.dumps(_rss_worker(args._rss_worker, args.level, args.data_dir), sort_keys=True))
        return 0

    result = _capture(args.data_dir)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
