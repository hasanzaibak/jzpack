from collections.abc import Iterable, Mapping
from typing import Any

from .compressor import JZPackCompressor, StreamingCompressor
from .errors import InvalidFormatError, JZPackError, ResourceLimitError, UnsupportedVersionError

__version__ = "0.3.0"
__all__ = [
    "__version__",
    "compress",
    "decompress",
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
