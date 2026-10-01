"""Contracts for the larger, separate wave-3 benchmark corpus and harness."""

from __future__ import annotations

import io
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import benchmark_corpus_wave3 as benchmark
from benchmarks.corpus_wave3 import (
    build_records,
    corpus_fingerprint,
    first_difference,
    is_faithful,
    iter_records,
    records_difference,
)
from jzpack import write_records

REPOSITORY = Path(__file__).resolve().parents[1]
BENCHMARK = REPOSITORY / "benchmarks" / "benchmark_corpus_wave3.py"
GOLDEN_FINGERPRINTS = {
    "schema-diverse": (10_148, "d8cbf577cb2cd8d7c058e99a6924d723811b886a25f4f87c2cc3daace1813604"),
    "nested-records": (16_856, "0de59584f7aff639eff51c7306e5dd1e041e92d3ab14b18e1b80bce56bc0ee25"),
    "large-strings": (811, "f6699fa5957fb1e62c0b6906640b19737852138d880019faef37dfc8a8202b2f"),
}


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    pythonpath = [str(REPOSITORY)]
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


def test_wave3_golden_fingerprints_pin_all_three_shapes() -> None:
    observed = {
        "schema-diverse": corpus_fingerprint(build_records("schema-diverse", 37)),
        "nested-records": corpus_fingerprint(build_records("nested-records", 37)),
        "large-strings": corpus_fingerprint(
            build_records("large-strings", 3, large_string_bytes=128)
        ),
    }

    assert observed == GOLDEN_FINGERPRINTS
    assert len({fingerprint for _, fingerprint in observed.values()}) == 3


def test_generator_is_lazy_and_large_string_size_means_exact_utf8_bytes() -> None:
    records = iter_records("large-strings", 2, large_string_bytes=17)

    first = next(records)
    second = next(records)

    assert first["record_id"] == 0
    assert second["record_id"] == 1
    assert len(first["payload"].encode("utf-8")) == 17
    assert len(second["payload"].encode("utf-8")) == 17
    with pytest.raises(StopIteration):
        next(records)


def test_schema_diverse_profile_keeps_absent_null_and_fixed_typed_values_distinct() -> None:
    records = build_records("schema-diverse", 512)
    field = "optional_06"
    observed = [record[field] if field in record else "<absent>" for record in records]

    assert "<absent>" in observed
    assert None in observed
    typed_values = [value for value in observed if value is not None and value != "<absent>"]
    assert typed_values
    assert {type(value) for value in typed_values} == {bool}
    assert len({tuple(sorted(record)) for record in records}) > 1


def test_wave3_exact_oracle_checks_builtin_types_float_bits_and_full_lengths() -> None:
    nan_a = struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0]
    nan_b = struct.unpack(">d", bytes.fromhex("7ff8000000000002"))[0]

    assert is_faithful({"nested": [True, 0.0, nan_a]}, {"nested": [True, 0.0, nan_a]})
    assert not is_faithful({"nested": [True]}, {"nested": [1]})
    assert not is_faithful({"nested": [0.0]}, {"nested": [-0.0]})
    assert not is_faithful({"nested": [nan_a]}, {"nested": [nan_b]})
    assert first_difference({"nested": [0.0]}, {"nested": [-0.0]}) == "$record.nested[0]: float64 bits differ"
    assert records_difference([{"id": 1}], [{"id": 1}, {"id": 2}]) == "record count: extra output at index 1"


def test_wave3_oracle_distinguishes_missing_from_null_and_reports_nested_length() -> None:
    assert first_difference({"optional": None}, {}) == "$record.optional: key is missing"
    assert first_difference({"nested": [1]}, {"nested": [1, 2]}) == (
        "$record.nested: list length 1 != 2"
    )
    assert records_difference([{"id": 1}, {"id": 2}], [{"id": 1}]) == (
        "record count: missing output at index 1"
    )


def test_parquet_eligibility_checks_the_entire_input_before_allowing_comparison(monkeypatch) -> None:
    records = [{"index": index, "value": index} for index in range(300)]
    decoded = [dict(record) for record in records]
    decoded[-1]["value"] = None

    monkeypatch.setattr(benchmark, "pa", object())
    monkeypatch.setattr(benchmark, "pq", object())
    monkeypatch.setattr(benchmark, "_encode_once", lambda *args: (b"fake", {}))
    monkeypatch.setattr(benchmark, "_decode_once", lambda *args: decoded)

    status = benchmark._parquet_eligibility(records)

    assert status == {
        "available": True,
        "eligible": False,
        "reason": "$[299].value: type int != NoneType",
    }


