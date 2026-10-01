from collections.abc import Iterable, Mapping
from typing import Any

from .chunks import ChunkError, ChunkRecords, iter_decompress, iter_decompress_recover
from .compressor import JZPackCompressor, StreamingCompressor
from .errors import InvalidFormatError, JZPackError, ResourceLimitError, UnsupportedVersionError
from .writer import write_records

__version__ = "0.4.0"
__all__ = [
    "__version__",
    "compress",
    "decompress",
    "iter_decompress",
    "iter_decompress_recover",
    "write_records",
    "ChunkRecords",
    "ChunkError",
    "JZPackCompressor",
    "StreamingCompressor",
    "JZPackError",
    "InvalidFormatError",
    "UnsupportedVersionError",
    "ResourceLimitError",
]


def compress(
    data: Iterable[Mapping[str, Any]] | Mapping[str, Any], level: int = 3, fast: bool = False
) -> bytes:
    return JZPackCompressor(compression_level=level, fast=fast).compress(data)


def decompress(
    data: bytes, max_output_size: int | None = None, max_records: int | None = None
) -> list[dict[str, Any]]:
    return JZPackCompressor().decompress(data, max_output_size=max_output_size, max_records=max_records)
