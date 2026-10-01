"""Deterministic larger synthetic workloads for the wave-3 comparison harness.

This is a separate corpus version; corpus v1 and its fingerprints are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

CORPUS_VERSION = 2
DEFAULT_SEED = 20_261_001
PROFILE_NAMES = ("schema-diverse", "nested-records", "large-strings")
DEFAULT_LARGE_STRING_BYTES = 16_384
MAX_LARGE_STRING_BYTES = 250_000


def iter_records(
    profile: str,
    count: int,
    seed: int = DEFAULT_SEED,
    *,
    large_string_bytes: int = DEFAULT_LARGE_STRING_BYTES,
) -> Iterator[dict[str, Any]]:
    """Yield deterministic records without retaining the corpus."""
    _validate_arguments(profile, count, seed, large_string_bytes)
    if profile == "schema-diverse":
        yield from _schema_diverse(count, seed)
    elif profile == "nested-records":
        yield from _nested_records(count)
    else:
        yield from _large_strings(count, seed, large_string_bytes)


def build_records(
    profile: str,
    count: int,
    seed: int = DEFAULT_SEED,
    *,
    large_string_bytes: int = DEFAULT_LARGE_STRING_BYTES,
) -> list[dict[str, Any]]:
    """Build a corpus in memory for the row-oriented comparison modes."""
    return list(iter_records(profile, count, seed, large_string_bytes=large_string_bytes))


def canonical_json_bytes(record: Mapping[str, Any]) -> bytes:
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def corpus_fingerprint(records: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    """Return canonical input bytes and an ordered newline-delimited SHA-256."""
    digest = hashlib.sha256()
    byte_count = 0
    for record in records:
        encoded = canonical_json_bytes(record)
        byte_count += len(encoded)
        digest.update(encoded)
        digest.update(b"\n")
    return byte_count, digest.hexdigest()


def is_faithful(expected: Any, actual: Any) -> bool:
    """Compare exact built-in value types, structure, and binary64 bits."""
    if type(expected) is not type(actual):
        return False
    if type(expected) is float:
        return struct.pack(">d", expected) == struct.pack(">d", actual)
    if type(expected) is dict:
        return expected.keys() == actual.keys() and all(
            is_faithful(expected[key], actual[key]) for key in expected
        )
    if type(expected) is list:
        return len(expected) == len(actual) and all(
            is_faithful(left, right) for left, right in zip(expected, actual)
        )
    return expected == actual


def first_difference(expected: Any, actual: Any, path: str = "$record") -> str | None:
    """Describe the first mismatch without including record values."""
    if type(expected) is not type(actual):
        return f"{path}: type {type(expected).__name__} != {type(actual).__name__}"
    if type(expected) is float:
        if struct.pack(">d", expected) != struct.pack(">d", actual):
            return f"{path}: float64 bits differ"
        return None
    if type(expected) is dict:
        expected_keys = expected.keys()
        actual_keys = actual.keys()
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        if missing:
            key = sorted(missing)[0]
            return f"{path}.{key}: key is missing"
        if extra:
            key = sorted(extra)[0]
            return f"{path}.{key}: unexpected key"
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if type(expected) is list:
        if len(expected) != len(actual):
            return f"{path}: list length {len(expected)} != {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path}: scalar values differ"
    return None


def records_difference(expected: Iterable[Any], actual: Iterable[Any]) -> str | None:
    """Compare two record streams completely, including their lengths."""
    sentinel = object()
    left = iter(expected)
    right = iter(actual)
    index = 0
    while True:
        expected_record = next(left, sentinel)
        actual_record = next(right, sentinel)
        if expected_record is sentinel and actual_record is sentinel:
            return None
        if expected_record is sentinel:
            return f"record count: extra output at index {index}"
        if actual_record is sentinel:
            return f"record count: missing output at index {index}"
        difference = first_difference(expected_record, actual_record, f"$[{index}]")
        if difference is not None:
            return difference
        index += 1


def _schema_diverse(count: int, seed: int) -> Iterator[dict[str, Any]]:
    for index in range(count):
        record: dict[str, Any] = {
            "event_id": index,
            "tenant": f"tenant-{index % 64:02d}",
            "score": -0.0 if index % 2 else 0.0,
        }
        for field in range(12):
            draw = _mix64(seed ^ (index * 0x9E3779B97F4A7C15) ^ (field * 0xD1B54A32D192ED03))
            state = draw % 13
            if state == 0:
                continue
            name = f"optional_{field:02d}"
            if state == 1:
                record[name] = None
            elif field % 4 == 0:
                record[name] = (index * 17 + field) % 1_000_003
            elif field % 4 == 1:
                record[name] = f"group-{(index + field) % 97:02d}"
            elif field % 4 == 2:
                record[name] = (index + field) % 3 == 0
            else:
                record[name] = float((index + field) % 257) / 16.0
        yield record

def _nested_records(count: int) -> Iterator[dict[str, Any]]:
    regions = ("north", "south", "east", "west")
    tiers = ("free", "standard", "pro")
    for index in range(count):
        spans = []
        for offset in range(index % 5 + 1):
            duration = float((index * 13 + offset * 7) % 10_000) / 10.0
            spans.append(
                {
                    "span_id": index * 5 + offset,
                    "duration_ms": duration,
                    "attributes": {
                        "cache_hit": (index + offset) % 2 == 0,
                        "region": regions[(index + offset) % len(regions)],
                    },
                    "samples": [duration, duration / 2.0, -0.0 if (index + offset) % 2 else 0.0],
                }
            )
        yield {
            "event_id": index,
            "account": {
                "account_id": index % 8192,
                "tier": tiers[index % len(tiers)],
                "enabled": index % 11 != 0,
            },
            "trace": {"spans": spans, "sampled": index % 4 == 0},
            "labels": [f"label-{(index + offset) % 23:02d}" for offset in range(index % 4)],
        }


def _large_strings(count: int, seed: int, payload_bytes: int) -> Iterator[dict[str, Any]]:
    for index in range(count):
        raw_bytes = hashlib.shake_256(f"jzpack-wave3:{seed}:{index}".encode("ascii")).digest(
            (payload_bytes + 1) // 2
        )
        payload = raw_bytes.hex()[:payload_bytes]
        yield {
            "record_id": index,
            "category": f"segment-{index % 32:02d}",
            "checksum": hashlib.sha256(payload.encode("ascii")).hexdigest(),
            "payload": payload,
            "score": -0.0 if index % 2 else 0.0,
        }


def _validate_arguments(profile: str, count: int, seed: int, large_string_bytes: int) -> None:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"unknown wave-3 corpus profile: {profile}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("record count must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if (
        isinstance(large_string_bytes, bool)
        or not isinstance(large_string_bytes, int)
        or not 1 <= large_string_bytes <= MAX_LARGE_STRING_BYTES
    ):
        raise ValueError(f"large string size must be between 1 and {MAX_LARGE_STRING_BYTES} bytes")


def _mix64(value: int) -> int:
    mask = (1 << 64) - 1
    value = (value + 0x9E3779B97F4A7C15) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return value ^ (value >> 31)
