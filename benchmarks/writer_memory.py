"""Isolated process-RSS and Python-allocation probes for the bounded writer."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tracemalloc
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None  # type: ignore[assignment]

import zstandard

from jzpack import __version__, write_records


class _DiscardSink:
    """Accept complete buffers without retaining compressed output."""

    def __init__(self) -> None:
        self.bytes_written = 0

    def write(self, data: memoryview) -> int:
        size = len(data)
        self.bytes_written += size
        return size


def _records(count: int):
    for index in range(count):
        yield {
            "sequence": index,
            "event": f"event-{index % 8}",
            "metadata": {"active": index % 2 == 0, "partition": index % 64},
        }


def _rss_peak_bytes() -> int | None:
    if resource is None:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _write(count: int) -> int:
    sink = _DiscardSink()
    archive_bytes = write_records(_records(count), sink)
    if archive_bytes != sink.bytes_written:
        raise RuntimeError("discard sink byte count disagrees with writer result")
    return archive_bytes


def _worker(rows: int, warmup_rows: int) -> dict[str, int | None]:
    _write(warmup_rows)
    rss_before = _rss_peak_bytes()
    tracemalloc.start()
    tracemalloc.reset_peak()
    archive_bytes = _write(rows)
    _, python_peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _rss_peak_bytes()
    rss_delta = None if rss_before is None or rss_after is None else max(0, rss_after - rss_before)
    return {
        "rows": rows,
        "warmup_rows": warmup_rows,
        "archive_bytes_discarded": archive_bytes,
        "python_tracemalloc_peak_bytes": python_peak_bytes,
        "process_rss_peak_bytes_including_startup_and_warmup": rss_after,
        "process_rss_peak_delta_after_warmup_bytes": rss_delta,
        "process_rss_peak_before_measurement_bytes": rss_before,
    }


def _run_sample(rows: int, warmup_rows: int) -> dict[str, int | None]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.writer_memory",
        "--worker",
        "--rows",
        str(rows),
        "--warmup-rows",
        str(warmup_rows),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _summarize(values: list[int | None]) -> int | None:
    defined = [value for value in values if value is not None]
    return int(statistics.median(defined)) if defined else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[25_000, 100_000, 400_000])
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--warmup-rows", type=int, default=1_000)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if any(rows <= 0 for rows in args.rows):
        parser.error("each row count must be positive")
    if args.samples <= 0:
        parser.error("--samples must be positive")
    if args.warmup_rows < 0:
        parser.error("--warmup-rows must be non-negative")
    return args


def main() -> None:
    args = _parse_args()
    if args.worker:
        print(json.dumps(_worker(args.rows[0], args.warmup_rows), sort_keys=True))
        return

    rows_results: list[dict[str, Any]] = []
    for rows in args.rows:
        samples = [_run_sample(rows, args.warmup_rows) for _ in range(args.samples)]
        rows_results.append(
            {
                "rows": rows,
                "samples": samples,
                "median_archive_bytes_discarded": _summarize(
                    [sample["archive_bytes_discarded"] for sample in samples]
                ),
                "median_python_tracemalloc_peak_bytes": _summarize(
                    [sample["python_tracemalloc_peak_bytes"] for sample in samples]
                ),
                "median_process_rss_peak_bytes_including_startup_and_warmup": _summarize(
                    [sample["process_rss_peak_bytes_including_startup_and_warmup"] for sample in samples]
                ),
                "median_process_rss_peak_delta_after_warmup_bytes": _summarize(
                    [sample["process_rss_peak_delta_after_warmup_bytes"] for sample in samples]
                ),
            }
        )

    print(
        json.dumps(
            {
                "schema": "jzpack-writer-memory-v1",
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "jzpack": __version__,
                    "zstandard": zstandard.__version__,
                    "samples_per_size": args.samples,
                    "warmup_rows_per_process": args.warmup_rows,
                    "writer_limits": "write_records defaults; same for every size",
                    "input_source": "deterministic synthetic generator; no full input list",
                    "output_sink": "discard sink; no archive retention",
                },
                "results": rows_results,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
