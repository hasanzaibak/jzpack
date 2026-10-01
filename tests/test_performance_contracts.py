import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import zstandard as zstd

from benchmarks import benchmark_corpus
from benchmarks.corpus import PROFILE_NAMES, build_records, corpus_fingerprint
from jzpack import InvalidFormatError, ResourceLimitError, StreamingCompressor, decompress
from jzpack.serializer import CompressionEngine

REPOSITORY = Path(__file__).parents[1]
CORPUS_BENCHMARK = REPOSITORY / "benchmarks" / "benchmark_corpus.py"


def test_versioned_corpus_is_deterministic_and_uses_ordered_checksums():
    for profile in PROFILE_NAMES:
        records = build_records(profile, 37, seed=251)
        assert records == build_records(profile, 37, seed=251)
        assert len(records) == 37
        assert corpus_fingerprint(records) == corpus_fingerprint(build_records(profile, 37, seed=251))

    records = build_records("high-entropy", 2, seed=251)
    assert records != build_records("high-entropy", 2, seed=252)
    assert corpus_fingerprint(records)[1] != corpus_fingerprint(records[::-1])[1]


def test_exact_oracle_distinguishes_types_and_float_bits():
    assert benchmark_corpus._same_value({"value": True}, {"value": True})
    assert not benchmark_corpus._same_value({"value": True}, {"value": 1})
    assert not benchmark_corpus._same_value({"value": 0.0}, {"value": -0.0})


def test_batch_validation_failure_does_not_change_stream_state():
    stream = StreamingCompressor()
    stream.add_record({"existing": {"value": 7}})

    with pytest.raises(TypeError, match="mappings"):
        stream.add_batch([{"pending": 9}, None])  # type: ignore[list-item]

    assert decompress(stream.finalize()) == [{"existing": {"value": 7}}]


def test_flatten_once_batch_preserves_uniform_and_mixed_record_shapes():
    records = [
        {"nested": {"left": 1}, "literal.dot": "first", "items": [1, {"x": True}]},
        {"nested": {"left": 2}, "literal.dot": "second", "items": [2, {"x": False}]},
        {"nested": {}, "literal.dot": "third"},
        {"nested": {"right": None}, "literal.dot": "fourth", "items": []},
    ]
    stream = StreamingCompressor()
    stream.add_batch(records)

    assert decompress(stream.finalize()) == records


@pytest.mark.parametrize("suffix", [b"junk", zstd.ZstdCompressor().compress(b"second frame")])
def test_single_frame_decompression_rejects_trailing_bytes(suffix: bytes):
    engine = CompressionEngine()
    frame = engine.compress(b"complete frame")

    with pytest.raises(InvalidFormatError, match="trailing frame data"):
        engine.decompress(frame + suffix)


def test_single_pass_decompression_keeps_size_and_frame_error_contracts():
    engine = CompressionEngine()
    body = b"known frame body"
    known = zstd.ZstdCompressor(write_checksum=True).compress(body)

    assert engine.decompress(known, max_output_size=len(body)) == body
    with pytest.raises(ResourceLimitError, match="max_output_size"):
        engine.decompress(known, max_output_size=len(body) - 1)
    with pytest.raises(InvalidFormatError, match="truncated"):
        engine.decompress(known[:-1], max_output_size=len(body))

    unknown = zstd.ZstdCompressor(write_content_size=False, write_checksum=True).compress(body)
    assert zstd.frame_content_size(unknown) < 0
    assert engine.decompress(unknown, max_output_size=len(body)) == body
    with pytest.raises(InvalidFormatError):
        engine.decompress(unknown, max_output_size=len(body) - 1)

    damaged = bytearray(known)
    damaged[-1] ^= 0x01
    with pytest.raises(InvalidFormatError, match="compressed payload"):
        engine.decompress(bytes(damaged), max_output_size=len(body))


def test_comparison_cli_emits_checksum_only_json_without_changing_legacy_benchmark():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPOSITORY), environment.get("PYTHONPATH")) if value
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(CORPUS_BENCHMARK),
            "--profiles",
            "high-entropy",
            "--records",
            "3",
            "--iterations",
            "1",
            "--warmups",
            "0",
            "--skip-rss",
            "--json",
        ],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["corpus"]["version"] == 1
    assert result["corpus"]["profiles"] == ["high-entropy"]
    implementations = result["workloads"]["high-entropy"]["implementations"]
    assert "jzpack" in implementations
    assert "msgpack-zstd" in implementations
    assert implementations["jzpack"]["exact_round_trip_validated"] is True
    record = build_records("high-entropy", 3)[0]
    assert record["digest"] not in completed.stdout
    assert record["payload"] not in completed.stdout
