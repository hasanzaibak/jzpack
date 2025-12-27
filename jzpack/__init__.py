from typing import Any

from .compressor import JZPackCompressor, StreamingCompressorV2

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "compress",
    "decompress",
    "JZPackCompressor",
    "StreamingCompressorV2",
]


def compress(data: list[dict[str, Any]], level: int = 3) -> bytes:
    return JZPackCompressor(level).compress(data)


def decompress(data: bytes) -> list[dict[str, Any]]:
    return JZPackCompressor().decompress(data)
