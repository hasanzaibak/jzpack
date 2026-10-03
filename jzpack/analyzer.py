from typing import Any

from .encoders import DeltaEncoder, DictionaryEncoder, EncodingType, RLEEncoder
from .errors import ResourceLimitError

_MAX_CACHED_RLE_RUNS = 32


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
        encoding_type, _ = self._determine_encoding_and_rle_runs(values, cache_rle_runs=False)
        return encoding_type

    def _determine_encoding_and_rle_runs(
        self, values: list, *, cache_rle_runs: bool
    ) -> tuple[EncodingType, list[list[Any]] | None]:
        if len(values) < self._thresholds.MIN_ROWS:
            return EncodingType.RAW, None

        rle_suitable, rle_runs = self._analyze_rle(
            values,
            collect_runs=cache_rle_runs and type(values) is list,
        )
        if rle_suitable:
            return EncodingType.RLE, rle_runs

        if DeltaEncoder.can_encode(values) and self._is_delta_suitable(values):
            return EncodingType.DELTA, None

        if all(type(value) is str for value in values) and self._is_dictionary_suitable(values):
            return EncodingType.DICTIONARY, None

        return EncodingType.RAW, None

    def _is_rle_suitable(self, values: list) -> bool:
        suitable, _ = self._analyze_rle(values, collect_runs=False)
        return suitable

    def _analyze_rle(
        self, values: list, *, collect_runs: bool
    ) -> tuple[bool, list[list[Any]] | None]:
        n = len(values)
        if not values:
            return True, [] if collect_runs else None

        max_runs = int(n * self._thresholds.RLE_MAX_RUN_RATIO)
        runs = 1
        current = values[0]
        count = 1
        if not RLEEncoder.supports_value(current):
            return False, None

        encoded_runs: list[list[Any]] | None = [] if collect_runs else None

        for i in range(1, n):
            value = values[i]
            if not RLEEncoder.supports_value(value):
                return False, None
            if not RLEEncoder._supported_values_equal(value, current):
                previous = current
                previous_count = count
                runs += 1
                current = value
                count = 1
                if runs > max_runs:
                    return False, None
                if encoded_runs is not None:
                    if runs > _MAX_CACHED_RLE_RUNS:
                        encoded_runs = None
                    else:
                        encoded_runs.append([previous, previous_count])
            else:
                count += 1

        if encoded_runs is None:
            return True, None

        encoded_runs.append([current, count])
        return True, encoded_runs

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

        encoding_type, rle_runs = self._analyzer._determine_encoding_and_rle_runs(
            values,
            cache_rle_runs=True,
        )
        return self._apply_encoding(values, encoding_type, rle_runs=rle_runs)

    def decode(self, encoded: dict[str, Any], max_values: int | None = None) -> list:
        if max_values is not None and (
            isinstance(max_values, bool) or not isinstance(max_values, int) or max_values < 0
        ):
            raise ValueError("max_values must be a non-negative integer")
        if not isinstance(encoded, dict):
            raise ValueError("Invalid column payload")
        encoding_value = encoded.get("t")
        if isinstance(encoding_value, bool) or not isinstance(encoding_value, int):
            raise ValueError("Invalid column encoding type")

        try:
            encoding_type = EncodingType(encoding_value)
        except ValueError as exc:
            raise ValueError("Unknown column encoding type") from exc

        expected_fields = {
            EncodingType.RAW: {"t", "d"},
            EncodingType.RLE: {"t", "d"},
            EncodingType.DELTA: {"t", "b", "d"},
            EncodingType.DICTIONARY: {"t", "m", "d"},
        }[encoding_type]
        if encoded.keys() != expected_fields:
            raise ValueError("Invalid column payload")

        decoder = self._get_decoder(encoding_type)
        if encoding_type == EncodingType.RLE:
            return RLEEncoder.decode(encoded["d"], max_output_size=max_values)
        if encoding_type == EncodingType.DELTA:
            return DeltaEncoder.decode(encoded["b"], encoded["d"], max_output_size=max_values)

        values = decoder(encoded)
        if not isinstance(values, list):
            raise ValueError("Invalid column payload")
        if max_values is not None and len(values) > max_values:
            raise ResourceLimitError("Column payload exceeds the maximum output size")
        return values

    def _apply_encoding(
        self,
        values: list,
        encoding_type: EncodingType,
        *,
        rle_runs: list[list[Any]] | None = None,
    ) -> dict[str, Any]:
        if encoding_type == EncodingType.RLE:
            runs = rle_runs if rle_runs is not None else RLEEncoder.encode(values)
            return {"t": EncodingType.RLE, "d": runs}

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
        return decoders[encoding_type]