def test_observed_sink_waits_for_a_complete_nonempty_first_chunk() -> None:
    sink = benchmark._ObservedSink(retain=False, started_at=benchmark.time.perf_counter() - 0.01)
    outer_header = b"JZPK\x03" + bytes(11)
    chunk_header = bytearray(56)
    chunk_header[:4] = b"CHNK"
    chunk_header[16:24] = (2).to_bytes(8, "big")
    chunk_header[40:48] = (4).to_bytes(8, "big")

    sink.write(outer_header + chunk_header + b"ab")
    assert sink.first_chunk_seconds is None
    assert sink.first_chunk_records is None

    sink.write(b"cd")
    assert sink.first_chunk_seconds is not None
    assert sink.first_chunk_seconds > 0
    assert sink.first_chunk_records == 2


def test_writer_decode_timing_materializes_rows_like_other_comparators() -> None:
    records = build_records("nested-records", 5)
    sink = io.BytesIO()
    write_records(records, sink)

    decoded = benchmark._decode_once("jzpack-writer", sink.getvalue())

    assert type(decoded) is list
    assert records_difference(records, decoded) is None


def test_smoke_suite_reports_exact_roundtrips_and_respects_parquet_eligibility() -> None:
    result = benchmark.run_benchmark(
        suite="smoke",
        samples=2,
        warmups=0,
        large_string_bytes=128,
        measure_memory=False,
    )

    assert result["benchmark"] == "jzpack-corpus-wave-3"
    assert result["corpus"]["version"] == 2
    assert result["configuration"]["suite"] == "smoke"
    assert re.fullmatch(r"[0-9a-f]{64}", result["runtime"]["benchmark_source_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", result["runtime"]["corpus_source_sha256"])
    assert len(result["workloads"]) == 3
    for workload in result["workloads"]:
        implementations = workload["implementations"]
        assert {"jzpack-memory", "jzpack-writer", "msgpack-zstd"} <= set(implementations)
        assert ("parquet-zstd" in implementations) is workload["parquet_eligibility"]["eligible"]
        for item in implementations.values():
            assert item["exact_round_trip_validated"] is True
            assert item["deterministic_output"] is True
            assert len(item["archive_sha256"]) == 64
            assert len(item["encode"]["samples_seconds"]) == 2
            assert len(item["decode"]["samples_seconds"]) == 2
        writer = implementations["jzpack-writer"]
        assert writer["first_output"]["kind"] == "first_complete_nonempty_v3_chunk"
        assert len(writer["first_output"]["records_in_first_chunk_samples"]) == 2
        assert all(
            1 <= rows <= workload["record_count"]
            for rows in writer["first_output"]["records_in_first_chunk_samples"]
        )

    schema = next(item for item in result["workloads"] if item["profile"] == "schema-diverse")
    assert not schema["parquet_eligibility"]["eligible"]
    if schema["parquet_eligibility"]["available"]:
        assert "key is missing" in schema["parquet_eligibility"]["reason"]


def test_isolated_memory_smoke_outputs_match_the_timed_archives() -> None:
    result = benchmark.run_benchmark(
        suite="smoke",
        samples=1,
        warmups=0,
        memory_samples=1,
        large_string_bytes=128,
        measure_memory=True,
    )

    for probe in result["memory_probes"]:
        workload = next(
            item
            for item in result["workloads"]
            if item["profile"] == probe["profile"] and item["record_count"] == probe["record_count"]
        )
        expected = workload["implementations"][probe["method"]]["archive_sha256"]
        assert all(sample["archive_sha256"] == expected for sample in probe["samples"])
        assert len(probe["samples"]) == 1
        assert probe["samples"][0]["python_tracemalloc_peak_bytes"] >= 0


def test_wave3_cli_emits_one_json_document_for_smoke_run() -> None:
    completed = _run_cli("--suite", "smoke", "--samples", "1", "--warmups", "0", "--skip-memory")

    assert completed.returncode == 0, completed.stderr
    assert len(completed.stdout.splitlines()) == 1
    result = json.loads(completed.stdout)
    assert result["benchmark"] == "jzpack-corpus-wave-3"
    assert result["configuration"]["memory_measured_in_isolated_processes"] is False
    assert result["memory_probes"] == []


def test_wave3_cli_uses_argparse_for_unknown_options_and_redacts_run_failures() -> None:
    invalid_option = _run_cli("--not-a-wave3-option")
    invalid_run = _run_cli("--samples", "0", "--skip-memory")

    assert invalid_option.returncode == 2
    assert invalid_option.stdout == ""
    assert "error:" in invalid_option.stderr
    assert invalid_run.returncode == benchmark.FAILURE_EXIT_CODE
    assert invalid_run.stdout == ""
    assert invalid_run.stderr == "benchmark failed: ValueError\n"
