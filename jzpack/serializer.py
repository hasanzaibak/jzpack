from typing import Any

import msgpack
import zstandard as zstd


class BinarySerializer:
    @staticmethod
    def serialize(data: Any) -> bytes:
        return msgpack.packb(data, use_bin_type=True)  # type: ignore

    @staticmethod
    def deserialize(data: bytes) -> Any:
        return msgpack.unpackb(data, raw=False, strict_map_key=False)


class CompressionEngine:
    def __init__(self, level: int = 3):
        self._compressor = zstd.ZstdCompressor(level=level)
        self._decompressor = zstd.ZstdDecompressor()

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def decompress(self, data: bytes) -> bytes:
        return self._decompressor.decompress(data)


class PayloadSerializer:
    MAGIC = b"JZPK"
    VERSION = 1
    HEADER_SIZE = 5

    def __init__(self, compression_level: int = 3):
        self._compression = CompressionEngine(compression_level)

    def serialize(self, payload: dict) -> bytes:
        binary = BinarySerializer.serialize(payload)
        compressed = self._compression.compress(binary)
        return self._prepend_header(compressed)

    def deserialize(self, data: bytes) -> dict:
        self._validate_header(data)
        compressed = data[self.HEADER_SIZE :]
        binary = self._compression.decompress(compressed)
        return BinarySerializer.deserialize(binary)

    def _prepend_header(self, data: bytes) -> bytes:
        return self.MAGIC + bytes([self.VERSION]) + data

    def _validate_header(self, data: bytes) -> None:
        if not data.startswith(self.MAGIC):
            raise ValueError("Invalid file format: missing magic header")

        version = data[len(self.MAGIC)]
        if version != self.VERSION:
            raise ValueError(f"Unsupported version: {version}")
