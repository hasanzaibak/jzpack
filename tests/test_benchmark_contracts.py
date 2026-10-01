"""Subprocess-level contracts for the deterministic comparison benchmark CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from benchmarks.corpus import build_records, corpus_fingerprint

REPOSITORY = Path(__file__).resolve().parents[1]
BENCHMARK = REPOSITORY / "benchmarks" / "benchmark_corpus.py"
GOLDEN_FINGERPRINTS = {
    "mixed-events": (665, "910e4f0e55a2ab2172c7d672f7ff3d149d691ce45e0cb55831d8e6150b1aaf82"),
    "high-entropy": (840, "51181548f2cda977690722c6f5a89c1aa0c5131bfb17e413e3b32c2436858277"),
}
GOLDEN_SEED = 20_261_001
GOLDEN_RECORDS = 5


def _run_cli(*arguments: str, pythonpath_prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    pythonpath = [str(REPOSITORY)]
    if pythonpath_prefix is not None:
        pythonpath.insert(0, str(pythonpath_prefix))
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return subprocess.run(
        [sys.executable, str(BENCHMARK), *arguments],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _install_sitecustomize(directory: Path, source: str) -> Path:
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return directory


def test_corpus_v1_golden_fingerprints_distinguish_profiles() -> None:
    observed = {
        profile: corpus_fingerprint(build_records(profile, GOLDEN_RECORDS, seed=GOLDEN_SEED))
        for profile in GOLDEN_FINGERPRINTS
    }

    assert observed == GOLDEN_FINGERPRINTS
    assert observed["mixed-events"][1] != observed["high-entropy"][1]


def test_comparative_cli_json_schema_matches_seeded_corpus_and_sample_counts() -> None:
    profiles = tuple(GOLDEN_FINGERPRINTS)
    completed = _run_cli(
        "--profiles",
        *profiles,
        "--records",
        str(GOLDEN_RECORDS),
        "--iterations",
        "2",
        "--warmups",
        "0",
        "--seed",
        str(GOLDEN_SEED),
        "--level",
        "3",
        "--skip-rss",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    assert len(completed.stdout.splitlines()) == 1
    result = json.loads(completed.stdout)
    assert set(result) == {
        "benchmark",
        "corpus",
        "configuration",
        "runtime",
        "rss_metric",
        "workloads",
    }
    assert result["benchmark"] == "jzpack-comparative-corpus"
    assert result["corpus"]["version"] == 1
    assert result["corpus"]["seed"] == GOLDEN_SEED
    assert result["corpus"]["record_count_per_profile"] == GOLDEN_RECORDS
    assert result["corpus"]["profiles"] == list(profiles)
    assert result["configuration"] == {"level": 3, "iterations": 2, "warmups": 0}
    assert result["rss_metric"] == "not measured"
    assert isinstance(result["runtime"]["python_version"], str)
    assert isinstance(result["runtime"]["dependencies"]["zstandard"], str)
    assert set(result["workloads"]) == set(profiles)

    for profile, (reference_size, fingerprint) in GOLDEN_FINGERPRINTS.items():
        workload = result["workloads"][profile]
        assert workload["record_count"] == GOLDEN_RECORDS
        assert workload["reference_input_size_bytes"] == reference_size
        assert workload["reference_input_sha256"] == fingerprint
        implementations = workload["implementations"]
        assert {"jzpack", "msgpack-zstd"} <= set(implementations)
        for implementation in implementations.values():
            assert implementation["reference_input_size_bytes"] == reference_size
            assert implementation["reference_input_sha256"] == fingerprint
            assert implementation["exact_round_trip_validated"] is True
            assert len(implementation["compressed_size_samples_bytes"]) == 4
            assert implementation["compressed_size_bytes"] == implementation["compressed_size_samples_bytes"][0]
            assert re.fullmatch(r"[0-9a-f]{64}", implementation["compressed_output_sha256"])
            for timing in implementation["timings"].values():
                assert len(timing["samples_seconds"]) == 2


def test_human_cli_output_contains_metrics_without_record_values() -> None:
    profile = "high-entropy"
    seed = 91
    count = 3
    records = build_records(profile, count, seed)
    completed = _run_cli(
        "--profiles",
        profile,
        "--records",
        str(count),
        "--iterations",
        "1",
        "--warmups",
        "0",
        "--seed",
        str(seed),
        "--skip-rss",
    )

    assert completed.returncode == 0, completed.stderr
    assert "benchmark: jzpack-comparative-corpus (corpus v1)" in completed.stdout
    assert "high-entropy: 3 records," in completed.stdout
    assert "encode median" in completed.stdout
    assert "decode median" in completed.stdout
    assert all(record["digest"] not in completed.stdout for record in records)
    assert all(record["payload"] not in completed.stdout for record in records)


@pytest.mark.parametrize("level", [1, 22])
def test_cli_accepts_compression_level_endpoints(level: int) -> None:
    completed = _run_cli(
        "--profiles",
        "mixed-events",
        "--records",
        "1",
        "--iterations",
        "1",
        "--warmups",
        "0",
        "--level",
        str(level),
        "--skip-rss",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["configuration"]["level"] == level


@pytest.mark.parametrize(
    "arguments",
    [
        ("--records", "0"),
        ("--iterations", "0"),
        ("--warmups", "-1"),
        ("--level", "0"),
        ("--level", "23"),
        ("--profiles", "unknown-profile"),
    ],
)
def test_invalid_cli_arguments_have_stable_argparse_exit(arguments: tuple[str, str]) -> None:
    completed = _run_cli(*arguments)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "error:" in completed.stderr


def test_optional_orjson_import_absence_keeps_required_baselines_available(tmp_path: Path) -> None:
    hook = _install_sitecustomize(
        tmp_path / "without-orjson",
        """
        import sys

        class HideOrjson:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "orjson":
                    raise ModuleNotFoundError("orjson disabled for benchmark test", name=fullname)

        sys.meta_path.insert(0, HideOrjson())
        """,
    )

    completed = _run_cli(
        "--profiles",
        "mixed-events",
        "--records",
        "2",
        "--iterations",
        "1",
        "--warmups",
        "0",
        "--skip-rss",
        "--json",
        pythonpath_prefix=hook,
    )

    assert completed.returncode == 0, completed.stderr
    implementations = json.loads(completed.stdout)["workloads"]["mixed-events"]["implementations"]
    assert set(implementations) == {"jzpack", "msgpack-zstd"}
    assert all(item["exact_round_trip_validated"] for item in implementations.values())


def test_controlled_cli_failure_discloses_only_exception_class(tmp_path: Path) -> None:
    hook = _install_sitecustomize(
        tmp_path / "controlled-failure",
        """
        from benchmarks import corpus

        def fail_with_sensitive_message(*args, **kwargs):
            raise RuntimeError("private-record-payload-marker")

        corpus.build_records = fail_with_sensitive_message
        """,
    )

    completed = _run_cli(
        "--profiles",
        "mixed-events",
        "--records",
        "2",
        "--iterations",
        "1",
        "--warmups",
        "0",
        "--skip-rss",
        "--json",
        pythonpath_prefix=hook,
    )

    assert completed.returncode == 3
    assert completed.stdout == ""
    assert completed.stderr == "benchmark failed: RuntimeError\n"
    assert "private-record-payload-marker" not in completed.stderr


def test_exact_validator_rejects_nested_sequence_length_mismatch() -> None:
    from benchmarks.benchmark_corpus import _same_value

    assert not _same_value([{"values": [1]}], [{"values": [1]}, {}])
    assert not _same_value({"values": [1]}, {"values": [1, 2]})
