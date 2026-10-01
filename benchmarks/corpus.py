"""Versioned deterministic JSON-event workloads for comparative benchmarks."""

from __future__ import annotations

import hashlib
import json
import string
from typing import Any

CORPUS_VERSION = 1
DEFAULT_SEED = 1729
PROFILE_NAMES = (
    "mixed-events",
    "optional-fields",
    "high-entropy",
    "nested-arrays",
    "integer-series",
)
REFERENCE_INPUT_SIZE_DEFINITION = (
    "sum of UTF-8 bytes in canonical JSON records (sorted keys, compact separators, and no newline)"
)


class _SplitMix64:
    """Tiny stable PRNG so corpus contents do not depend on Python's random module."""

    _MASK = (1 << 64) - 1
    _GAMMA = 0x9E3779B97F4A7C15

    def __init__(self, seed: int):
        self._state = seed & self._MASK

    def next(self) -> int:
        self._state = (self._state + self._GAMMA) & self._MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & self._MASK
        return value ^ (value >> 31)


def build_records(profile: str, count: int, seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Build a deterministic supported-value workload without reading external data."""
    if profile not in PROFILE_NAMES:
        raise ValueError(f"unknown benchmark profile: {profile}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("record count must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    if profile == "mixed-events":
        return _mixed_events(count)
    if profile == "optional-fields":
        return _optional_fields(count, seed)
    if profile == "high-entropy":
        return _high_entropy(count, seed)
    if profile == "nested-arrays":
        return _nested_arrays(count)
    return _integer_series(count)


def canonical_json_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def corpus_fingerprint(records: list[dict[str, Any]]) -> tuple[int, str]:
    """Return canonical reference size and an order-sensitive, newline-delimited SHA-256."""
    digest = hashlib.sha256()
    byte_count = 0
    for record in records:
        encoded = canonical_json_bytes(record)
        byte_count += len(encoded)
        digest.update(encoded)
        digest.update(b"\n")
    return byte_count, digest.hexdigest()


def _mixed_events(count: int) -> list[dict[str, Any]]:
    http_methods = ("GET", "POST", "PUT", "DELETE")
    db_operations = ("select", "insert", "update", "delete")
    queue_names = ("email", "billing", "events", "media")
    job_names = ("refresh-index", "daily-rollup", "expire-sessions", "sync-catalog")
    records = []
    for index in range(count):
        event_type = index % 4
        common = {"event_id": index, "timestamp_ns": 1_700_000_000_000_000_000 + index * 1_000_000}
        if event_type == 0:
            records.append(
                {
                    **common,
                    "kind": "http",
                    "request": {"method": http_methods[(index // 4) % len(http_methods)], "route": f"/v{index % 8}/items"},
                    "status_code": 200 + index % 5,
                    "duration_ms": index % 211,
                }
            )
        elif event_type == 1:
            records.append(
                {
                    **common,
                    "kind": "database",
                    "query": {"operation": db_operations[(index // 4) % len(db_operations)], "shard": index % 16},
                    "rows_affected": index % 37,
                    "committed": index % 9 != 0,
                }
            )
        elif event_type == 2:
            records.append(
                {
                    **common,
                    "kind": "queue",
                    "queue": queue_names[(index // 4) % len(queue_names)],
                    "message_count": index % 23,
                    "delay_ms": index % 97,
                }
            )
        else:
            records.append(
                {
                    **common,
                    "kind": "job",
                    "job": {"name": job_names[(index // 4) % len(job_names)], "attempt": index % 6},
                    "succeeded": index % 7 != 0,
                }
            )
    return records


def _optional_fields(count: int, seed: int) -> list[dict[str, Any]]:
    rng = _SplitMix64(seed)
    records = []
    for index in range(count):
        record: dict[str, Any] = {"record_id": index, "category": f"segment-{index % 16}"}
        for field_number in range(12):
            # Each field is sparse, with deterministic per-seed placement.
            if rng.next() % 8 == 0:
                record[f"optional_{field_number:02d}"] = (index + field_number * 13) % 10_007
        records.append(record)
    return records


def _high_entropy(count: int, seed: int) -> list[dict[str, Any]]:
    alphabet = string.ascii_letters + string.digits + "-_"
    rng = _SplitMix64(seed)
    records = []
    for index in range(count):
        token = "".join(alphabet[rng.next() % len(alphabet)] for _ in range(64))
        records.append({"record_id": index, "digest": token, "payload": token[::-1]})
    return records


def _nested_arrays(count: int) -> list[dict[str, Any]]:
    records = []
    for index in range(count):
        items = [
            {"sku": f"item-{(index + offset) % 24:02d}", "quantity": (index + offset) % 5 + 1}
            for offset in range(index % 3 + 2)
        ]
        records.append(
            {
                "record_id": index,
                "account": {"id": index % 2048, "tier": ("free", "standard", "pro")[index % 3]},
                "batches": [
                    [item["quantity"] for item in items[:2]],
                    [item["quantity"] for item in items[2:]],
                ],
                "items": items,
            }
        )
    return records


def _integer_series(count: int) -> list[dict[str, Any]]:
    start_ns = 1_700_000_000_000_000_000
    return [
        {
            "sequence": index,
            "timestamp_ns": start_ns + index * 1_000,
            "counter": index * 7,
            "device": f"sensor-{index % 32:02d}",
        }
        for index in range(count)
    ]
