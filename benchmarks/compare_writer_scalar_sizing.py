"""Compare the pinned writer baseline with the current scalar-sizing candidate.

Run from a candidate checkout with:

    python benchmarks/compare_writer_scalar_sizing.py --samples 9

Each measurement runs in a fresh Python process. Corpus generation, warmup,
round-trip validation, and archive hashing are outside the timed write call.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

BASE_COMMIT = "3ac252ff6d7641c7e07f0bebff9177381ff81d9a"
CASES = {
    "wave3-schema-diverse-50k": ("wave3", "schema-diverse", 50_000, 20_261_001, None),
    "wave3-nested-records-40k": ("wave3", "nested-records", 40_000, 20_261_001, None),
    "wave1-integer-series-50k": ("wave1", "integer-series", 50_000, 1729, None),
    "wave3-large-strings-512x16k": ("wave3", "large-strings", 512, 20_261_001, 16_384),
}
CORPUS_FILES = ("benchmarks/corpus.py", "benchmarks/corpus_wave3.py")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _file_hash(path: Path) -> str:
    return _sha256(path.read_bytes())


def _package_versions() -> dict[str, str]:
    from jzpack import __version__

    return {
        "jzpack": __version__,
        "msgpack": importlib.metadata.version("msgpack"),
        "zstandard": importlib.metadata.version("zstandard"),
    }


def _sample(repo: Path, commit: str, case_id: str) -> dict[str, Any]:
    sys.path.insert(0, str(repo))
    from benchmarks import corpus as corpus_v1
    from benchmarks import corpus_wave3 as corpus_v3
    from jzpack import iter_decompress, write_records

    source, profile, count, seed, large_bytes = CASES[case_id]
    if source == "wave3":
        kwargs = {} if large_bytes is None else {"large_string_bytes": large_bytes}
        records = corpus_v3.build_records(profile, count, seed, **kwargs)
        input_bytes, input_hash = corpus_v3.corpus_fingerprint(records)
        oracle = corpus_v3.records_difference
    else:
        records = corpus_v1.build_records(profile, count, seed)
        input_bytes, input_hash = corpus_v1.corpus_fingerprint(records)
        oracle = corpus_v3.records_difference

    def encode() -> bytes:
        sink = io.BytesIO()
        written = write_records(records, sink, compression_level=3)
        if written != sink.tell():
            raise RuntimeError("writer byte count differs from sink length")
        return sink.getvalue()

    warm_archive = encode()
    started = time.perf_counter_ns()
    archive = encode()
    elapsed_ns = time.perf_counter_ns() - started
    if warm_archive != archive:
        raise RuntimeError("warmup and timed archive bytes differ")
    restored = list(iter_decompress(io.BytesIO(archive)))
    difference = oracle(records, restored)
    if difference is not None:
        raise RuntimeError(f"exact round-trip failed: {difference}")

    try:
        import resource

        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        peak_rss = None
    return {
        "case": case_id,
        "commit": commit,
        "writer_sha256": _file_hash(repo / "jzpack/writer.py"),
        "corpus_sha256": {name: _file_hash(repo / name) for name in CORPUS_FILES},
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "packages": _package_versions(),
        },
        "input_bytes": input_bytes,
        "input_sha256": input_hash,
        "archive_bytes": len(archive),
        "archive_sha256": _sha256(archive),
        "elapsed_ns": elapsed_ns,
        "peak_rss_raw": peak_rss,
        "exact_round_trip": True,
    }


def _run_child(script: Path, repo: Path, commit: str, case_id: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    result = subprocess.run(
        [sys.executable, str(script), "--_sample", str(repo), commit, case_id],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"sample process failed for {case_id}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _baseline_checkout(repo: Path, commit: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", commit, "jzpack", "benchmarks"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"unsafe path in baseline Git archive: {member.name!r}")
            target = (destination / member_path).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"unsafe path in baseline Git archive: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError(f"could not read baseline archive member: {member.name!r}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise RuntimeError(f"unexpected baseline archive member: {member.name!r}")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    candidate_commit = _git(repo, "rev-parse", "HEAD")
    changed_runtime = _git(
        repo,
        "diff",
        "--name-only",
        f"{args.base}..{candidate_commit}",
        "--",
        "jzpack",
    ).splitlines()
    if changed_runtime != ["jzpack/writer.py"]:
        raise RuntimeError(
            "expected jzpack/writer.py to be the only runtime source change; "
            f"found {changed_runtime!r}"
        )
    dirty_runtime = _git(repo, "status", "--porcelain=v1", "--", "jzpack").splitlines()
    if dirty_runtime:
        raise RuntimeError(f"candidate runtime source is not clean: {dirty_runtime!r}")

    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="jzpack-writer-baseline-") as temp:
        baseline_repo = Path(temp)
        _baseline_checkout(repo, args.base, baseline_repo)
        baseline_corpus = {name: _file_hash(baseline_repo / name) for name in CORPUS_FILES}
        candidate_corpus = {name: _file_hash(repo / name) for name in CORPUS_FILES}
        if baseline_corpus != candidate_corpus:
            raise RuntimeError("benchmark corpus sources differ between baseline and candidate")

        report: dict[str, Any] = {
            "format": "jzpack-writer-scalar-sizing-paired-v1",
            "baseline_commit": args.base,
            "candidate_commit": candidate_commit,
            "changed_runtime_files": changed_runtime,
            "writer_sha256": {
                "baseline": _file_hash(baseline_repo / "jzpack/writer.py"),
                "candidate": _file_hash(repo / "jzpack/writer.py"),
            },
            "harness_sha256": _file_hash(script),
            "corpus_sha256": candidate_corpus,
            "method": {
                "paired_samples_per_case": args.samples,
                "pair_order": "alternating baseline-candidate and candidate-baseline",
                "fresh_process_per_sample": True,
                "warmups_per_sample": 1,
                "timed_region": "public write_records call to BytesIO",
                "exact_round_trip_and_archive_hash": "verified outside timer for every sample",
                "peak_rss": "per-process ru_maxrss; includes imports, corpus, warmup, write, and oracle",
            },
            "cases": {},
        }

        for case_id in CASES:
            pairs = []
            for index in range(args.samples):
                order = (
                    [(baseline_repo, args.base, "baseline"), (repo, candidate_commit, "candidate")]
                    if index % 2 == 0
                    else [(repo, candidate_commit, "candidate"), (baseline_repo, args.base, "baseline")]
                )
                samples = {
                    label: _run_child(script, checkout, commit, case_id)
                    for checkout, commit, label in order
                }
                baseline = samples["baseline"]
                candidate = samples["candidate"]
                for field in ("runtime", "input_bytes", "input_sha256", "archive_bytes", "archive_sha256"):
                    if baseline[field] != candidate[field]:
                        raise RuntimeError(f"{case_id} pair {index + 1}: {field} differs")
                baseline_seconds = baseline["elapsed_ns"] / 1_000_000_000
                candidate_seconds = candidate["elapsed_ns"] / 1_000_000_000
                pairs.append(
                    {
                        "pair": index + 1,
                        "baseline_seconds": baseline_seconds,
                        "candidate_seconds": candidate_seconds,
                        "candidate_speedup_percent": 100.0
                        * (baseline_seconds - candidate_seconds)
                        / baseline_seconds,
                        "peak_rss_raw": {
                            "baseline": baseline["peak_rss_raw"],
                            "candidate": candidate["peak_rss_raw"],
                        },
                        "input_bytes": baseline["input_bytes"],
                        "input_sha256": baseline["input_sha256"],
                        "archive_bytes": baseline["archive_bytes"],
                        "archive_sha256": baseline["archive_sha256"],
                    }
                )
            speedups = [pair["candidate_speedup_percent"] for pair in pairs]
            report["cases"][case_id] = {
                "pairs": pairs,
                "paired_median_speedup_percent": statistics.median(speedups),
                "candidate_wins": sum(value > 0 for value in speedups),
                "sample_count": len(pairs),
                "archive_bytes": pairs[0]["archive_bytes"],
                "archive_sha256": pairs[0]["archive_sha256"],
                "input_bytes": pairs[0]["input_bytes"],
                "input_sha256": pairs[0]["input_sha256"],
            }
            print(json.dumps({"case": case_id, "summary": report["cases"][case_id]}, sort_keys=True), flush=True)

    report["environment"] = samples["baseline"]["runtime"]
    report["candidate_worktree_status"] = _git(repo, "status", "--porcelain=v1").splitlines()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"ARTIFACT={args.output}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE_COMMIT, help="pinned baseline git commit")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--samples", type=int, default=9, help="paired samples per case (1-25)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--_sample", nargs=3, metavar=("REPO", "COMMIT", "CASE"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._sample:
        repo_name, commit, case_id = args._sample
        if case_id not in CASES:
            parser.error(f"unknown case: {case_id}")
        print(json.dumps(_sample(Path(repo_name), commit, case_id), sort_keys=True))
        return
    if not 1 <= args.samples <= 25:
        parser.error("--samples must be between 1 and 25")
    if args.output is None:
        args.output = args.repo.resolve() / "benchmarks/results/writer-scalar-sizing-paired.json"
    _run(args)


if __name__ == "__main__":
    main()
