from enum import IntEnum
from typing import Any

from .errors import ResourceLimitError


class EncodingType(IntEnum):
    RAW = 0
    RLE = 1
    DELTA = 2
    DICTIONARY = 3


class RLEEncoder:
    @staticmethod
    def encode(values: list) -> list:
        if not values:
            return []

        result = []
        current = values[0]
        count = 1

        for i in range(1, len(values)):
            if values[i] == current:
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
        if not encoded:
            return []

        total = 0
        for item in encoded:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("Invalid RLE payload")
            count = item[1]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("Invalid RLE count")
            total += count

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
    def encode(values: list) -> tuple[Any, list]:
        if not values:
            return 0, []

        base = values[0]
        deltas = [None] * (len(values) - 1)
        prev = base

        for i in range(1, len(values)):
            deltas[i - 1] = values[i] - prev
            prev = values[i]

        return base, deltas

    @staticmethod
    def decode(base: Any, deltas: list) -> list:
        if not isinstance(deltas, list):
            raise ValueError("Invalid delta payload")

        result = [None] * (len(deltas) + 1)
        result[0] = base
        current = base

        for i, delta in enumerate(deltas):
            current += delta
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
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("Invalid dictionary index")
            try:
                result.append(dictionary[index])
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError("Dictionary index out of range") from exc
        return result
