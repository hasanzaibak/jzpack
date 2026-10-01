import struct
import sys
from enum import IntEnum
from typing import Any

from .errors import ResourceLimitError

MIN_SUPPORTED_INTEGER = -(2**63)
MAX_SUPPORTED_INTEGER = 2**64 - 1


def _is_supported_integer(value: Any) -> bool:
    return type(value) is int and MIN_SUPPORTED_INTEGER <= value <= MAX_SUPPORTED_INTEGER


def _is_supported_number(value: Any) -> bool:
    return _is_supported_integer(value) or type(value) is float


class EncodingType(IntEnum):
    RAW = 0
    RLE = 1
    DELTA = 2
    DICTIONARY = 3


class RLEEncoder:
    @staticmethod
    def supports_value(value: Any) -> bool:
        value_type = type(value)
        if value_type is int:
            return _is_supported_integer(value)
        return value_type in (type(None), bool, float, str, bytes)

    @staticmethod
    def values_equal(left: Any, right: Any) -> bool:
        if type(left) is not type(right) or not RLEEncoder.supports_value(left):
            return False
        if type(left) is float:
            return struct.pack(">d", left) == struct.pack(">d", right)
        return left == right

    @staticmethod
    def encode(values: list) -> list:
        if not values:
            return []

        result = []
        current = values[0]
        count = 1

        for i in range(1, len(values)):
            if RLEEncoder.values_equal(values[i], current):
                count += 1
            else:
                result.append([current, count])
                current = values[i]
                count = 1

        result.append([current, count])
        return result

    @staticmethod
    def decode(encoded: list, max_output_size: int | None = None) -> list:
        if not isinstance(encoded, list):
            raise ValueError("Invalid RLE payload")
        if max_output_size is not None and (
            isinstance(max_output_size, bool) or not isinstance(max_output_size, int) or max_output_size < 0
        ):
            raise ValueError("Invalid RLE output limit")
        if not encoded:
            return []

        total = 0
        for item in encoded:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("Invalid RLE payload")
            count = item[1]
            if not _is_supported_integer(count) or count <= 0:
                raise ValueError("Invalid RLE count")
            total += count
            if total > sys.maxsize:
                raise ValueError("RLE output exceeds the supported list size")

        if max_output_size is not None and total > max_output_size:
            raise ResourceLimitError("RLE payload exceeds the maximum output size")

        result = [None] * total
        idx = 0

        for value, count in encoded:
            for i in range(idx, idx + count):
                result[i] = value
            idx += count

        return result


class DeltaEncoder:
    @staticmethod
    def can_encode(values: list) -> bool:
        if not values or any(not _is_supported_integer(value) for value in values):
            return False
        previous = values[0]
        for value in values[1:]:
            delta = value - previous
            if not _is_supported_integer(delta):
                return False
            previous = value
        return True

    @staticmethod
    def encode(values: list) -> tuple[Any, list]:
        if not values:
            return 0, []
        if not DeltaEncoder.can_encode(values):
            raise ValueError("Delta encoding requires supported integers and deltas")

        base = values[0]
        deltas = [None] * (len(values) - 1)
        prev = base

        for i in range(1, len(values)):
            deltas[i - 1] = values[i] - prev
            prev = values[i]

        return base, deltas

    @staticmethod
    def decode(base: Any, deltas: list, max_output_size: int | None = None) -> list:
        if not isinstance(deltas, list):
            raise ValueError("Invalid delta payload")
        if max_output_size is not None and (
            isinstance(max_output_size, bool) or not isinstance(max_output_size, int) or max_output_size < 0
        ):
            raise ValueError("Invalid delta output limit")
        if max_output_size is not None and len(deltas) + 1 > max_output_size:
            raise ResourceLimitError("Delta payload exceeds the maximum output size")
        if not _is_supported_number(base):
            raise ValueError("Invalid delta base")

        result = [None] * (len(deltas) + 1)
        result[0] = base
        current = base

        for i, delta in enumerate(deltas):
            if not _is_supported_number(delta):
                raise ValueError("Invalid delta value")
            try:
                current += delta
            except (OverflowError, TypeError) as exc:
                raise ValueError("Invalid delta payload") from exc
            if not _is_supported_number(current):
                raise ValueError("Decoded delta value is outside the supported range")
            result[i + 1] = current

        return result


class DictionaryEncoder:
    @staticmethod
    def encode(values: list) -> tuple[list, list]:
        value_to_index = {}
        dictionary = []
        indices = [0] * len(values)

        for i, value in enumerate(values):
            index = value_to_index.get(value)
            if index is None:
                index = len(dictionary)
                value_to_index[value] = index
                dictionary.append(value)
            indices[i] = index

        return dictionary, indices

    @staticmethod
    def decode(dictionary: list | dict, indices: list) -> list:
        if not isinstance(indices, list):
            raise ValueError("Invalid dictionary indices")

        result = []
        for index in indices:
            if not _is_supported_integer(index) or index < 0:
                raise ValueError("Invalid dictionary index")
            try:
                result.append(dictionary[index])
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError("Dictionary index out of range") from exc
        return result
