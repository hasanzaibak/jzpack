from typing import Any

from .encoders import DeltaEncoder, DictionaryEncoder, EncodingType, RLEEncoder


class EncodingThresholds:
    RLE_MAX_RUN_RATIO = 0.1
    DELTA_MIN_EFFICIENCY = 0.5
    DICTIONARY_MAX_CARDINALITY = 0.2
    MIN_ROWS = 10
    SAMPLE_SIZE = 50


class ColumnAnalyzer:
    def __init__(self, thresholds: EncodingThresholds | None = None):
        self._thresholds = thresholds or EncodingThresholds()

    def determine_encoding(self, values: list) -> EncodingType:
        if len(values) < self._thresholds.MIN_ROWS:
            return EncodingType.RAW

        first_value = values[0]
        value_type = type(first_value)

        if self._is_rle_suitable(values):
            return EncodingType.RLE

        if value_type in (int, float) and self._is_delta_suitable(values):
            return EncodingType.DELTA

        if value_type is str and self._is_dictionary_suitable(values):
            return EncodingType.DICTIONARY

        return EncodingType.RAW

    def _is_rle_suitable(self, values: list) -> bool:
        n = len(values)
        max_runs = int(n * self._thresholds.RLE_MAX_RUN_RATIO)
        runs = 1
        current = values[0]

        for i in range(1, n):
            if values[i] != current:
                runs += 1
                current = values[i]
                if runs > max_runs:
                    return False

        return True

    def _is_delta_suitable(self, values: list) -> bool:
        n = len(values)
        step = max(1, n // self._thresholds.SAMPLE_SIZE)
        sampled = [values[i] for i in range(0, n, step)]

        if len(sampled) < 2:
            return False

        max_unique = int(len(sampled) * (1 - self._thresholds.DELTA_MIN_EFFICIENCY))
        unique_deltas = set()

        for i in range(1, len(sampled)):
            unique_deltas.add(sampled[i] - sampled[i - 1])
            if len(unique_deltas) > max_unique:
                return False

        return True

    def _is_dictionary_suitable(self, values: list) -> bool:
        max_unique = int(len(values) * self._thresholds.DICTIONARY_MAX_CARDINALITY)
        seen = set()

        for val in values:
            seen.add(val)
            if len(seen) > max_unique:
                return False

        return True


class ColumnEncoder:
    def __init__(self, skip_analysis: bool = False):
        self._analyzer = ColumnAnalyzer()
        self._skip_analysis = skip_analysis

    def encode(self, values: list) -> dict[str, Any]:
        if self._skip_analysis or not values:
            return self._encode_raw(values)

        encoding_type = self._analyzer.determine_encoding(values)
        return self._apply_encoding(values, encoding_type)

    def decode(self, encoded: dict[str, Any]) -> list:
        encoding_type = EncodingType(encoded["t"])
        decoder = self._get_decoder(encoding_type)
        return decoder(encoded)

    def _apply_encoding(self, values: list, encoding_type: EncodingType) -> dict[str, Any]:
        if encoding_type == EncodingType.RLE:
            return {"t": EncodingType.RLE, "d": RLEEncoder.encode(values)}

        if encoding_type == EncodingType.DELTA:
            base, deltas = DeltaEncoder.encode(values)
            return {"t": EncodingType.DELTA, "b": base, "d": deltas}

        if encoding_type == EncodingType.DICTIONARY:
            dictionary, indices = DictionaryEncoder.encode(values)
            return {"t": EncodingType.DICTIONARY, "m": dictionary, "d": indices}

        return self._encode_raw(values)

    def _encode_raw(self, values: list) -> dict[str, Any]:
        return {"t": EncodingType.RAW, "d": values}

    def _get_decoder(self, encoding_type: EncodingType):
        decoders = {
            EncodingType.RAW: lambda e: e["d"],
            EncodingType.RLE: lambda e: RLEEncoder.decode(e["d"]),
            EncodingType.DELTA: lambda e: DeltaEncoder.decode(e["b"], e["d"]),
            EncodingType.DICTIONARY: lambda e: DictionaryEncoder.decode(e["m"], e["d"]),
        }
        return decoders.get(encoding_type, lambda e: e["d"])
