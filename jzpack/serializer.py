from typing import Any

import msgpack
import zstandard as zstd


class BinarySerializer:
    def serialize(self, data: Any) -> bytes:
        return msgpack.packb(data, use_bin_type=True) # type: ignore

    def deserialize(self, data: bytes) -> Any:
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
    MAGIC_HEADER = b"JZPK"
    VERSION = 1

    def __init__(self, compression_level: int = 3):
        self._binary = BinarySerializer()
        self._compression = CompressionEngine(compression_level)

    def serialize(self, payload: dict) -> bytes:
        binary_data = self._binary.serialize(payload)
        compressed = self._compression.compress(binary_data)
        return self._add_header(compressed)

    def deserialize(self, data: bytes) -> dict:
        compressed = self._strip_header(data)
        binary_data = self._compression.decompress(compressed)
        return self._binary.deserialize(binary_data)

    def _add_header(self, data: bytes) -> bytes:
        return self.MAGIC_HEADER + bytes([self.VERSION]) + data

    def _strip_header(self, data: bytes) -> bytes:
        if not data.startswith(self.MAGIC_HEADER):
            raise ValueError("Invalid file format: missing magic header")

        version = data[4]
        if version != self.VERSION:
            raise ValueError(f"Unsupported version: {version}")

        return data[5:]
