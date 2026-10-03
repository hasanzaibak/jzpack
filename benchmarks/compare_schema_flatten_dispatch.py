from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.corpus import build_records as build_v1
from benchmarks.corpus import corpus_fingerprint as fingerprint_v1
from benchmarks.corpus_wave3 import build_records as build_wave3
from benchmarks.corpus_wave3 import corpus_fingerprint as fingerprint_wave3
from benchmarks.corpus_wave3 import is_faithful
from jzpack import compress, decompress
from jzpack.schema import SchemaManager

ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "900911fa2060578f9a1dce9c73c0c48dcceae036"
PAIRS = 9
CASES = (
    ("schema-diverse", 10_000, "wave3"),
    ("schema-diverse", 50_000, "wave3"),
    ("nested-records", 10_000, "wave3"),
    ("nested-records", 50_000, "wave3"),
    ("integer-series", 50_000, "v1"),
)
MODES = (False, True)  # public compress(fast=...)

CANDIDATE_FLATTEN = SchemaManager._flatten


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=False).decode().strip()


def changed_paths(*args: str) -> set[str]:
    return {path for path in git(*args).splitlines() if path}


def binary_patch(*args: str, paths: set[str]) -> bytes:
    return subprocess.check_output(
        ["git", "diff", "--binary", *args, "--", *sorted(paths)], cwd=ROOT
    )


def baseline_flatten_from_git() -> Any:
    source = subprocess.check_output(
        ["git", "show", f"{BASE_REVISION}:jzpack/schema.py"], cwd=ROOT
    ).decode("utf-8")
    tree = ast.parse(source)
    manager = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SchemaManager")
    method = next(node for node in manager.body if isinstance(node, ast.FunctionDef) and node.name == "_flatten")
    function = copy.deepcopy(method)
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace: dict[str, Any] = {"Mapping": Mapping, "Path": tuple[str, ...], "Any": Any}
    exec(compile(module, "baseline:jzpack/schema.py:SchemaManager._flatten", "exec"), namespace)
    return namespace["_flatten"]


BASELINE_FLATTEN = baseline_flatten_from_git()


def set_variant(variant: str) -> None:
    SchemaManager._flatten = BASELINE_FLATTEN if variant == "baseline" else CANDIDATE_FLATTEN


def make_records(profile: str, count: int, corpus: str) -> list[dict[str, Any]]:
    return build_v1(profile, count) if corpus == "v1" else build_wave3(profile, count)


def fingerprint(records: list[dict[str, Any]], corpus: str) -> tuple[int, str]:
    return fingerprint_v1(records) if corpus == "v1" else fingerprint_wave3(records)


def candidate_file_hashes() -> dict[str, str]:
    return {
        "jzpack/schema.py": sha256((ROOT / "jzpack/schema.py").read_bytes()),
        "tests/test_schema_contracts.py": sha256((ROOT / "tests/test_schema_contracts.py").read_bytes()),
    }


