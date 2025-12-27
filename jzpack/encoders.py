from enum import IntEnum
from typing import Any


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
        current_val = values[0]
        count = 1

        for val in values[1:]:
            if val == current_val:
                count += 1
            else:
                result.append([current_val, count])
                current_val = val
                count = 1

        result.append([current_val, count])
        return result

    @staticmethod
    def decode(encoded: list) -> list:
        result = []
        for val, count in encoded:
            result.extend([val] * count)
        return result

    @staticmethod
    def estimate_size(values: list) -> int:
        if not values:
            return 0

        runs = 1
        current = values[0]
        for val in values[1:]:
            if val != current:
                runs += 1
                current = val
        return runs * 2


class DeltaEncoder:
    @staticmethod
    def encode(values: list) -> tuple[Any, list]:
        if not values:
            return 0, []

        base = values[0]
        deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
        return base, deltas

    @staticmethod
    def decode(base: Any, deltas: list) -> list:
        result = [base]
        current = base
        for d in deltas:
            current += d
            result.append(current)
        return result

    @staticmethod
    def is_applicable(values: list) -> bool:
        if not values or len(values) < 2:
            return False
        return all(isinstance(v, (int, float)) for v in values)

    @staticmethod
    def estimate_efficiency(values: list) -> float:
        if not values or len(values) < 2:
            return 0.0

        deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
        unique_deltas = len(set(deltas))
        return 1.0 - (unique_deltas / len(deltas))


class DictionaryEncoder:
    @staticmethod
    def encode(values: list) -> tuple[dict, list]:
        dictionary = {}
        next_id = 0
        indices = []

        for val in values:
            if val not in dictionary:
                dictionary[val] = next_id
                next_id += 1
            indices.append(dictionary[val])

        inverse_dict = {v: k for k, v in dictionary.items()}
        return inverse_dict, indices

    @staticmethod
    def decode(dictionary: dict, indices: list) -> list:
        return [dictionary[i] for i in indices]

    @staticmethod
    def estimate_efficiency(values: list) -> tuple[float, int]:
        if not values:
            return 0.0, 0

        unique_count = len(set(values))
        total_count = len(values)
        cardinality_ratio = unique_count / total_count
        return 1.0 - cardinality_ratio, unique_count
