from typing import Any

import msgpack
import zstandard as zstd

from .errors import InvalidFormatError, ResourceLimitError, UnsupportedVersionError


class BinarySerializer:
    @staticmethod
    def serialize(data: Any) -> bytes:
        return msgpack.packb(data, use_bin_type=True)  # type: ignore

    @staticmethod
    def deserialize(data: bytes) -> Any:
        return msgpack.unpackb(data, raw=False, strict_map_key=False)


class CompressionEngine:
    def __init__(self, level: int = 3):
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 22:
            raise ValueError("compression level must be an integer from 1 to 22")

        self._compressor = zstd.ZstdCompressor(level=level, write_checksum=True)
        self._decompressor = zstd.ZstdDecompressor()

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def decompress(self, data: bytes, max_output_size: int | None = None) -> bytes:
        try:
            if max_output_size is not None:
                frame_size = zstd.frame_content_size(data)
                if frame_size not in (zstd.CONTENTSIZE_UNKNOWN, zstd.CONTENTSIZE_ERROR) and frame_size > max_output_size:
                    raise ResourceLimitError("JZPK payload exceeds max_output_size")
            if max_output_size is None:
                return self._decompressor.decompress(data)
            return self._decompressor.decompress(data, max_output_size=max_output_size)
        except ResourceLimitError:
            raise
        except zstd.ZstdError as exc:
            raise InvalidFormatError("Invalid or truncated compressed payload") from exc


class PayloadSerializer:
    MAGIC = b"JZPK"
    VERSION = 2
    SUPPORTED_VERSIONS = frozenset({1, 2})
    HEADER_SIZE = 5

    def __init__(self, compression_level: int = 3):
        self._compression = CompressionEngine(compression_level)

    def serialize(self, payload: dict) -> bytes:
        binary = BinarySerializer.serialize(payload)
        compressed = self._compression.compress(binary)
        return self._prepend_header(compressed)

    def deserialize(self, data: bytes, max_output_size: int | None = None) -> dict:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("compressed data must be bytes-like")
        if max_output_size is not None and (
            isinstance(max_output_size, bool) or not isinstance(max_output_size, int) or max_output_size < 0
        ):
            raise ValueError("max_output_size must be a non-negative integer")

        data = bytes(data)
        self._validate_header(data)
        compressed = data[self.HEADER_SIZE :]
        binary = self._compression.decompress(compressed, max_output_size=max_output_size)

        try:
            payload = BinarySerializer.deserialize(binary)
        except (ValueError, TypeError, msgpack.exceptions.ExtraData) as exc:
            raise InvalidFormatError("Invalid MessagePack payload") from exc

        if not isinstance(payload, dict):
            raise InvalidFormatError("JZPK payload must be a map")
        return payload

    def _prepend_header(self, data: bytes) -> bytes:
        return self.MAGIC + bytes([self.VERSION]) + data

    def _validate_header(self, data: bytes) -> int:
        if len(data) < self.HEADER_SIZE:
            raise InvalidFormatError("Invalid file format: truncated header")

        if not data.startswith(self.MAGIC):
            raise InvalidFormatError("Invalid file format: missing magic header")

        version = data[len(self.MAGIC)]
        if version not in self.SUPPORTED_VERSIONS:
            raise UnsupportedVersionError(f"Unsupported version: {version}")
        return version