def main(output: Path, smoke: bool = False) -> None:
    head = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE_REVISION, head],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    committed_changes = changed_paths("diff", "--name-only", f"{BASE_REVISION}...{head}")
    staged_changes = changed_paths("diff", "--cached", "--name-only")
    unstaged_changes = changed_paths("diff", "--name-only")
    untracked_changes = changed_paths("ls-files", "--others", "--exclude-standard")
    changed = committed_changes | staged_changes | unstaged_changes | untracked_changes
    required = {"jzpack/schema.py", "tests/test_schema_contracts.py"}
    if not required.issubset(changed):
        raise SystemExit(f"missing candidate files: {sorted(required - changed)}")

    baseline_schema = subprocess.check_output(
        ["git", "show", f"{BASE_REVISION}:jzpack/schema.py"], cwd=ROOT
    )
    patch_components = {
        "committed_since_base": binary_patch(
            f"{BASE_REVISION}...{head}", paths=required
        ),
        "staged": binary_patch("--cached", paths=required),
        "unstaged": binary_patch(paths=required),
    }
    patch = b"".join(
        name.encode("ascii") + b"\0" + patch_components[name] + b"\0"
        for name in patch_components
    )
    expected_source = {
        "jzpack/schema.py": sha256(baseline_schema),
    }

    results: list[dict[str, Any]] = []
    cases = CASES[:1] if smoke else CASES
    pair_count = 1 if smoke else PAIRS
    for profile, count, corpus in cases:
        records = make_records(profile, count, corpus)
        canonical_bytes, input_sha = fingerprint(records, corpus)
        for fast in MODES:
            archives: dict[str, bytes] = {}
            # Warm and verify both variants outside all timed samples.
            for variant in ("baseline", "candidate"):
                set_variant(variant)
                archive = compress(records, level=3, fast=fast)
                restored = decompress(archive)
                if len(restored) != len(records) or not all(is_faithful(x, y) for x, y in zip(records, restored)):
                    raise AssertionError(f"round-trip fidelity failed in warmup: {profile}/{count}/{variant}/fast={fast}")
                archives[variant] = archive
            if archives["baseline"] != archives["candidate"]:
                raise AssertionError(f"warmup archive bytes differ: {profile}/{count}/fast={fast}")

            samples: list[dict[str, Any]] = []
            for pair in range(pair_count):
                order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                pair_results: dict[str, int] = {}
                for variant in order:
                    set_variant(variant)
                    started = time.perf_counter_ns()
                    archive = compress(records, level=3, fast=fast)
                    elapsed = time.perf_counter_ns() - started
                    if archive != archives[variant]:
                        raise AssertionError(f"archive changed between runs: {profile}/{count}/{variant}/fast={fast}")
                    pair_results[variant] = elapsed
                if archives["baseline"] != archives["candidate"]:
                    raise AssertionError(f"archive bytes differ: {profile}/{count}/fast={fast}")
                samples.append(
                    {
                        "pair": pair + 1,
                        "order": list(order),
                        "baseline_ns": pair_results["baseline"],
                        "candidate_ns": pair_results["candidate"],
                        "baseline_over_candidate": pair_results["baseline"] / pair_results["candidate"],
                    }
                )

            baseline_values = [sample["baseline_ns"] for sample in samples]
            candidate_values = [sample["candidate_ns"] for sample in samples]
            pair_ratios = [sample["baseline_over_candidate"] for sample in samples]
            results.append(
                {
                    "profile": profile,
                    "count": count,
                    "corpus": corpus,
                    "fast": fast,
                    "input_canonical_bytes": canonical_bytes,
                    "input_fingerprint_sha256": input_sha,
                    "archive_bytes": len(archives["baseline"]),
                    "archive_sha256": sha256(archives["baseline"]),
                    "baseline_candidate_archives_identical": True,
                    "round_trip_fidelity": "passed outside timer with corpus is_faithful oracle (exact types, recursive values, float bits; corpus has no float values)",
                    "pair_count": pair_count,
                    "raw_pairs": samples,
                    "baseline_median_ms": statistics.median(baseline_values) / 1_000_000,
                    "candidate_median_ms": statistics.median(candidate_values) / 1_000_000,
                    "paired_speedup_median": statistics.median(pair_ratios),
                    "baseline_range_ms": [min(baseline_values) / 1_000_000, max(baseline_values) / 1_000_000],
                    "candidate_range_ms": [min(candidate_values) / 1_000_000, max(candidate_values) / 1_000_000],
                    "faster_candidate_pairs": sum(sample["candidate_ns"] < sample["baseline_ns"] for sample in samples),
                }
            )

    set_variant("candidate")
    harness_sha = sha256(Path(__file__).resolve().read_bytes())
    report = {
        "title": "SchemaManager._flatten built-in type dispatch candidate benchmark",
        "base_revision": BASE_REVISION,
        "candidate_head": head,
        "branch": branch,
        "smoke_mode": smoke,
        "worktree_changes": sorted(changed),
        "change_sets": {
            "committed_since_base": sorted(committed_changes),
            "staged": sorted(staged_changes),
            "unstaged": sorted(unstaged_changes),
            "untracked": sorted(untracked_changes),
        },
        "baseline_schema_sha256": expected_source["jzpack/schema.py"],
        "candidate_source_sha256": candidate_file_hashes(),
        "binary_patch_sha256": sha256(patch),
        "binary_patch_component_sha256": {
            name: sha256(component) for name, component in patch_components.items()
        },
        "harness_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "capture_harness_sha256": harness_sha,
        "replay_harness_sha256": harness_sha,
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "msgpack": importlib.metadata.version("msgpack"),
            "zstandard": importlib.metadata.version("zstandard"),
        },
        "method": {
            "operation": "public jzpack.compress(records, level=3, fast=False|True)",
            "timer": "perf_counter_ns around only public compress call; no cProfile or tracemalloc",
            "inputs": "deterministic fixed corpus prebuilt/fingerprinted before timer",
            "warmups": "one per variant, case, and fast mode; output validation outside timer",
            "paired_repetitions": pair_count,
            "pair_order": "alternating baseline/candidate order per pair",
            "fidelity_and_byte_validation": "archive equality checked after timer for each call; round trip/corpus oracle validated before timed pairs; warmup archives must be byte-identical",
            "baseline_method": "AST-loaded exact SchemaManager._flatten method from the baseline Git commit; only that method differs between variants",
        },
        "cases": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "report": str(output),
        "report_sha256": sha256(output.read_bytes()),
        "baseline": BASE_REVISION,
        "candidate_head": head,
        "capture_harness_sha256": report["capture_harness_sha256"],
        "replay_harness_sha256": report["replay_harness_sha256"],
        "patch_sha256": report["binary_patch_sha256"],
        "case_mode_count": len(results),
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    main(arguments.output, smoke=arguments.smoke)
