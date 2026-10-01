"""Run narrow codec-fidelity mutations in disposable repository copies.

The checked-out package is only read. Each deliberate defect is written to a
temporary copy, then pytest runs against that copy with its path taking
precedence over any installed jzpack distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import msgpack

REPOSITORY = Path(__file__).resolve().parents[1]
CODEC_FILE = Path("jzpack/encoders.py")
SNAPSHOT_FILES = (
    "jzpack/analyzer.py",
    "jzpack/encoders.py",
    "tests/fidelity_oracle.py",
    "tests/test_codec_fidelity.py",
    "tests/test_codec_performance_contracts.py",
    "tests/test_fidelity_mutation_harness.py",
    "tools/verify_fidelity_mutations.py",
)
IGNORE_COPY = shutil.ignore_patterns(
    ".git",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "*.egg-info",
)


def _replace_exact(source: str, old: str, new: str, expected: int = 1) -> str:
    matches = source.count(old)
    if matches != expected:
        raise ValueError(f"mutation anchor matched {matches} times; expected {expected}: {old!r}")
    return source.replace(old, new, expected)


def _mutate_float_bit_comparison(source: str) -> str:
    return _replace_exact(
        source,
        'return struct.pack(">d", left) == struct.pack(">d", right)',
        "return left == right",
        expected=2,
    )


def _mutate_scalar_type_guard(source: str) -> str:
    source = _replace_exact(
        source,
        "if type(left) is not type(right) or not RLEEncoder.supports_value(left):\n            return False",
        "if not RLEEncoder.supports_value(left):\n            return False",
    )
    return _replace_exact(
        source,
        "        if type(left) is not type(right):\n            return False\n",
        "",
    )


def _replace_in_method(source: str, method: str, old: str, new: str) -> str:
    class_start = source.index("class DeltaEncoder:")
    class_end = source.index("\n\nclass DictionaryEncoder:", class_start)
    class_source = source[class_start:class_end]
    method_start = class_source.index(f"    def {method}(")
    next_method = class_source.find("\n    @staticmethod", method_start)
    method_end = len(class_source) if next_method < 0 else next_method
    method_source = class_source[method_start:method_end]
    method_source = _replace_exact(method_source, old, new)
    return source[:class_start] + class_source[:method_start] + method_source + class_source[method_end:] + source[class_end:]


def _mutate_unsafe_float_delta(source: str) -> str:
    replacements = {
        "can_encode": (
            ("if not _is_supported_integer(previous):", "if not _is_supported_number(previous):"),
            ("if not _is_supported_integer(value):", "if not _is_supported_number(value):"),
            ("if not _is_supported_integer(delta):", "if not _is_supported_number(delta):"),
        ),
        "encode": (
            ("if not _is_supported_integer(base):", "if not _is_supported_number(base):"),
            ("if not _is_supported_integer(value):", "if not _is_supported_number(value):"),
            ("if not _is_supported_integer(delta):", "if not _is_supported_number(delta):"),
        ),
    }
    for method, guards in replacements.items():
        for old, new in guards:
            source = _replace_in_method(source, method, old, new)
    return source


@dataclass(frozen=True)
class Mutation:
    name: str
    description: str
    unit_test: str
    public_test: str
    apply: Callable[[str], str]


MUTATIONS = (
    Mutation(
        name="ordinary-float-equality",
        description="Replace binary64-bit equality with Python float equality in both RLE comparisons.",
        unit_test=(
            "tests/test_codec_fidelity.py::test_rle_preserves_exact_type_and_float_bits["
            "signed-zero-distinction]"
        ),
        public_test=(
            "tests/test_codec_fidelity.py::test_default_codec_preserves_reproduced_values["
            "signed-zero-rle]"
        ),
        apply=_mutate_float_bit_comparison,
    ),
    Mutation(
        name="bypass-scalar-type-guard",
        description="Allow Python-equal scalar values of different exact types to merge in RLE.",
        unit_test=(
            "tests/test_codec_fidelity.py::test_rle_preserves_exact_type_and_float_bits["
            "bool-int-distinction]"
        ),
        public_test=(
            "tests/test_codec_fidelity.py::test_default_codec_preserves_reproduced_values["
            "bool-and-int-rle]"
        ),
        apply=_mutate_scalar_type_guard,
    ),
    Mutation(
        name="unsafe-floating-delta",
        description="Permit float values and deltas in the DELTA writer, reproducing cancellation loss.",
        unit_test=(
            "tests/test_codec_performance_contracts.py::test_direct_delta_encoder_still_rejects_malformed_inputs["
            "unsafe-float-cancellation]"
        ),
        public_test=(
            "tests/test_codec_fidelity.py::test_default_codec_preserves_reproduced_values["
            "float-cancellation]"
        ),
        apply=_mutate_unsafe_float_delta,
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for relative_path in SNAPSHOT_FILES:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_repository(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("temporary copy destination must be outside the source repository")
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination, ignore=IGNORE_COPY)


def make_mutated_copy(source: Path, destination: Path, mutation: Mutation) -> str:
    copy_repository(source, destination)
    target = destination / CODEC_FILE
    original = target.read_text(encoding="utf-8")
    mutated = mutation.apply(original)
    if mutated == original:
        raise ValueError(f"mutation {mutation.name!r} did not change its temporary copy")
    target.write_text(mutated, encoding="utf-8")
    return _sha256(target.read_bytes())


def failed_nodeids(output: str) -> list[str]:
    nodeids = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            nodeids.append(line[len("FAILED ") :].partition(" - ")[0])
    return nodeids


def _pytest(repository: Path, nodeids: tuple[str, ...]) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *nodeids],
        cwd=repository,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )
    output = completed.stdout
    summary = next((line for line in reversed(output.splitlines()) if line.strip()), "")
    return {
        "returncode": completed.returncode,
        "failed_nodeids": failed_nodeids(output),
        "summary": summary,
    }


def _git_head(repository: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run_verification(repository: Path = REPOSITORY) -> dict[str, object]:
    repository = repository.resolve()
    unit_nodeids = tuple(mutation.unit_test for mutation in MUTATIONS)
    public_nodeids = tuple(mutation.public_test for mutation in MUTATIONS)
    baseline_nodeids = unit_nodeids + public_nodeids
    source_hash = _sha256((repository / CODEC_FILE).read_bytes())
    try:
        runtime_versions = {
            "python": sys.version.split()[0],
            "msgpack": importlib.metadata.version("msgpack"),
            "msgpack_backend": msgpack.Packer.__module__,
            "zstandard": importlib.metadata.version("zstandard"),
        }
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("pytest, msgpack, and zstandard must be installed in this Python environment") from exc

    report: dict[str, object] = {
        "repository_commit": _git_head(repository),
        "source_snapshot_sha256": _snapshot_hash(repository),
        "baseline_source_sha256": source_hash,
        "runtime": runtime_versions,
        "baseline_nodeids": baseline_nodeids,
        "baseline": None,
        "mutations": [],
        "passed": False,
    }

    with tempfile.TemporaryDirectory(prefix="jzpack-fidelity-mutation-review-") as temporary:
        temporary_root = Path(temporary)
        baseline_copy = temporary_root / "baseline"
        copy_repository(repository, baseline_copy)
        baseline = _pytest(baseline_copy, baseline_nodeids)
        report["baseline"] = baseline
        if baseline["returncode"] != 0:
            report["baseline_failure"] = "targeted baseline tests must pass before mutants are evaluated"
            return report

        results = []
        for mutation in MUTATIONS:
            mutated_copy = temporary_root / mutation.name
            try:
                mutated_sha256 = make_mutated_copy(baseline_copy, mutated_copy, mutation)
            except (OSError, ValueError) as exc:
                results.append({"name": mutation.name, "status": "mutation-error", "error": str(exc)})
                continue

            target_nodeids = (mutation.unit_test, mutation.public_test)
            outcome = _pytest(mutated_copy, target_nodeids)
            failures = set(outcome["failed_nodeids"])
            killed_by = [nodeid for nodeid in target_nodeids if nodeid in failures]
            unexpected_failures = sorted(failures.difference(target_nodeids))
            killed = (
                outcome["returncode"] != 0
                and len(killed_by) == 2
                and not unexpected_failures
            )
            results.append(
                {
                    "name": mutation.name,
                    "description": mutation.description,
                    "mutated_source_sha256": mutated_sha256,
                    "unit_test": mutation.unit_test,
                    "public_container_test": mutation.public_test,
                    "killed_by": killed_by,
                    "unexpected_failures": unexpected_failures,
                    "pytest_summary": outcome["summary"],
                    "status": "killed-by-both" if killed else "survived-or-partial",
                }
            )
        report["mutations"] = results
        report["passed"] = len(results) == len(MUTATIONS) and all(
            result.get("status") == "killed-by-both" for result in results
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=REPOSITORY,
        help="repository to snapshot and test (default: this checkout)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="also write the JSON report to this path, relative to the repository unless absolute",
    )
    arguments = parser.parse_args()
    report = run_verification(arguments.repository)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if arguments.json_out is not None:
        output_path = arguments.json_out
        if not output_path.is_absolute():
            output_path = arguments.repository / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
