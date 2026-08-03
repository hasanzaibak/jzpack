from __future__ import annotations

import argparse
import time

from jzpack import compress, decompress


def build_records(count: int) -> list[dict[str, object]]:
    statuses = ("active", "inactive", "pending", "error")
    return [
        {
            "id": index,
            "service": "api-gateway",
            "status": statuses[index % len(statuses)],
            "latency_ms": index % 100,
        }
        for index in range(count)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark jzpack round trips")
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    records = build_records(args.records)
    raw_size = sum(len(str(record)) for record in records)
    compressed_sizes = []
    compress_seconds = []
    decompress_seconds = []

    for _ in range(args.iterations):
        started = time.perf_counter()
        compressed = compress(records, level=args.level)
        compress_seconds.append(time.perf_counter() - started)
        compressed_sizes.append(len(compressed))

        started = time.perf_counter()
        restored = decompress(compressed)
        decompress_seconds.append(time.perf_counter() - started)
        assert restored == records

    average_compress = sum(compress_seconds) / len(compress_seconds)
    average_decompress = sum(decompress_seconds) / len(decompress_seconds)
    average_size = sum(compressed_sizes) / len(compressed_sizes)

    print(f"records: {args.records:,}")
    print(f"compressed size: {average_size / 1024 / 1024:.2f} MiB")
    print(f"reference input size: {raw_size / 1024 / 1024:.2f} MiB")
    print(f"compression: {average_compress:.3f}s")
    print(f"decompression: {average_decompress:.3f}s")


if __name__ == "__main__":
    main()
