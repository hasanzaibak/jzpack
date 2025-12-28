from typing import Any

from .compressor import JZPackCompressor, StreamingCompressor

__version__ = "0.2.0"
__all__ = [
    "__version__",
    "compress",
    "decompress",
    "JZPackCompressor",
    "StreamingCompressor",
]


def compress(data: list[dict[str, Any]], level: int = 3, fast: bool = False) -> bytes:
    return JZPackCompressor(compression_level=level, fast=fast).compress(data)


def decompress(data: bytes) -> list[dict[str, Any]]:
    return JZPackCompressor().decompress(data)
