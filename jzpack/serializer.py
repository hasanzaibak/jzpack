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
                binary = self._decompressor.decompress(data, allow_extra_data=False)
            else:
                binary = self._decompressor.decompress(
                    data, max_output_size=max_output_size, allow_extra_data=False
                )
        except ResourceLimitError:
            raise
        except zstd.ZstdError as exc:
            # python-zstandard 0.21.0+ raises ZstdError when strict one-shot
            # decoding sees bytes after its first complete frame.
            if "unused data" in str(exc).lower():
                raise InvalidFormatError("Invalid compressed payload: trailing frame data")
            raise InvalidFormatError("Invalid or truncated compressed payload") from exc
        return binary


class PayloadSerializer:
    MAGIC = b"JZPK"
    INNER_VERSION = 2
    HEADER_SIZE = 5

    def __init__(self, compression_level: int = 3):
        self._compression = CompressionEngine(compression_level)

    def serialize(self, payload: dict) -> bytes:
        encoded, _ = self.serialize_with_body(payload)
        return encoded

    def serialize_with_body(self, payload: dict) -> tuple[bytes, bytes]:
        """Serialize the v2 payload embedded inside a version 3 chunk."""
        binary = BinarySerializer.serialize(payload)
        compressed = self._compression.compress(binary)
        return self._prepend_header(compressed), binary

    def deserialize(self, data: bytes, max_output_size: int | None = None) -> dict:
        payload, _ = self.deserialize_with_body(data, max_output_size=max_output_size)
        return payload

    def deserialize_with_body(self, data: bytes, max_output_size: int | None = None) -> tuple[dict, bytes]:
        """Deserialize an inner v2 payload and return its MessagePack body as well.

        The body is useful to version 3's chunk reader, whose framing records the
        exact uncompressed MessagePack length.  Keeping this validation here means
        chunks use precisely the same v2 schema and column decoding contract.
        """
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
        except Exception as exc:
            raise InvalidFormatError("Invalid MessagePack payload") from exc

        if not isinstance(payload, dict):
            raise InvalidFormatError("JZPK payload must be a map")
        return payload, binary

    def _prepend_header(self, data: bytes) -> bytes:
        return self.MAGIC + bytes([self.INNER_VERSION]) + data

    def _validate_header(self, data: bytes) -> int:
        if len(data) < self.HEADER_SIZE:
            raise InvalidFormatError("Invalid file format: truncated header")

        if not data.startswith(self.MAGIC):
            raise InvalidFormatError("Invalid file format: missing magic header")

        version = data[len(self.MAGIC)]
        if version != self.INNER_VERSION:
            raise UnsupportedVersionError(f"Unsupported inner version: {version}")
        return version
