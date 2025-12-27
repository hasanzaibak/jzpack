from typing import Any

from .encoders import DeltaEncoder, DictionaryEncoder, EncodingType, RLEEncoder


class ColumnAnalyzer:
    RLE_THRESHOLD = 0.1
    DELTA_EFFICIENCY_THRESHOLD = 0.5
    DICTIONARY_CARDINALITY_THRESHOLD = 0.2
    MIN_ROWS_FOR_ENCODING = 10

    def analyze(self, values: list) -> EncodingType:
        if not values or len(values) < self.MIN_ROWS_FOR_ENCODING:
            return EncodingType.RAW

        if self._should_use_rle(values):
            return EncodingType.RLE

        if self._should_use_delta(values):
            return EncodingType.DELTA

        if self._should_use_dictionary(values):
            return EncodingType.DICTIONARY

        return EncodingType.RAW

    def _should_use_rle(self, values: list) -> bool:
        estimated_runs = RLEEncoder.estimate_size(values)
        compression_ratio = estimated_runs / len(values)
        return compression_ratio < self.RLE_THRESHOLD

    def _should_use_delta(self, values: list) -> bool:
        if not DeltaEncoder.is_applicable(values):
            return False

        efficiency = DeltaEncoder.estimate_efficiency(values)
        return efficiency > self.DELTA_EFFICIENCY_THRESHOLD

    def _should_use_dictionary(self, values: list) -> bool:
        if not values or not isinstance(values[0], str):
            return False

        _, unique_count = DictionaryEncoder.estimate_efficiency(values)
        cardinality_ratio = unique_count / len(values)
        return cardinality_ratio < self.DICTIONARY_CARDINALITY_THRESHOLD


class ColumnEncoder:
    def __init__(self):
        self._analyzer = ColumnAnalyzer()

    def encode_column(self, values: list) -> dict[str, Any]:
        encoding_type = self._analyzer.analyze(values)

        if encoding_type == EncodingType.RLE:
            return self._encode_rle(values)

        if encoding_type == EncodingType.DELTA:
            return self._encode_delta(values)

        if encoding_type == EncodingType.DICTIONARY:
            return self._encode_dictionary(values)

        return {"t": EncodingType.RAW, "d": values}

    def decode_column(self, encoded: dict[str, Any]) -> list:
        encoding_type = EncodingType(encoded["t"])

        if encoding_type == EncodingType.RAW:
            return encoded["d"]

        if encoding_type == EncodingType.RLE:
            return RLEEncoder.decode(encoded["d"])

        if encoding_type == EncodingType.DELTA:
            return DeltaEncoder.decode(encoded["b"], encoded["d"])

        if encoding_type == EncodingType.DICTIONARY:
            return DictionaryEncoder.decode(encoded["m"], encoded["d"])

        return encoded["d"]

    def _encode_rle(self, values: list) -> dict[str, Any]:
        return {"t": EncodingType.RLE, "d": RLEEncoder.encode(values)}

    def _encode_delta(self, values: list) -> dict[str, Any]:
        base, deltas = DeltaEncoder.encode(values)
        return {"t": EncodingType.DELTA, "b": base, "d": deltas}

    def _encode_dictionary(self, values: list) -> dict[str, Any]:
        dictionary, indices = DictionaryEncoder.encode(values)
        return {"t": EncodingType.DICTIONARY, "m": dictionary, "d": indices}
