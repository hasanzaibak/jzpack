import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import benchmark

REPOSITORY = Path(__file__).parents[1]
BENCHMARK = REPOSITORY / "benchmarks" / "benchmark.py"


def run_cli(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    process_environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPOSITORY), process_environment.get("PYTHONPATH")) if value
    )
    if env:
        process_environment.update(env)
    return subprocess.run(
        [sys.executable, str(BENCHMARK), *arguments],
        cwd=REPOSITORY,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_build_records_is_deterministic():
    assert benchmark.build_records(3) == benchmark.build_records(3)
    assert benchmark.build_records(0) == []


def test_zero_record_round_trip():
    result = benchmark.run_benchmark([], level=3, iterations=1)

    assert result["configuration"]["records"] == 0
    assert result["dataset"]["reference_input_size_bytes"] == 0
    assert result["round_trip_validated"] is True


def test_small_json_execution_has_stable_typed_fields():
    completed = run_cli("--records", "3", "--iterations", "2", "--json")

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert set(result) == {
        "benchmark",
        "budget_status",
        "configuration",
        "dataset",
        "memory",
        "round_trip_validated",
        "runtime",
        "timings",
    }
    assert result["benchmark"] == "jzpack"
    assert result["configuration"] == {"records": 3, "level": 3, "iterations": 2}
    assert isinstance(result["dataset"]["reference_input_size_bytes"], int)
    assert isinstance(result["dataset"]["compressed_size_bytes"], int)
    assert isinstance(result["runtime"]["jzpack_version"], str)
    assert isinstance(result["runtime"]["python_version"], str)
    assert isinstance(result["runtime"]["platform"], str)
    assert isinstance(result["runtime"]["dependencies"]["msgpack"], str)
    assert isinstance(result["runtime"]["dependencies"]["zstandard"], str)
    assert result["timings"]["compression"]["average_seconds"] >= 0
    assert result["timings"]["decompression"]["stdev_seconds"] >= 0
    assert isinstance(result["memory"]["peak_python_memory_mib"], float)
    assert result["budget_status"]["passed"] is True
    assert result["budget_status"]["failed_budgets"] == []


def test_generous_budgets_pass():
    completed = run_cli(
        "--records",
        "3",
        "--iterations",
        "1",
        "--max-compress-seconds",
        "1000000",
        "--max-decompress-seconds",
        "1000000",
        "--max-memory-mib",
        "1000000",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["budget_status"]["passed"] is True
    assert result["budget_status"]["checks"] == {
        "max-compress-seconds": True,
        "max-decompress-seconds": True,
        "max-memory-mib": True,
    }


@pytest.mark.parametrize(
    ("budget_flag", "budget_name"),
    [
        ("--max-compress-seconds", "max-compress-seconds"),
        ("--max-decompress-seconds", "max-decompress-seconds"),
        ("--max-memory-mib", "max-memory-mib"),
    ],
)
def test_budget_failure_is_non_zero_and_identifies_budget(budget_flag: str, budget_name: str):
    completed = run_cli("--records", "3", "--iterations", "1", budget_flag, "0", "--json")

    assert completed.returncode == benchmark.BUDGET_FAILURE_EXIT_CODE
    result = json.loads(completed.stdout)
    assert result["budget_status"]["passed"] is False
    assert budget_name in result["budget_status"]["failed_budgets"]
    assert budget_name in completed.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ("--records", "-1"),
        ("--iterations", "0"),
        ("--level", "0"),
        ("--level", "23"),
        ("--max-compress-seconds", "-1"),
        ("--max-decompress-seconds", "-1"),
        ("--max-memory-mib", "-1"),
    ],
)
def test_invalid_arguments_fail_through_argparse(arguments: tuple[str, str]):
    completed = run_cli(*arguments)

    assert completed.returncode == 2
    assert "error:" in completed.stderr


def test_round_trip_validation_is_an_assertion(monkeypatch):
    monkeypatch.setattr(benchmark, "decompress", lambda _compressed: [])

    with pytest.raises(AssertionError, match="round-trip validation failed"):
        benchmark.run_benchmark(benchmark.build_records(1), level=3, iterations=1)


def test_json_output_excludes_paths_environment_values_and_raw_records():
    secret = "benchmark-secret-that-must-not-leak"
    completed = run_cli(
        "--records",
        "1",
        "--iterations",
        "1",
        "--json",
        env={"JZPACK_BENCHMARK_TEST_SECRET": secret},
    )

    assert completed.returncode == 0, completed.stderr
    assert str(REPOSITORY) not in completed.stdout
    assert secret not in completed.stdout
    assert "api-gateway" not in completed.stdout
    assert "active" not in completed.stdout
    json.loads(completed.stdout)
